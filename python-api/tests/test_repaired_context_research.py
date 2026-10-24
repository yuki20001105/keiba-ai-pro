"""Synthetic repair gate and isolated cleanup; no live services/network/fit."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('run_repaired_context_research',ROOT/'scripts/run_repaired_context_research.py')
research=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


def complete_receipt():
    return {'schema':'last3f-single-network-run-wrapper-v1','batch_id':research.REPAIR_PLAN.name,
            'plan_sha256':research.REPAIR_PLAN_SHA,'limit':24565,'network_cli_invocations':1,'automatic_retries':0,
            **{name:True for name in ('safety_verification_pass','source_integrity_pass','journal_audit_pass',
            'protected_state_unchanged','verification_source_epoch_stable','wrapper_and_verification_code_unchanged')},
            'execution_outcome':'completed_with_domain_missing',
            'child':{'confirmed_exited':True,'exit_code':0},
            'child_final_status':{'schema':'last3f-gap-status-v2','phase':'network','cache_phase_complete':True,
                                 'stop_reason':None,'protected_state_unchanged':True,'status':'completed_with_domain_missing',
                                 'race_status_counts':{'completed':24800,'domain_missing':19},'target_races':24819,'remaining_nonterminal_races':0}}


def partial_item(**updates):
    return {'status':'partial','phase':'network','identity_grade':'canonical_url_plus_visible_date_and_all_runners',
            'proof_file':'evidence/example.json','proof_sha256':'a'*64,'remaining_source_states':{'invalid':1,'missing':1},
            'remaining_gap_runners':2,'reason':None,**updates}


def test_complete_repair_receipt_passes_but_exit_zero_alone_does_not():
    assert research.repair_gate(complete_receipt())==(True,None)
    assert not research.repair_gate({'child':{'exit_code':0}})[0]


@pytest.mark.parametrize('status',['paused','blocked','stopped','failed','interrupted','completed_selected_batch'])
def test_interruption_states_are_not_research_readiness(status):
    value=complete_receipt();value['execution_outcome']=status
    assert not research.repair_gate(value)[0]


@pytest.mark.parametrize('field',['safety_verification_pass','source_integrity_pass','journal_audit_pass',
    'protected_state_unchanged','verification_source_epoch_stable','wrapper_and_verification_code_unchanged'])
def test_every_independent_safety_flag_is_required(field):
    value=complete_receipt();value[field]=False
    assert not research.repair_gate(value)[0]


def test_audited_exhausted_source_missing_is_distinct_from_pending():
    value=complete_receipt();value['execution_outcome']='partial_pending_sources'
    value['child_final_status'].update(status='partial_pending_sources',race_status_counts={'completed':24800,'domain_missing':18,'partial':1},remaining_nonterminal_races=1)
    assert research.repair_gate(value)[0]
    assert research.source_missing_remainders({'x':partial_item()})[0]['missing_values_retained']
    value['child_final_status']['race_status_counts']['pending']=1
    value['child_final_status']['race_status_counts']['completed']-=1
    assert not research.repair_gate(value)[0]


@pytest.mark.parametrize('change',[
    {'phase':'cache'},{'status':'pending'},{'remaining_source_states':{'masked':2}},
    {'source_verified':False},{'identity_grade':'unverified'},{'reason':'http-429'},
    {'remaining_gap_runners':3},{'proof_sha256':'invalid'},
])
def test_unavailable_source_release_rejects_masked_unattempted_and_identity_failures(change):
    with pytest.raises(ValueError,match='not-verified-unavailable'):
        research.source_missing_remainders({'x':partial_item(**change)})


def test_empty_no_missing_remainder_is_valid():
    assert research.source_missing_remainders({'x':{'status':'completed'},'y':{'status':'domain_missing'}})==[]


@pytest.mark.parametrize('raw,expected',[('稍','稍重'),('不','不良'),(' 重 ','重'),('良','良'),(None,None),('',None),('荒れ馬場',None),('0',None)])
def test_fixed_going_aliases_do_not_guess_unknowns(raw,expected):
    assert research.normalize_going(raw)[0]==expected


def test_cleanup_retains_source_and_masks_only_invalid_clock_values():
    source=pd.DataFrame({'field_condition':['稍','不','良','不明',None], 'last_3f_time':[34.2,0.,100.,np.nan,35.]})
    original=source.copy(deep=True)
    clean,audit=research.normalized_frame(source)
    pd.testing.assert_frame_equal(source,original)
    assert clean.field_condition.iloc[:3].tolist()==['稍重','不良','良']
    assert clean.field_condition.iloc[3:].isna().all()
    assert clean.last_3f_time.iloc[[1,2,3]].isna().all()
    assert clean.last_3f_time.iloc[[0,4]].tolist()==[34.2,35.]
    assert audit['last3f_finite_out_of_source_domain_to_missing']==2


def test_yearly_quality_distinguishes_unknown_identity_and_no_clock():
    source=pd.DataFrame({'race_date':['20260101','20260101'],'race_id':['x','x'],
        'horse_id':['h1',''],'time_seconds':[90.,91.],'last_3f_time':[34.,np.nan],
        'horse_weight':[480.,np.nan],'horse_weight_change':[0.,np.nan],
        'field_condition':['良',None],'sire':['父','-'],'damsire':['母父',None],
        '_ctx_history_corners':[(1,2),None]})
    year=research.yearly_quality(source)[0]
    assert year['fields']['last_3f_time']=={'available':1,'missing':1,'coverage':.5}
    assert year['fields']['horse_id']['available']==1 and year['fields']['sire']['available']==1
    assert year['fields']['horse_weight_change']['available']==1  # real zero is valid


def test_full_preparation_on_synthetic_snapshot_is_pit_and_source_preserving(tmp_path):
    import sys
    sys.path.insert(0,str(ROOT/'python-api/tests'))
    from test_db_context_features import fixture,write_db
    source=fixture()
    source.loc[source.race_date.eq('20130101'),'field_condition']='稍'
    source.loc[source.race_date.eq('20240101'),'field_condition']='不'
    source.loc[source.race_date.eq('20130101'),'last_3f_time']=0.
    snapshot=write_db(tmp_path/'fixture.sqlite3',source)
    before=research.sha(snapshot)
    destination=tmp_path/'separate-research-output';destination.mkdir()
    prepared=research.prepare(destination,{},snapshot,before)
    assert research.sha(snapshot)==before
    feature=pd.read_parquet(prepared['features'])
    target=pd.read_parquet(prepared['target'])
    assert not target.field_condition.isin(['稍','不']).any()
    assert target.loc[target.race_date.eq('20130101'),'last_3f_time'].isna().all()
    # First observation contributes no invented zero clock to future means.
    assert feature.loc[target.race_date.eq('20240101'),'safe_past3_avg_last3f_time'].isna().all()
    receipt=json.loads((destination/'preparation.json').read_text(encoding='utf-8'))
    assert receipt['point_in_time_audit']['future_leakage_rows']==0
    assert receipt['context_audit']['target_corners_or_outcomes_consumed'] is False
    assert receipt['normalization']['history']['last3f_finite_out_of_source_domain_to_missing']==4
    assert receipt['live_source_modified'] is False


def test_all_five_groups_fixed_and_no_market_input():
    import sys
    sys.path.insert(0,str(ROOT/'scripts'))
    import repaired_context_evaluation as evaluation
    groups=evaluation.groups()
    assert [len(v) for v in groups.values()]==[46,60,73,53,80]
    assert not any('odds' in c or 'market' in c for columns in groups.values() for c in columns)


def test_status_is_in_own_output_and_no_existing_receipt_overwrite(tmp_path):
    research.exclusive(tmp_path/'plan.json',{'a':1})
    with pytest.raises(FileExistsError):
        research.exclusive(tmp_path/'plan.json',{'a':2})
    research.status(tmp_path,'waiting_for_verified_repair',new_training_started=False)
    assert json.loads((tmp_path/'status.json').read_text())['new_training_started'] is False


def test_live_services_are_not_ignored_by_guard(monkeypatch):
    protected={'active':'same','forward_processes':[{'pid':1}]}
    monkeypatch.setattr(research,'protections',lambda:protected)
    monkeypatch.setattr(research,'process_alive',lambda _p:False)
    with pytest.raises(ValueError,match='api-or-forward'):
        research.guard({'protected_before':protected,'service_processes':[{'pid':1}],'code_sha256':{}})


class FakeFuture:
    def __init__(self,item,fail=False):self.item=item;self.fail=fail;self.cancelled=False
    def result(self):
        if self.fail:raise RuntimeError('fit-failed')
        return self.item
    def cancel(self):self.cancelled=True


class FakePool:
    def __init__(self,fail=False):self.futures=[];self.fail=fail;self.closed=None
    def submit(self,_worker,job):
        future=FakeFuture(job,fail=self.fail and not self.futures)
        self.futures.append(future);return future
    def shutdown(self,**kwargs):self.closed=kwargs


def test_failed_fit_cancels_queue_instead_of_submitting_55(monkeypatch,tmp_path):
    pool=FakePool(fail=True)
    monkeypatch.setattr(research,'ProcessPoolExecutor',lambda **_:pool)
    monkeypatch.setattr(research,'wait',lambda items,**_:({next(iter(items))},set()))
    monkeypatch.setattr(research,'guard',lambda _:None)
    with pytest.raises(RuntimeError,match='fit-failed'):
        research.run_jobs(tmp_path,{},list(range(55)),lambda x:x)
    assert len(pool.futures)==3
    assert all(x.cancelled for x in pool.futures[1:])
    assert pool.closed=={'wait':True,'cancel_futures':True}


def test_stop_request_before_dispatch_starts_no_workers(monkeypatch,tmp_path):
    pool=FakePool()
    monkeypatch.setattr(research,'ProcessPoolExecutor',lambda **_:pool)
    monkeypatch.setattr(research,'guard',lambda _:None)
    (tmp_path/'stop-request.json').write_text('{}')
    with pytest.raises(RuntimeError,match='stop-requested'):
        research.run_jobs(tmp_path,{},list(range(55)),lambda x:x)
    assert not pool.futures
