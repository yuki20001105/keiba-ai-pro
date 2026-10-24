"""Predeclared, finite accuracy-first search; no payout/market selection."""
from __future__ import annotations
from itertools import combinations
import math
from experiments.common import json_sha256

PLAN = {
    'schema':'accuracy-feature-search-v1', 'max_model_trials':200,
    'search_years':[2021,2022], 'confirmation_years':[2023,2024,2025],
    'retrospective_years':[2026], 'all_confirmation_periods_known_reused':True,
    'primary':'mean per-race Spearman', 'secondary':'worst search-year Spearman; physical speed RMSE (m/s) within regression only',
    'roi_or_top1_used_for_model_selection':False,
    'selection_policy':'highest development Spearman; exact-near ties (1e-6) prefer fewer features then worst annual Spearman; physical speed RMSE is reported and regression Pareto is retained',
    'cycles':['initial hypotheses and every individual feature removal',
              'development-informed combinations and predeclared training conditions',
              'local feature refinement then fixed-seed robustness'],
    'maximum_combinations_cycle1':24, 'maximum_conditions':18,
    'maximum_refinement_removals':16, 'maximum_combinations_cycle2':12,
    'stop':'bounded plan exhausted; never tune until confirmation ROI exceeds 100%',
    'model_fixed_before_betting':True, 'no_live_bets':True, 'no_automatic_promotion':True,
    'hypotheses':[
        'race-relative strictly historical ability/features',
        'recent-versus-longer-term historical trends',
        'explicit observed-history support and missing indicators',
        'leave-one-feature-out then cumulative/pair removal',
        'ranking loss versus normalized-rank regression versus speed regression',
        'tree complexity, regularization, learning rate and training recency',
    ],
}
BASE_PARAMS={'num_boost_round':200,'learning_rate':0.05,'num_leaves':31,'min_data_in_leaf':30,'max_bin':63}


def specification(identifier,columns,*,target='ranking',ranking_loss='lambdarank',regression_loss='mse',params=None,train_start_year=2016,seed=42):
    return {'id':identifier,'target':target,**({'ranking_loss':ranking_loss} if target=='ranking' else {'regression_loss':regression_loss}),
            'validation_years':[2021,2022],'train_start_year':train_start_year,
            'feature_columns':list(columns),'params':dict(params or BASE_PARAMS),'seed':seed,
            'bootstrap_samples':0,'contribution_sample':0,'save_model':False}


def initial_candidates(manifest):
    all_columns=manifest['feature_allowlist']
    old_columns=manifest['parent_feature_sets']['M03']
    return [specification('B-rank-72',old_columns),specification('B-rank-all',all_columns),
            specification('B-xendcg-all',all_columns,ranking_loss='rank_xendcg'),
            specification('B-rankreg-all',all_columns,target='rank_regression',regression_loss='huber'),
            specification('B-speed-72',old_columns,target='regression'),
            specification('B-speed-all',all_columns,target='regression')]


def fingerprint(spec):
    return json_sha256({k:v for k,v in spec.items() if k not in ('id','removed_features','description')})


def best_accuracy(results):
    if not results: raise ValueError('no-search-results')
    for result in results:
        if result.get('validation_years')!=[2021,2022]:
            raise ValueError('selection-must-use-only-search-years')
        value=result['evaluation']['overall'].get('rank_correlation')
        if not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('missing-rank-correlation')
        annual=result['evaluation'].get('by_year',{})
        if set(annual)!={'2021','2022'} or any(not isinstance(row.get('rank_correlation'),(int,float))
                or not math.isfinite(row['rank_correlation']) for row in annual.values()):
            raise ValueError('missing-annual-rank-correlation')
    maximum=max(r['evaluation']['overall']['rank_correlation'] for r in results)
    tied=[r for r in results if r['evaluation']['overall']['rank_correlation']>=maximum-1e-6]
    return max(tied,key=lambda r:(-len(r['feature_columns']),
                                 min(m['rank_correlation'] for m in r['evaluation']['by_year'].values())))


def individual_candidates(base,limit=None,prefix='D'):
    columns=base['feature_columns'][:limit] if limit is not None else base['feature_columns']
    return [{**base,'id':f'{prefix}{index:03d}','feature_columns':[c for c in base['feature_columns'] if c!=column],
             'removed_features':[column]} for index,column in enumerate(columns,1)]


