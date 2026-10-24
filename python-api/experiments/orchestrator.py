"""Bounded development experiments, frozen selection, then retrospective audit.

No production registry, raw DB, active model, or network scraping is modified.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from experiments.common import REPO_ROOT, active_pointer_hash, file_sha256, read_json, source_manifest, write_json
from experiments.resources import capability_probe, resource_snapshot

NAMES = {'M01':'速度・基本', 'M02':'速度・履歴', 'M03':'速度・拡張', 'M04':'速度・厳選', 'M05':'順位・比較'}
BASE_PARAMS = {'num_boost_round':200, 'learning_rate':0.05, 'num_leaves':31, 'max_bin':63}
ALTERNATE_PARAMS = {**BASE_PARAMS, 'num_boost_round':300, 'num_leaves':15, 'min_data_in_leaf':80, 'lambda_l2':1.0}
SELECTION_RULE = {
    'development_years':[2023,2024,2025], 'final_year':2026,
    'final_label':'known_reused_retrospective', 'final_is_unseen':False,
    'ablation_maximum':3, 'tuning_maximum':2,
    'ablation_acceptance':'development rank correlation loss <= 0.002 and top1 win rate loss <= 0.002; prefer fewer columns',
    'shortlist':'best mean per-race Spearman; ties within 0.002 resolved by top1 win rate, then annual minimum Spearman',
    'selection_uses_final':False, 'selection_uses_roi':False,
    'promotion':'never automatic', 'final_candidates':'baseline and development-selected winner only',
}


@contextlib.contextmanager
def driver_lock(base: Path, root: Path):
    """Common advisory OS lock across output roots; crash releases the lock."""
    base.mkdir(parents=True, exist_ok=True)
    with (base/'.experiment-driver.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b' '); stream.flush()
        stream.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('another-experiment-driver-is-running') from exc
        try:
            write_json(base/'driver.json', {'pid':os.getpid(), 'root':str(root), 'started_at':time.time()})
            yield
        finally:
            stream.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def run_pilot(root: Path) -> dict:
    """Measure real development data in a short-lived process, never final data."""
    import lightgbm as lgb
    import numpy as np
    import pandas as pd
    import psutil
    from experiments.training import FoldEncoder
    from keiba_ai.speed_deviation import fit_speed_deviation_baseline, apply_speed_deviation_baseline

    started = time.perf_counter()
    process = psutil.Process()
    rss = [process.memory_info().rss / 2**20]
    stopped = threading.Event()
    def sample():
        while not stopped.wait(0.1):
            rss.append(process.memory_info().rss / 2**20)
    monitor = threading.Thread(target=sample, daemon=True); monitor.start()
    try:
        manifest = read_json(root/'dataset/dataset_manifest.json')
        frame = pd.read_parquet(root/'dataset/dataset.parquet')
        development = frame.loc[frame.race_date.between('20160101','20221231')].copy()
        columns = manifest['feature_sets']['M03']
        if development.empty: raise ValueError('pilot-development-empty')
        # Last complete races from the development training partition only.
        dates = sorted(development.race_date.unique())
        chosen_dates, count = [], 0
        for date in reversed(dates):
            chosen_dates.append(date)
            count += int(development.race_date.eq(date).sum())
            if count >= 100000: break
        sample_frame = development.loc[development.race_date.isin(chosen_dates)].reset_index(drop=True)
        encoder = FoldEncoder(columns, manifest['categorical_features'])
        x = encoder.fit_transform(sample_frame)
        baseline = fit_speed_deviation_baseline(sample_frame)
        y = apply_speed_deviation_baseline(sample_frame, baseline)
        prepared_seconds = time.perf_counter() - started
        rounds = 100
        fit_start = time.perf_counter()
        model = lgb.train({'objective':'regression','verbosity':-1,'num_threads':4,
                           'device_type':'cpu','deterministic':True,'force_col_wise':True,
                           'max_bin':63,'num_leaves':31,'learning_rate':0.05,'seed':42},
                          lgb.Dataset(x,label=y,categorical_feature=[c for c in manifest['categorical_features'] if c in encoder.feature_columns]),
                          num_boost_round=rounds)
        fit_seconds = time.perf_counter()-fit_start
        finite = bool(np.isfinite(model.predict(x.iloc[:256],num_threads=4)).all())
        if not finite: raise RuntimeError('pilot-nonfinite-prediction')
        result = {'device':'cpu','num_threads':4,'rounds':rounds,'rows':len(sample_frame),
                  'dataset_sha256':manifest['dataset_sha256'],
                  'features':len(encoder.feature_columns),'training_period':[str(sample_frame.race_date.min()),str(sample_frame.race_date.max())],
                  'final_data_used':False,'preparation_seconds':prepared_seconds,'training_seconds':fit_seconds,
                  'end_to_end_seconds':time.perf_counter()-started,'peak_sampled_rss_mb':max(rss),
                  'dataset_rows':len(frame),'finite_predictions':finite}
        capability=read_json(root/'capability.json')
        result['gpu_real_data_verified']=False
        result['gpu_faster']=False
        if capability['gpu_verified']:
            gpu_start=time.perf_counter()
            try:
                gpu_model=lgb.train({'objective':'regression','verbosity':-1,'num_threads':4,
                                     'device_type':'gpu','max_bin':63,'num_leaves':31,'learning_rate':0.05,'seed':42,
                                     **{k:capability['opencl'][k] for k in ('gpu_platform_id','gpu_device_id')}},
                                    lgb.Dataset(x,label=y,categorical_feature=[c for c in manifest['categorical_features'] if c in encoder.feature_columns]),
                                    num_boost_round=rounds)
                gpu_seconds=time.perf_counter()-gpu_start
                result['gpu_real_data_verified']=bool(np.isfinite(gpu_model.predict(x.iloc[:256],num_threads=4)).all())
                result['gpu_training_seconds']=gpu_seconds
                result['gpu_faster']=result['gpu_real_data_verified'] and gpu_seconds < fit_seconds
            except Exception as exc:
                result['gpu_error']=str(exc)[:1000]
        result['peak_sampled_rss_mb']=max(rss)
        result['end_to_end_seconds']=time.perf_counter()-started
        write_json(root/'pilot.json', result)
        return result
    finally:
        stopped.set(); monitor.join(timeout=2)


def best_result(results: list[dict]) -> dict:
    """Fixed development-only rule. Never look at return rate or final metrics."""
    if not results or any(r['phase'] != 'development' for r in results):
        raise ValueError('selection-only-allows-development-results')
    for result in results:
        metrics = result['evaluation']['overall']
        if metrics.get('rank_correlation') is None or metrics.get('top1_win_rate') is None:
            raise ValueError('selection-metrics-unavailable')
        if not result['evaluation']['by_year'] or any(x.get('rank_correlation') is None for x in result['evaluation']['by_year'].values()):
            raise ValueError('selection-annual-metrics-unavailable')
    highest = max(r['evaluation']['overall']['rank_correlation'] for r in results)
    tied = [r for r in results if r['evaluation']['overall']['rank_correlation'] >= highest-0.002]
    return max(tied, key=lambda r:(r['evaluation']['overall']['top1_win_rate'],
                                  min(x['rank_correlation'] for x in r['evaluation']['by_year'].values()),
                                  r['evaluation']['overall']['rank_correlation']))


def ablation_candidates(manifest: dict) -> list[dict]:
    full = manifest['feature_sets']['M03']
    base = set(manifest['feature_sets']['M01'])
    trials = []
    for group, columns in manifest['feature_groups'].items():
        removed = set(columns)-base
        if not removed: continue
        kept = [c for c in full if c not in removed]
        if not kept or len(kept)==len(full): continue
        trials.append({'id':f'ablate-{group}','candidate_id':'M04','phase':'development',
                       'params':BASE_PARAMS,'feature_columns':kept,'removed_group':group})
        if len(trials)==SELECTION_RULE['ablation_maximum']: break
    return trials


def _metric(value, digits=3, percent=False):
    return '未計測' if value is None else f'{value*(100 if percent else 1):.{digits}f}'


def write_report(root: Path, contract: dict, selection: dict, results: dict, finals: dict) -> dict:
    manifest = read_json(root/'dataset/dataset_manifest.json')
    table = []
    for candidate in NAMES:
        result = results[selection['candidate_trials'][candidate]]
        table.append({'candidate_id':candidate,'name':NAMES[candidate],'trial_id':result['trial_id'],
                      'features':result['folds'][-1]['feature_count'],
                      'metrics':result['evaluation']['overall'],
                      'evaluation':result['evaluation'],
                      'seconds':result['resource_usage']['wall_seconds']})
    summary = {'experiment_id':root.name,'status':'completed','dataset':{
        k:manifest[k] for k in ('row_count','race_count','date_from','date_to','dataset_sha256','point_in_time_audit')},
        'selection':selection,'development_comparison':table,
        'final_comparison':{key:val['evaluation'] for key,val in finals.items()},
        'contract':contract,'active_model_unchanged':active_pointer_hash()==contract['active_pointer_sha256'],
        'promoted':False,'report_path':str(root/'comparison.md')}
    from experiments.cli import store_for
    trials=store_for(root).snapshot(root.name)['trials']
    started=[row['started_at'] for row in trials if row.get('started_at') is not None]
    ended=[row['finished_at'] for row in trials if row.get('finished_at') is not None]
    # Keep elapsed parallel time separate from cumulative per-worker time.
    summary['timing']={'parallel_trials_elapsed_seconds':max(ended)-min(started) if started and ended else None,
                       'sum_worker_seconds':sum(row['result']['resource_usage']['wall_seconds'] for row in trials if row.get('result')),
                       'trial_count':len(trials),'includes_snapshot_and_dataset_preparation':False,
                       'measured_serial_speedup':None}
    lines = ['# 安全な学習データ・並列モデル比較', '',
             '## データ', '',
             f"中央競馬・平地 {manifest['race_count']:,}レース / {manifest['row_count']:,}頭。期間 {manifest['date_from']}–{manifest['date_to']}。",
             '未来・同日の結果情報を入力しない履歴をDBから再生成。オッズ・人気は入力せず、評価にのみ使用。',
             '全出走馬の結果・タイムが揃ったレースのみの比較です。欠損レースを含む実運用全体の成績ではありません。', '',
             '## 開発期間（2023–2025、年ごとの時系列検証）', '',
             '| モデル | 特徴量 | 順位相関 | RMSE | 1位勝率 | 人気1位との差 | 回収率 | 処理時間 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in table:
        m=row['metrics']
        lines.append(f"| {row['name']} | {row['features']} | {_metric(m['rank_correlation'])} | {_metric(m['rmse'])} | {_metric(m['top1_win_rate'],1,True)}% | {_metric(m['top1_vs_popularity_pp'],1)}pt | {_metric(m['win_return_rate_percent'],1)} | {row['seconds']:.0f}秒 |")
    lines += ['', '回収率は各レースの予測1位に単勝100円を固定したシミュレーション。100%が収支均衡です。払戻が不明なレースがあれば全体回収率は未計測とします。',
              '人気は記録済み最終人気による参考比較で、予測時点の市場を再現した比較ではありません。', '',
              f"開発期間で選んだ候補: **{NAMES[selection['winner_candidate']]}**（{selection['winner_trial']}）。最終期間の成績は選定に使用していません。", '',
              ('厳選版は特徴量削減を採用しました: '+', '.join(selection['M04_removed_groups']) if selection['M04_removed_groups'] else '厳選試験では削減による非劣化を確認できず、M04はM03と同じ特徴量を維持しました。'), '',
              '## 2026年の事後比較', '',
              'この期間は過去モデルの学習等で既に参照されています。未知の最終テストとは呼べません。', '',
              '| モデル | 順位相関 | RMSE | 1位勝率 | 人気1位との差 | 回収率 | レース数 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for key,result in finals.items():
        m=result['evaluation']['overall']
        lines.append(f"| {NAMES[result['candidate_id']]} ({key}) | {_metric(m['rank_correlation'])} | {_metric(m['rmse'])} | {_metric(m['top1_win_rate'],1,True)}% | {_metric(m['top1_vs_popularity_pp'],1)}pt | {_metric(m['win_return_rate_percent'],1)} | {m['race_count']:,} |")
    for key,result in finals.items():
        m=result['evaluation']['overall']
        if m['payout_missing_races']:
            lines += ['', f"{key}: 払戻未確認 {m['payout_missing_races']}レースのため全体回収率は未計測。確認済み {m['payout_verified_races']:,}レースだけの参考回収率は {_metric(m['verified_subset_win_return_rate_percent'],1)}%。"]
    lines += ['', '年別・芝ダート別成績、MAE・R²、日単位ブートストラップ95%区間、払戻の検証件数は comparison.json と各試行の result.json に保存。', '',
              '## 実行環境と保管', '',
              f"同時実行枠: CPU {contract['capacity']['cpu']} / GPU {contract['capacity']['gpu']}。1 CPUモデル4スレッド。",
              f"実データ計測: {contract['pilot']['rows']:,}行・100round、学習 {contract['pilot']['training_seconds']:.1f}秒 / 準備を含む {contract['pilot']['end_to_end_seconds']:.1f}秒。",
              '現在のLightGBMビルドはGPU機能を含まないため、今回はCPUで実行。稼働Python環境を変更していません。' if not contract['capability']['gpu_verified'] else 'GPUは能力確認を通過した実行枠にのみ割り当て。',
              '候補成果物はこの実験フォルダ内のみ。使用中モデル・元DBは変更していません。既存の予測機能への採用には別途推論互換性の確認が必要です。', '',
              '特徴量寄与は学習データの小標本TreeSHAP（training_contributions.json）。重要度だけで選別せず、特徴量グループを削除した開発期間の成績で厳選しています。',
              '回帰モデルの寄与は速度スコアへの寄与です。勝率ではありません。', '']
    timing=summary['timing']
    if timing['parallel_trials_elapsed_seconds'] is not None:
        lines += [f"全{timing['trial_count']}試行の経過時間: {timing['parallel_trials_elapsed_seconds']:.0f}秒。各ワーカー処理時間の合計: {timing['sum_worker_seconds']:.0f}秒。準備処理は含みません。逐次実行との同条件比較はしておらず、この比を高速化率とは呼びません。", '']
    write_json(root/'comparison.json', summary)
    (root/'comparison.md').write_text('\n'.join(lines), encoding='utf-8')
    return {'status':'completed','report':str(root/'comparison.md'),'selected':selection['winner_candidate'],
            'active_model_unchanged':summary['active_model_unchanged']}


def run_experiment(root: Path) -> dict:
    from experiments.cli import batch, store_for, verify_contract
    from experiments.runtime import ExperimentError
    common_root=REPO_ROOT/'reports/generated/model-experiments'
    with driver_lock(common_root, root):
        # After a driver crash its children may still hold leases. A second
        # output root cannot bypass that ledger just because the OS lock freed.
        import sqlite3
        for ledger in common_root.rglob('experiment.sqlite3'):
            if ledger.resolve()==(root/'experiment.sqlite3').resolve(): continue
            with sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True) as other:
                if other.execute("SELECT 1 FROM experiments WHERE status IN ('active','cancelling','blocked') LIMIT 1").fetchone():
                    raise RuntimeError(f'another-experiment-requires-attention: {ledger.parent.name}')
        manifest = read_json(root/'dataset/dataset_manifest.json')
        snapshot = read_json(root/'snapshot.json')
        generated_by=manifest.get('source_contract',{})
        for filename, key in (('dataset.py','dataset_module_sha256'),('features.py','features_module_sha256')):
            if generated_by.get(key) != file_sha256(Path(__file__).with_name(filename)):
                raise RuntimeError('dataset-generated-with-different-source; prepare a fresh dataset')
        if manifest['snapshot_sha256'] != snapshot['sha256']:
            raise RuntimeError('dataset-snapshot-contract-mismatch')
        if snapshot['active_pointer_sha256'] != active_pointer_hash():
            raise RuntimeError('active-model-changed-since-snapshot')
        store=store_for(root)
        contract_file=root/'contract.json'
        if contract_file.exists():
            contract=read_json(contract_file)
            verify_contract(root,contract)
        else:
            capability=capability_probe(); write_json(root/'capability.json',capability)
            if not (root/'pilot.json').exists() or read_json(root/'pilot.json').get('dataset_sha256')!=manifest['dataset_sha256']:
                env=dict(os.environ,PYTHONUTF8='1',OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
                         PYTHONPATH=os.pathsep.join([str(REPO_ROOT/'python-api'),str(REPO_ROOT/'keiba')]))
                with (root/'pilot.log').open('wb') as log:
                    subprocess.run([sys.executable, str(Path(__file__).resolve()),'--pilot',str(root)],
                                   env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            pilot=read_json(root/'pilot.json')
            resources=resource_snapshot()
            worker_mb=max(2048, math.ceil(pilot['peak_sampled_rss_mb']*2.5))
            capacity=2 if resources['available_ram_mb'] >= 3072+2*worker_mb else 1
            if resources['available_ram_mb'] < 3072+worker_mb:
                raise RuntimeError('insufficient-free-memory-for-safe-model-worker')
            use_gpu=pilot.get('gpu_faster',False) and resources['available_ram_mb'] >= 3072+2*worker_mb
            contract={'schema':'local-safe-parallel-comparison-v1','source_manifest':source_manifest(),
                      'snapshot_sha256':snapshot['sha256'],'dataset_sha256':manifest['dataset_sha256'],
                      'dataset_manifest_sha256':file_sha256(root/'dataset/dataset_manifest.json'),
                      'active_pointer_sha256':active_pointer_hash(),'capacity':{'cpu':1 if use_gpu else capacity,
                                                                            'gpu':1 if use_gpu else 0},
                      'reserve_ram_mb':3072,'estimated_worker_memory_mb':worker_mb,
                      'gpu_opencl':capability['opencl'],'capability':capability,'pilot':pilot,
                      'selection_rule':SELECTION_RULE,'base_parameters':BASE_PARAMS,'alternate_parameters':ALTERNATE_PARAMS,
                      'real_world_all_races':False,'feature_input_policy':'strictly-prior-observed-JRA-flat-history; no market',
                      'no_promotion':True}
            write_json(contract_file,contract)
        try:
            existing=store.snapshot(root.name)
        except ExperimentError as exc:
            if str(exc)!='Experiment does not exist': raise
            store.create_experiment(root.name,contract)
        else:
            if existing['contract']!=contract:
                raise RuntimeError('existing-experiment-contract-mismatch')
            if existing['status']=='completed':
                return {'status':'completed','report':str(root/'comparison.md'),'already_completed':True}
            if existing['status']!='active':
                raise RuntimeError('experiment-requires-investigation-before-resume')
        print(json.dumps({'stage':'contract-locked','capacity':contract['capacity'],'pilot':contract['pilot']},ensure_ascii=False),flush=True)
        initial=[{'id':f'{candidate}-dev','candidate_id':candidate,'phase':'development','params':BASE_PARAMS}
                 for candidate in ('M01','M02','M03','M05')]
        results=batch(root,initial)
        ablations=ablation_candidates(manifest)
        if ablations: results.update(batch(root,ablations))
        reference=results['M03-dev']['evaluation']['overall']
        acceptable=[p for p in ablations if
                    results[p['id']]['evaluation']['overall']['rank_correlation'] >= reference['rank_correlation']-0.002 and
                    results[p['id']]['evaluation']['overall']['top1_win_rate'] >= reference['top1_win_rate']-0.002]
        selected_features=(min(acceptable,key=lambda p:len(p['feature_columns']))['feature_columns']
                           if acceptable else manifest['feature_sets']['M03'])
        compact={'id':'M04-dev','candidate_id':'M04','phase':'development','params':BASE_PARAMS,'feature_columns':selected_features}
        results.update(batch(root,[compact]))
        primary=[results[f'{c}-dev'] for c in NAMES]
        winner=best_result(primary)
        runnerup=best_result([r for r in primary if r['candidate_id']!=winner['candidate_id']])
        alternatives=[]
        for source in (winner,runnerup):
            candidate=source['candidate_id']
            trial={'id':f'{candidate}-tuned','candidate_id':candidate,'phase':'development','params':ALTERNATE_PARAMS}
            if candidate=='M04': trial['feature_columns']=selected_features
            alternatives.append(trial)
        results.update(batch(root,alternatives))
        candidate_trials={c:best_result([r for r in results.values() if r['candidate_id']==c and not r['trial_id'].startswith('ablate-')])['trial_id'] for c in NAMES}
        winner=best_result([results[t] for t in candidate_trials.values()])
        selection={'winner_trial':winner['trial_id'],'winner_candidate':winner['candidate_id'],
                   'candidate_trials':candidate_trials,'M04_features':selected_features,'M04_removed_groups':[p['removed_group'] for p in acceptable if p['feature_columns']==selected_features],
                   'rule':SELECTION_RULE,'frozen_before_final':True,'development_result_hashes':{
                       k:file_sha256(root/'trials'/k/'result.json') for k in results}}
        selection_file=root/'selection.json'
        if selection_file.exists() and read_json(selection_file)!=selection:
            raise RuntimeError('selection-changed-after-freeze')
        write_json(selection_file,selection)
        final_payloads=[]
        by_id={p['id']:p for p in initial+[compact]+alternatives}
        for trial_id in dict.fromkeys(['M01-dev',winner['trial_id']]):
            payload={**by_id[trial_id],'id':trial_id.replace('-dev','')+'-final','phase':'final'}
            final_payloads.append(payload)
        finals=batch(root,final_payloads)
        verify_contract(root,contract)
        summary=write_report(root,contract,selection,results,finals)
        store.finish_experiment(root.name)
        return summary


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--pilot': raise SystemExit('use experiment CLI')
    # Standalone child process has no repository PYTHONPATH assumptions.
    print(json.dumps(run_pilot(Path(sys.argv[2])),ensure_ascii=False))
