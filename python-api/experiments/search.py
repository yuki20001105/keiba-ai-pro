"""Finite, resumable feature/condition search followed by a separate betting audit."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from experiments.common import REPO_ROOT, active_pointer_hash, file_sha256, read_json, source_manifest, write_json
from experiments.cli import batch, store_for, verify_contract, worker
from experiments.orchestrator import driver_lock
from experiments.resources import capability_probe, resource_snapshot
from experiments.runtime import ExperimentError
from experiments.search_design import (PLAN, initial_candidates, best_accuracy, individual_candidates,
    feature_effects, combination_candidates, condition_candidates, full_spec, fingerprint)

BASE = REPO_ROOT/'reports/generated/model-experiments'
WORKER = REPO_ROOT/'scripts/run_model_search.py'


def freeze_json(path: Path, value: dict) -> None:
    if path.exists():
        if read_json(path) != value:
            raise RuntimeError(f'frozen-search-artifact-changed: {path.name}')
    else:
        temporary=path.with_name(f'.freeze-{uuid.uuid4().hex}.json')
        write_json(temporary,value)
        try:
            try: os.link(temporary,path)
            except FileExistsError:
                if read_json(path)!=value: raise RuntimeError(f'frozen-search-artifact-changed: {path.name}')
        finally:
            temporary.unlink(missing_ok=True)


def prepare(root: Path, parent: Path) -> dict:
    from experiments.search_features import build_search_dataset
    if root == parent or root.is_relative_to(parent) or parent.is_relative_to(root) or not parent.is_dir():
        raise ValueError('search requires a distinct completed parent experiment')
    if store_for(parent).snapshot(parent.name)['status'] != 'completed':
        raise ValueError('parent experiment must be completed')
    root.mkdir(parents=True,exist_ok=True)
    source = parent/'dataset'
    original = read_json(source/'dataset_manifest.json')
    if file_sha256(source/'dataset.parquet') != original['dataset_sha256']:
        raise RuntimeError('parent-dataset-changed')
    lineage={'parent_root':str(parent),'parent_manifest_sha256':file_sha256(source/'dataset_manifest.json'),
             'parent_dataset_sha256':original['dataset_sha256'],'snapshot':read_json(parent/'snapshot.json'),
             'source_data_untouched':True,'created_active_pointer_sha256':active_pointer_hash()}
    freeze_json(root/'parent.json',lineage)
    target=root/'dataset'
    if (target/'dataset_manifest.json').exists():
        manifest=read_json(target/'dataset_manifest.json')
        if (file_sha256(target/'dataset.parquet') != manifest['dataset_sha256'] or
                manifest['search_feature_contract']['source_dataset_sha256'] != original['dataset_sha256']):
            raise RuntimeError('existing-search-dataset-changed')
    else:
        manifest=build_search_dataset(source,target)
    freeze_json(root/'search-plan.json',PLAN)
    return {'rows':manifest['row_count'],'races':manifest['race_count'],
            'features':len(manifest['feature_allowlist']),'dataset_sha256':manifest['dataset_sha256']}


def _initialize(root: Path) -> tuple[dict,dict]:
    manifest=read_json(root/'dataset/dataset_manifest.json')
    lineage=read_json(root/'parent.json')
    parent=Path(lineage['parent_root'])
    if (file_sha256(parent/'dataset/dataset.parquet') != lineage['parent_dataset_sha256'] or
            file_sha256(parent/'dataset/dataset_manifest.json') != lineage['parent_manifest_sha256']):
        raise RuntimeError('parent-lineage-changed')
    if lineage['created_active_pointer_sha256'] != active_pointer_hash():
        raise RuntimeError('active-model-changed-since-search-preparation')
    for filename,key in [('dataset.py','dataset_module_sha256'),('features.py','features_module_sha256')]:
        if manifest['source_contract'][key] != file_sha256(Path(__file__).with_name(filename)):
            raise RuntimeError('parent-feature-source-changed')
    if manifest['search_feature_contract']['search_features_module_sha256'] != file_sha256(Path(__file__).with_name('search_features.py')):
        raise RuntimeError('search-feature-source-changed')
    freeze_json(root/'search-plan.json',PLAN)
    if (root/'contract.json').exists():
        contract=read_json(root/'contract.json')
        if contract['plan'] != PLAN: raise RuntimeError('search-plan-changed')
        verify_contract(root,contract)
    else:
        capability=capability_probe()
        # This machine's installed build lacks a GPU learner. Do not silently
        # install/replace it, nor count a GPU as used without real validation.
        pilot=read_json(parent/'pilot.json')
        resources=resource_snapshot()
        estimated=max(2800,round(pilot['peak_sampled_rss_mb']*3))
        if resources['available_ram_mb'] < 3072+estimated:
            raise RuntimeError('insufficient-free-memory-for-search')
        capacity=2 if resources['available_ram_mb'] >= 3072+2*estimated else 1
        contract={'schema':'local-bounded-feature-search-v1','plan':PLAN,'source_manifest':source_manifest(),
                  'dataset_sha256':manifest['dataset_sha256'],
                  'dataset_manifest_sha256':file_sha256(root/'dataset/dataset_manifest.json'),
                  'active_pointer_sha256':active_pointer_hash(),'capacity':{'cpu':capacity,'gpu':0},
                  'reserve_ram_mb':3072,'estimated_worker_memory_mb':estimated,'capability':capability,
                  'gpu_reason':'CPU two-worker search; GPU build unavailable or not measured faster for this search',
                  'parent_pilot':pilot,'parent':lineage,'no_promotion':True,'no_live_bets':True}
        freeze_json(root/'contract.json',contract)
        for name,digest in contract['source_manifest'].items():
            source=REPO_ROOT/name; destination=root/'code-snapshot'/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            if destination.exists() and file_sha256(destination)!=digest:
                raise RuntimeError('archived-source-mismatch')
            if not destination.exists(): shutil.copyfile(source,destination)
    store=store_for(root)
    try: state=store.snapshot(root.name)
    except ExperimentError as exc:
        if str(exc)!='Experiment does not exist': raise
        store.create_experiment(root.name,contract)
    else:
        if state['contract']!=contract: raise RuntimeError('search-ledger-contract-mismatch')
        if state['status'] not in ('active','completed'):
            raise RuntimeError('search-needs-investigation-before-resume')
    return manifest,contract


class Campaign:
    def __init__(self,root: Path):
        self.root=root
        self.results={}
        self.specs={}

    def execute(self,name: str,payloads: list[dict]) -> dict:
        identifiers=[p['id'] for p in payloads]
        if len(set(identifiers))!=len(identifiers): raise ValueError('duplicate-stage-trial-id')
        for payload in payloads:
            if payload['id'] in self.specs and self.specs[payload['id']]!=payload:
                raise ValueError('trial-id-reused-with-different-specification')
        ledger_rows=store_for(self.root).snapshot(self.root.name)['trials']
        requested={p['id']:p for p in payloads}
        for row in ledger_rows:
            if row['trial_id'] not in requested: continue
            normalized={k:v for k,v in row['payload'].items()
                        if k not in ('device','num_threads','experiment_contract_sha256')}
            if normalized!=requested[row['trial_id']]:
                raise RuntimeError('resume-ledger-payload-mismatch')
            if row['status']=='completed':
                result_path=self.root/'trials'/row['trial_id']/'result.json'
                if read_json(result_path)!=row['result']:
                    raise RuntimeError('resume-result-ledger-mismatch')
                artifact_root=result_path.parent
                for relative,digest in read_json(artifact_root/'artifact_manifest.json').items():
                    artifact=(artifact_root/relative).resolve()
                    if not artifact.is_relative_to(artifact_root.resolve()) or file_sha256(artifact)!=digest:
                        raise RuntimeError('resume-artifact-hash-mismatch')
        existing={r['trial_id'] for r in ledger_rows}
        if len(existing|set(identifiers))>PLAN['max_model_trials']:
            raise RuntimeError('predeclared-model-trial-cap-exceeded')
        freeze_json(self.root/'stages'/f'{name}.json',{'name':name,'payloads':payloads,
                    'selection_period':[2021,2022],'confirmation_used_for_selection':False})
        self.specs.update({p['id']:p for p in payloads})
        if not payloads: return {}
        print(json.dumps({'stage':name,'trials':len(payloads)},ensure_ascii=False),flush=True)
        observed=batch(self.root,payloads,worker_script=WORKER)
        self.results.update(observed)
        return observed

    def candidates(self,target=None):
        return [r for key,r in self.results.items()
                if r['validation_years']==[2021,2022] and self.specs[key].get('bootstrap_samples')==0
                and self.specs[key].get('seed',42)==42
                and (target is None or r['target']==target)]

    def best(self,target=None): return best_accuracy(self.candidates(target))

    def decision(self,name: str,extra=None):
        selected=self.best(); regression=self.best('regression')
        value={'stage':name,'best':selected['trial_id'],'regression':regression['trial_id'],
               'rank_correlation':selected['evaluation']['overall']['rank_correlation'],
               'regression_metrics':regression['evaluation']['overall'],
               'trials_so_far':len(self.results),'selection_uses_only':[2021,2022],
               'confirmation_and_roi_excluded':True,**(extra or {})}
        freeze_json(self.root/'decisions'/f'{name}.json',value)
        print(json.dumps(value,ensure_ascii=False),flush=True)


def _frozen_predictions(root,results,identifiers):
    import pandas as pd
    parts=[]; provenance=[]
    for identifier in identifiers:
        result=results[identifier]
        artifact=result['artifacts']['predictions']; path=Path(artifact['path'])
        if file_sha256(path)!=artifact['sha256']: raise RuntimeError('prediction-artifact-changed')
        frame=pd.read_parquet(path)
        end_dates={fold['year']:fold['contract']['train_period'][1] for fold in result['folds']}
        frame['train_end_date']=frame.fold_year.map(end_dates)
        if not frame.train_end_date.lt(frame.race_date).all(): raise RuntimeError('prediction-not-out-of-time')
        frame['fold_train_date_to']=frame['train_end_date']
        parts.append(frame)
        provenance.append({'trial_id':identifier,'artifact':artifact,'folds':[
            {'year':fold['year'],'train_period':fold['contract']['train_period'],'model_sha256':fold['sha256']}
            for fold in result['folds']]})
    combined=pd.concat(parts,ignore_index=True)
    if combined.duplicated(['race_id','horse_number']).any(): raise RuntimeError('overlapping-prediction-cohorts')
    return combined,provenance


def _fmt(value,digits=4): return '—' if value is None else f'{value:.{digits}f}'


def write_report(root,contract,campaign,selection,betting,elapsed):
    rows=[]
    for identifier,result in campaign.results.items():
        metric=result['evaluation']['overall']
        rows.append({'trial_id':identifier,'target':result['target'],'loss':result['loss'],
                     'years':result['validation_years'],'features':len(result['feature_columns']),
                     'rank_correlation':metric['rank_correlation'],'rmse_speed_deviation':metric.get('rmse'),
                     'rmse_speed_mps':metric.get('rmse_speed_mps'),
                     'screening':result['artifact_policy']['screening_primary_only'],
                     'seconds':result['resource_usage']['wall_seconds']})
    result={'status':'completed','trial_count':len(rows),'wall_seconds':elapsed,'selection':selection,
            'trials':rows,'betting_report':str(root/'betting/betting_search_report.json'),
            'global_optimum_proven':False,'unseen_validation':False,
            'active_pointer_unchanged':active_pointer_hash()==contract['active_pointer_sha256'],
            'no_actual_bets':True,'prediction_model_selected_without_roi':True}
    write_json(root/'summary.json',result)
    write_json(root/'trial-comparison.json',rows)
    lines=['# 特徴量・学習条件の探索結果','',
           f"完了試行: {len(rows)}。順位相関を主目的とし、回収率はモデル選定に使用していません。",'',
           f"選定: `{selection['winner_trial']}` / {len(selection['winner_spec']['feature_columns'])}特徴量。",'',
           '## 同条件の比較','',
           '|モデル|評価年|順位相関|速度RMSE（m/s）|特徴量数|','|---|---|---:|---:|---:|']
    for row in rows:
        if not row['screening']:
            lines.append(f"|{row['trial_id']}|{','.join(map(str,row['years']))}|{_fmt(row['rank_correlation'])}|{_fmt(row['rmse_speed_mps'])}|{row['features']}|")
    lines += ['', '## 検証の範囲','',
        '- 2021–2022年だけで特徴量・条件を探索。各年は前年までで学習。',
        '- 選定を固定して2023–2025年、2026年を確認。これらは過去実験で既知のため、未知データ検証ではありません。',
        '- 順位モデルに速度RMSEを付けず、速度回帰モデルを別に比較。',
        '- 全組み合わせの総当たりではありません。最大200試行の有限探索であり、数学的な最適性や将来の収益を保証しません。',
        '- 払戻を確認できない購入の総回収率は未確定。確認済み部分だけの値と区別します。',
        '- オッズの観測時刻が不明なため、買い目評価は最終市場情報による参考シミュレーションです。',
        '- 対象は整合性検査を通った中央平地の完全なレース。全レースへの一般化は別検証が必要です。',
        '- 使用中モデルの変更、実購入は行っていません。','',
        '## 詳細','',
        '- `feature-effects.json`: 各特徴量を除去した再学習の効果（正値は除去で改善）',
        '- `decisions/`: 各改善サイクルの選定理由と結果',
        '- `selection.json`: 確認期間を見る前に固定したモデル仕様',
        '- `trials/`: 年別モデル・予測・指標・ハッシュ',
        '- `betting/`: モデル固定後の買い目ルール探索と確認','',
        '方法参考: [LightGBM](https://github.com/lightgbm-org/LightGBM/blob/main/docs/Parameters.rst)、'
        '[相関した特徴量の重要度](https://scikit-learn.org/stable/auto_examples/inspection/plot_permutation_importance_multicollinear.html)。']
    (root/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result


def run(root: Path) -> dict:
    started=time.perf_counter()
    with driver_lock(BASE,root):
        for ledger in BASE.rglob('experiment.sqlite3'):
            if ledger.resolve()==(root/'experiment.sqlite3').resolve(): continue
            with sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True) as connection:
                if connection.execute("SELECT 1 FROM experiments WHERE status IN ('active','cancelling','blocked') LIMIT 1").fetchone():
                    raise RuntimeError(f'another-experiment-requires-attention: {ledger.parent.name}')
        manifest,contract=_initialize(root)
        if store_for(root).snapshot(root.name)['status']=='completed': return read_json(root/'summary.json')
        campaign=Campaign(root)
        initial=campaign.execute('01-initial',initial_candidates(manifest))
        all_count=len(manifest['feature_allowlist'])
        expanded=best_accuracy([r for r in initial.values() if len(r['feature_columns'])==all_count and r['target']!='regression'])
        base=campaign.specs[expanded['trial_id']]
        drops=individual_candidates(base)
        campaign.execute('02-every-feature-removal',drops)
        effects=feature_effects(expanded,drops,campaign.results)
        freeze_json(root/'feature-effects.json',{'reference':expanded['trial_id'],'effects':effects,'years':[2021,2022]})
        campaign.decision('cycle1-individual',{'full_reference':expanded['trial_id'],
            'issue':'single-feature effects may substitute correlated predictors; test combinations next'})
        campaign.execute('03-combinations',combination_candidates(base,effects,prefix='C',maximum=24))
        campaign.execute('04-conditions',condition_candidates(campaign.specs[campaign.best()['trial_id']],
                           campaign.specs[campaign.best('regression')['trial_id']]))
        campaign.decision('cycle2-conditions',{'issue':'recheck feature effects after changing loss/regularization/window'})
        current=campaign.best(); current_spec=campaign.specs[current['trial_id']]
        refinements=[name['feature'] for name in effects if name['feature'] in current_spec['feature_columns']][:16]
        local=[{**current_spec,'id':f'R{i:02d}','feature_columns':[c for c in current_spec['feature_columns'] if c!=name],
                'removed_features':[name]} for i,name in enumerate(refinements,1)]
        campaign.execute('05-local-refinement',local)
        local_effects=feature_effects(current,local,campaign.results)
        freeze_json(root/'local-feature-effects.json',{'reference':current['trial_id'],'effects':local_effects})
        campaign.execute('06-local-combinations',combination_candidates(current_spec,local_effects,prefix='RC',maximum=12))
        campaign.decision('cycle3-final',{'issue':'bounded search stops here; confirmation results cannot trigger reselection'})
        winner=campaign.best(); regression=campaign.best('regression')
        chosen=campaign.specs[winner['trial_id']]
        seeds=[{**chosen,'id':f'seed-{seed}','seed':seed} for seed in (7,2026)]
        campaign.execute('07-seed-robustness',seeds)
        regression_results=campaign.candidates('regression')
        pareto=[r['trial_id'] for r in regression_results if not any(
            other['evaluation']['overall']['rank_correlation']>=r['evaluation']['overall']['rank_correlation'] and
            other['evaluation']['overall']['rmse_speed_mps']<=r['evaluation']['overall']['rmse_speed_mps'] and
            (other['evaluation']['overall']['rank_correlation']>r['evaluation']['overall']['rank_correlation'] or
             other['evaluation']['overall']['rmse_speed_mps']<r['evaluation']['overall']['rmse_speed_mps']) for other in regression_results)]
        selection={'winner_trial':winner['trial_id'],'winner_spec':chosen,'regression_trial':regression['trial_id'],
                   'regression_pareto':pareto,'rule':PLAN,'frozen_before_confirmation':True,
                   'seed_results_not_used_to_cherry_pick_seed':True,'default_seed':42,
                   'result_hashes':{key:file_sha256(root/'trials'/key/'result.json') for key in campaign.results}}
        freeze_json(root/'selection.json',selection)
        finalists={'baseline':campaign.specs['B-rank-72'],'winner':chosen,
                   'regression':campaign.specs[regression['trial_id']]}
        full=[]
        for label,spec in finalists.items():
            for band,years in [('search',[2021,2022]),('confirmation',[2023,2024,2025]),('retrospective',[2026])]:
                full.append(full_spec(spec,f'{label}-{band}',years))
        campaign.execute('08-frozen-full-verification',full)
        from experiments.betting_search import evaluate_betting_search
        frame,provenance=_frozen_predictions(root,campaign.results,
                    ['winner-search','winner-confirmation','winner-retrospective'])
        betting_dir=root/'betting'
        if betting_dir.exists() and any(betting_dir.iterdir()):
            # A partially written betting audit is never overwritten/reselected.
            raise RuntimeError('existing-betting-output-requires-verification-before-resume')
        betting=evaluate_betting_search(frame,betting_dir,model_id=winner['trial_id'],
            prediction_provenance={'selection_sha256':file_sha256(root/'selection.json'),
                                   'model_fixed_before_rules':True,'out_of_time_folds':provenance})
        verify_contract(root,contract)
        result=write_report(root,contract,campaign,selection,betting,time.perf_counter()-started)
        store_for(root).finish_experiment(root.name)
        return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','worker','status','cancel'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--parent',type=Path,default=BASE/'20260919-jra-safe-v1')
    parser.add_argument('--device',choices=['cpu','gpu'],default='cpu')
    args=parser.parse_args(); root=args.root.resolve(); parent=args.parent.resolve()
    if root==BASE.resolve() or not root.is_relative_to(BASE.resolve()):
        parser.error('root must be an experiment subdirectory')
    if args.action=='prepare': result=prepare(root,parent)
    elif args.action=='run': result=run(root)
    elif args.action=='worker':
        from experiments.search_training import run_search_trial
        return worker(root,args.device,trial_runner=run_search_trial)
    elif args.action=='cancel': store_for(root).cancel(root.name); result={'cancel_requested':True}
    else:
        state=store_for(root).snapshot(root.name)
        result={'status':state['status'],'counts':{s:sum(r['status']==s for r in state['trials'])
                   for s in sorted({r['status'] for r in state['trials']})},
                'running':[{k:r.get(k) for k in ('trial_id','progress_stage','progress_pct')} for r in state['trials'] if r['status']=='running']}
    print(json.dumps(result,ensure_ascii=False,default=str))
    return 0