def feature_effects(base_result,drop_specs,results):
    reference=base_result['evaluation']['overall']
    output=[]
    for spec in drop_specs:
        result=results[spec['id']]
        observed=result['evaluation']['overall']
        by_year=result['evaluation']['by_year']
        changes={year:value['rank_correlation']-base_result['evaluation']['by_year'][year]['rank_correlation'] for year,value in by_year.items()}
        output.append({'feature':spec['removed_features'][0],'trial_id':spec['id'],
                       'delta_rank':observed['rank_correlation']-reference['rank_correlation'],
                       'delta_rmse':observed['rmse']-reference['rmse'] if observed.get('rmse') is not None and reference.get('rmse') is not None else None,
                       'year_deltas':changes,'minimum_year_delta':min(changes.values())})
    return sorted(output,key=lambda row:(row['delta_rank'],row['minimum_year_delta']),reverse=True)


def combination_candidates(base,effects,*,prefix,maximum):
    # Actual retraining, not permutation importance alone. Correlated features
    # can substitute for each other, so test both pairs and cumulative removal.
    promising=[r['feature'] for r in effects if r['delta_rank']>=-0.0002 and r['minimum_year_delta']>=-0.002][:8]
    if len(promising)<2:
        promising=[r['feature'] for r in effects[:4]]
    removals=[list(pair) for pair in combinations(promising[:6],2)]
    removals += [promising[:n] for n in range(2,len(promising)+1)]
    output=[]; seen=set()
    for removed in removals:
        remaining=[c for c in base['feature_columns'] if c not in removed]
        key=tuple(remaining)
        if not remaining or key in seen: continue
        seen.add(key)
        output.append({**base,'id':f'{prefix}{len(output)+1:03d}','feature_columns':remaining,'removed_features':removed})
        if len(output)>=maximum: break
    return output


def condition_candidates(best,regression):
    variants=[
        ({'num_leaves':15,'min_data_in_leaf':80,'lambda_l2':2,'num_boost_round':400},2016),
        ({'num_leaves':63,'min_data_in_leaf':60,'lambda_l2':2,'num_boost_round':400},2016),
        ({'num_leaves':31,'min_data_in_leaf':80,'lambda_l2':5,'learning_rate':0.03,'num_boost_round':600},2016),
        ({'num_leaves':63,'min_data_in_leaf':120,'lambda_l2':5,'learning_rate':0.03,'num_boost_round':600},2016),
        ({'num_leaves':15,'min_data_in_leaf':60,'max_bin':127,'num_boost_round':400},2016),
        ({'num_leaves':31,'min_data_in_leaf':60,'max_bin':127,'feature_fraction':0.8,'num_boost_round':400},2016),
        ({'num_leaves':31,'min_data_in_leaf':60,'lambda_l2':2,'num_boost_round':400},2018),
        ({'num_leaves':15,'min_data_in_leaf':80,'lambda_l2':5,'num_boost_round':400},2020),
        ({'num_leaves':63,'min_data_in_leaf':120,'feature_fraction':0.8,'num_boost_round':400},2018),
        ({'num_leaves':31,'min_data_in_leaf':80,'bagging_fraction':0.8,'bagging_freq':1,'num_boost_round':400},2016),
        ({'num_leaves':31,'min_data_in_leaf':80,'lambda_l1':1,'lambda_l2':5,'num_boost_round':400},2018),
        ({'num_leaves':15,'min_data_in_leaf':100,'learning_rate':0.03,'num_boost_round':600},2018),
    ]
    output=[{**best,'id':f'H{index:02d}','params':{**best['params'],**changes},'train_start_year':start}
            for index,(changes,start) in enumerate(variants,1)]
    for index,(loss,changes,start) in enumerate([
        ('huber',{'num_boost_round':400,'min_data_in_leaf':60,'lambda_l2':2},2016),
        ('mae',{'num_boost_round':400,'min_data_in_leaf':60,'lambda_l2':2},2016),
        ('mse',{'num_boost_round':400,'num_leaves':63,'min_data_in_leaf':80,'lambda_l2':2},2016),
        ('huber',{'num_boost_round':400,'min_data_in_leaf':80,'lambda_l2':5},2018),
        ('mse',{'num_boost_round':600,'learning_rate':0.03,'min_data_in_leaf':80,'lambda_l2':5},2018),
        ('mse',{'num_boost_round':400,'num_leaves':15,'min_data_in_leaf':100},2020)],1):
        output.append({**regression,'id':f'HR{index:02d}','regression_loss':loss,'params':{**regression['params'],**changes},'train_start_year':start})
    return output


def full_spec(base,identifier,years):
    return {**base,'id':identifier,'validation_years':list(years),'bootstrap_samples':300,'contribution_sample':128,'save_model':True}
