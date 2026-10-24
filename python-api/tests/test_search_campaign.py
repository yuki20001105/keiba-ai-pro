"""Offline orchestration boundaries; no external work or model promotion."""
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from experiments import search
from experiments.common import file_sha256, read_json


def test_freeze_rejects_changed_plan(tmp_path):
    path=tmp_path/'fixed.json'
    search.freeze_json(path,{'a':1})
    search.freeze_json(path,{'a':1})
    with pytest.raises(RuntimeError,match='frozen-search'):
        search.freeze_json(path,{'a':2})
    assert read_json(path)=={'a':1}


def test_campaign_uses_search_worker_and_freezes_payload(tmp_path,monkeypatch):
    monkeypatch.setattr(search,'store_for',lambda _:SimpleNamespace(snapshot=lambda _: {'trials':[]}))
    seen=[]
    monkeypatch.setattr(search,'batch',lambda root,payloads,**kw: seen.append((root,payloads,kw)) or {'T':{'ok':True}})
    campaign=search.Campaign(tmp_path)
    assert campaign.execute('stage',[{'id':'T'}])=={'T':{'ok':True}}
    assert seen[0][2]['worker_script']==search.WORKER
    assert read_json(tmp_path/'stages/stage.json')['confirmation_used_for_selection'] is False
    with pytest.raises(ValueError,match='different-specification'):
        campaign.execute('other',[{'id':'T','changed':True}])


def test_cap_counts_existing_ledger_before_launch(tmp_path,monkeypatch):
    monkeypatch.setattr(search,'store_for',lambda _:SimpleNamespace(snapshot=lambda _: {'trials':[{'trial_id':str(i)} for i in range(200)]}))
    monkeypatch.setattr(search,'batch',lambda *a,**k: pytest.fail('must not launch'))
    with pytest.raises(RuntimeError,match='cap-exceeded'):
        search.Campaign(tmp_path).execute('too-many',[{'id':'new'}])


def test_resume_rejects_ledger_payload_changes(tmp_path,monkeypatch):
    monkeypatch.setattr(search,'store_for',lambda _:SimpleNamespace(snapshot=lambda _: {'trials':[
        {'trial_id':'T','payload':{'id':'T','changed':True,'device':'cpu','num_threads':4}}]}))
    monkeypatch.setattr(search,'batch',lambda *a,**k: pytest.fail('must not launch'))
    with pytest.raises(RuntimeError,match='payload-mismatch'):
        search.Campaign(tmp_path).execute('stage',[{'id':'T'}])


def test_duplicate_ids_rejected(tmp_path):
    with pytest.raises(ValueError,match='duplicate'):
        search.Campaign(tmp_path).execute('bad',[{'id':'T'},{'id':'T'}])


def test_selection_excludes_confirm_full_and_alternate_seeds(tmp_path):
    campaign=search.Campaign(tmp_path)
    campaign.results={key:{'validation_years':years,'target':'ranking'} for key,years in [
        ('search',[2021,2022]),('confirm',[2023,2024,2025]),('full',[2021,2022]),('seed',[2021,2022])]}
    campaign.specs={key:{'bootstrap_samples':300 if key=='full' else 0,'seed':7 if key=='seed' else 42} for key in campaign.results}
    assert campaign.candidates()==[campaign.results['search']]


def _prediction(tmp_path,identifier,year,train_end):
    path=tmp_path/f'{identifier}.parquet'
    pd.DataFrame({'race_id':[f'{year}01010101'],'horse_number':[1],
                  'fold_year':[year],'race_date':[f'{year}0101']}).to_parquet(path)
    return {'artifacts':{'predictions':{'path':str(path),'sha256':file_sha256(path)}},
            'folds':[{'year':year,'contract':{'train_period':['20160101',train_end]},'sha256':'abc'}]}


def test_prediction_provenance_requires_prior_training_and_integrity(tmp_path):
    good=_prediction(tmp_path,'good',2021,'20201231')
    frame,provenance=search._frozen_predictions(tmp_path,{'good':good},['good'])
    assert frame.train_end_date.tolist()==['20201231']
    assert provenance[0]['folds'][0]['model_sha256']=='abc'
    bad=_prediction(tmp_path,'bad',2021,'20211231')
    with pytest.raises(RuntimeError,match='out-of-time'):
        search._frozen_predictions(tmp_path,{'bad':bad},['bad'])
    with pytest.raises(RuntimeError,match='overlapping'):
        search._frozen_predictions(tmp_path,{'good':good},['good','good'])
    Path(good['artifacts']['predictions']['path']).write_bytes(b'changed')
    with pytest.raises(RuntimeError,match='artifact-changed'):
        search._frozen_predictions(tmp_path,{'good':good},['good'])
