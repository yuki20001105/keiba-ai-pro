"""Bounded hardware verification; no production model/pointer writes or holdout tuning."""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / '.local-runtime/gpu-validation/.venv'
assert Path(sys.executable).resolve().is_relative_to(ENV.resolve()), 'isolated GPU environment required'
os.environ['APPDATA'] = str(ENV / 'appdata')
sys.path[:0] = [str(ROOT / 'python-api'), str(ROOT / 'keiba')]
import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class Log:
    def __init__(self): self.messages = []
    def info(self, message):
        self.messages.append(message)
        if 'GPU' in message or 'kernel' in message: print(message, flush=True)
    def warning(self, message): self.messages.append(message)

def main():
    args = argparse.ArgumentParser()
    args.add_argument('--benchmark', action='store_true')
    args.add_argument('--large', action='store_true', help='Fixed 2016-2020 / 600-round follow-up, two repetitions per device')
    options = args.parse_args()
    logger = Log()
    lgb.register_logger(logger)
    report = {'schema': 'isolated-gpu-validation-v1', 'production_changed': False,
              'python': sys.executable, 'lightgbm_version': lgb.__version__,
              'gpu_dll_sha256': sha(ENV / 'Lib/site-packages/lightgbm/bin/lib_lightgbm.dll'),
              'production_dll_sha256': sha(ROOT / 'python-api/.venv/Lib/site-packages/lightgbm/bin/lib_lightgbm.dll'),
              'nvidia_smi': subprocess.check_output(['nvidia-smi', '--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu', '--format=csv,noheader'], text=True).strip(),
              'limitations': ['OpenCL GPU, not Windows CUDA learner', 'offline kernel disk cache disabled',
                              'not full experiment throughput; no production promotion']}
    rng = np.random.default_rng(42)
    x = rng.normal(size=(2048, 32)); y = 2*x[:,0] + x[:,1] + rng.normal(size=2048)*.1
    started = time.perf_counter()
    model = lgb.train({'objective':'regression','device_type':'gpu','num_threads':2,
                      'max_bin':63,'verbosity':1}, lgb.Dataset(x,label=y), num_boost_round=5)
    report['probe'] = {'seconds':time.perf_counter()-started, 'finite_predictions':bool(np.isfinite(model.predict(x, num_threads=2)).all()),
                       'gpu_learner_confirmed':any('Using GPU Device' in line for line in logger.messages)}
    assert report['probe']['finite_predictions'] and report['probe']['gpu_learner_confirmed']
    del model
    if options.benchmark or options.large:
        import psutil
        if psutil.virtual_memory().available < 5 * 1024**3:
            raise RuntimeError('Insufficient RAM: retain 3 GiB reserve plus bounded benchmark working set')
        from experiments.training import FoldEncoder, _validate_frame
        root = ROOT / 'reports/generated/model-experiments/20260919-feature-search-v1'
        source = root / 'dataset/dataset.parquet'
        spec = json.loads((root / 'selection.json').read_text())['winner_spec']
        manifest = json.loads((root / 'dataset/dataset_manifest.json').read_text())
        columns = spec['feature_columns']
        assert sha(source) == manifest['dataset_sha256'], 'frozen dataset hash mismatch'
        extra = ['race_date','race_id','finish','num_horses','horse_id','horse_number','time_seconds',
                 'distance','surface','venue','odds','popularity','tansho_payout']
        train_start = '20160101' if options.large else '20200101'
        frame = _validate_frame(pd.read_parquet(source, columns=list(dict.fromkeys(columns + extra)),
                                filters=[('race_date','>=',train_start), ('race_date','<=','20211231')]))
        train = frame[frame.race_date < '20210101'].copy()
        valid = frame[frame.race_date >= '20210101'].copy()
        categories = [c for c in manifest['categorical_features'] if c in columns]
        encoder = FoldEncoder(columns, categories)
        x_train = encoder.fit_transform(train); x_valid = encoder.transform(valid)
        y_train = (train.num_horses - train.finish)/(train.num_horses - 1)
        y_valid = (valid.num_horses - valid.finish)/(valid.num_horses - 1)
        params = {**spec['params'], 'objective':'huber','metric':'rmse','num_threads':4,'seed':42,
                  'feature_fraction':.9, 'bagging_fraction':1.0, 'verbosity':1, 'feature_fraction_seed':42,
                  'bagging_seed':42,'data_random_seed':42}
        params.pop('num_boost_round', None)
        rounds = 600 if options.large else 100
        results = []
        schedule = ['cpu','gpu','gpu','cpu'] if options.large else ['cpu','gpu','gpu','cpu','cpu','gpu']
        for iteration, device in enumerate(schedule):
            gc.collect()
            started = time.perf_counter()
            data = lgb.Dataset(x_train, label=y_train, categorical_feature=[c for c in categories if c in x_train])
            model = lgb.train({**params,'device_type':device}, data, num_boost_round=rounds)
            fit_seconds = time.perf_counter()-started
            start_predict = time.perf_counter()
            predicted = model.predict(x_valid, num_threads=4)
            predict_seconds = time.perf_counter()-start_predict
            ranking = pd.DataFrame({'race_id':valid.race_id.to_numpy(),'truth':y_valid.to_numpy(),'prediction':predicted})
            rho = [spearmanr(group.truth, group.prediction).statistic for _,group in ranking.groupby('race_id')]
            row = {'iteration':iteration,'device':device,'fit_seconds':fit_seconds,'predict_seconds':predict_seconds,
                   'rmse_normalized_rank':float(np.sqrt(np.mean((predicted-y_valid)**2))),
                   'race_mean_spearman':float(np.nanmean(rho)), 'finite_predictions':bool(np.isfinite(predicted).all())}
            results.append(row); print(json.dumps(row), flush=True)
            del model, data
        cpu = statistics.median(r['fit_seconds'] for r in results if r['device']=='cpu')
        gpu = statistics.median(r['fit_seconds'] for r in results if r['device']=='gpu')
        report['benchmark'] = {'dataset_sha256':sha(source),'selection_sha256':sha(root/'selection.json'),
                               'train_period':'2016-2020' if options.large else '2020','validation_period':'2021 known development',
                               'train_rows':len(train),'validation_rows':len(valid),'validation_races':valid.race_id.nunique(),
                               'requested_feature_count':len(columns),'used_feature_count':len(x_train.columns),
                               'dropped_columns':encoder.dropped_columns,'params':params,'rounds':rounds,'results':results,
                               'median_cpu_fit_seconds':cpu,'median_gpu_fit_seconds':gpu,'fit_speedup_cpu_over_gpu':cpu/gpu,
                               'scope':f'same complete-runner cohort, encoded features/categories/labels, {rounds}-round pilot; not full multi-year H04 campaign',
                               'validation_used_for_tuning':False,'scores_are_probabilities':False,
                               'timing_excludes':'data loading, encoder fitting, validation evaluation, process startup; prediction timed separately'}
    report['gpu_log_evidence'] = [m for m in logger.messages if 'GPU' in m or 'kernel' in m]
    suffix = '-large' if options.large else '' if options.benchmark else '-probe'
    output = ROOT / f'reports/generated/gpu-validation-20260919{suffix}.json'
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(output, flush=True)

if __name__ == '__main__': main()
