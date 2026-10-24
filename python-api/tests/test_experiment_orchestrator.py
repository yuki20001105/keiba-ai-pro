from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.orchestrator import best_result, ablation_candidates, driver_lock


def candidate(name, rho, win, phase='development', roi=100):
    return {'candidate_id':name,'trial_id':name+'-dev','phase':phase,
            'evaluation':{'overall':{'rank_correlation':rho,'top1_win_rate':win,'win_return_rate_percent':roi},
                          'by_year':{str(y):{'rank_correlation':rho} for y in (2023,2024,2025)}}}


def test_selection_prefers_rank_correlation_not_return():
    low=candidate('M01',.4,.22,roi=300)
    high=candidate('M03',.5,.20,roi=60)
    assert best_result([low,high]) is high


def test_selection_ties_use_win_rate():
    high=candidate('M03',.5,.20)
    close=candidate('M04',.499,.22)
    assert best_result([high,close]) is close


def test_final_results_cannot_enter_selection():
    with pytest.raises(ValueError,match='development'):
        best_result([candidate('M01',.4,.2),candidate('M03',.8,.5,phase='final')])


def test_missing_metrics_fail_closed():
    with pytest.raises(ValueError,match='unavailable'):
        best_result([candidate('M01',None,.2)])


def test_ablation_is_bounded_and_keeps_base():
    manifest={'feature_sets':{'M01':['base'],'M03':['base','a','b','c','d']},
              'feature_groups':{'base':['base'],'group1':['a'],'group2':['b'],'group3':['c'],'group4':['d']}}
    trials=ablation_candidates(manifest)
    assert len(trials)==3
    assert all(t['candidate_id']=='M04' and t['phase']=='development' and 'base' in t['feature_columns'] for t in trials)
    assert trials[0]['feature_columns']==['base','b','c','d']


def test_common_driver_lock_blocks_different_roots_and_releases(tmp_path):
    with driver_lock(tmp_path,tmp_path/'first'):
        with pytest.raises(RuntimeError,match='another-experiment'):
            with driver_lock(tmp_path,tmp_path/'second'):
                pytest.fail('must not share the driver lock')
    with driver_lock(tmp_path,tmp_path/'second'):
        pass


def test_full_orchestration_freezes_selection_before_final_and_resumes(tmp_path,monkeypatch):
    import experiments.orchestrator as module
    import experiments.cli as cli
    from experiments.common import write_json,file_sha256,read_json
    repo=tmp_path
    root=repo/'reports/generated/model-experiments/test-orchestration'
    manifest={'snapshot_sha256':'snapshot','dataset_sha256':'dataset',
              'source_contract':{key:file_sha256(Path(module.__file__).with_name(filename))
                                 for filename,key in [('dataset.py','dataset_module_sha256'),('features.py','features_module_sha256')]},
              'feature_sets':{'M01':['base'],'M03':['base','history','entity']},
              'feature_groups':{'base':['base'],'history':['history'],'entity':['entity']}}
    write_json(root/'dataset/dataset_manifest.json',manifest)
    write_json(root/'snapshot.json',{'sha256':'snapshot','active_pointer_sha256':'active'})
    write_json(root/'pilot.json',{'dataset_sha256':'dataset','peak_sampled_rss_mb':500,'gpu_faster':False})
    monkeypatch.setattr(module,'REPO_ROOT',repo)
    monkeypatch.setattr(module,'active_pointer_hash',lambda:'active')
    monkeypatch.setattr(module,'source_manifest',lambda:{'source':'stable'})
    monkeypatch.setattr(module,'capability_probe',lambda:{'gpu_verified':False,'opencl':None})
    monkeypatch.setattr(module,'resource_snapshot',lambda:{'available_ram_mb':8192})
    monkeypatch.setattr(cli,'verify_contract',lambda *a,**k:None)
    calls=[]
    def fake_batch(output,payloads):
        calls.append(payloads)
        produced={}
        for payload in payloads:
            if payload['phase']=='final':
                assert (output/'selection.json').exists()
                assert read_json(output/'selection.json')['frozen_before_final'] is True
            result=candidate(payload['candidate_id'],.6 if payload['candidate_id']=='M05' else .4,.25,phase=payload['phase'])
            result['trial_id']=payload['id']
            write_json(output/'trials'/payload['id']/'result.json',result)
            produced[payload['id']]=result
        return produced
    monkeypatch.setattr(cli,'batch',fake_batch)
    monkeypatch.setattr(module,'write_report',lambda root,contract,selection,results,finals:{'status':'completed'})
    assert module.run_experiment(root)['status']=='completed'
    final=calls[-1]
    assert {x['candidate_id'] for x in final}=={'M01','M05'}
    assert all(x['phase']=='final' for x in final)
    before=len(calls)
    assert module.run_experiment(root)['already_completed'] is True
    assert len(calls)==before
