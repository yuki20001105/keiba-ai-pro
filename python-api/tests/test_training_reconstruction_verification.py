from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from verify_training_reconstruction import SLOTS, compare_record, day, file_map, number


def fixture():
    raw={"id":1,"race_id":"202105010101","horse_number":1,"horse_name":"テスト馬",
         "training_date":"2021/01/02","course":" 美Ｗ ","track_condition":" 良 ","rider":"助手",
         "position":"","pace":"馬也","grade":"B","comment":"原文","is_last_training":1,"created_at":"2026-05-01 00:01:02",
         **{slot:None if i==0 else float(i+10) for i,slot in enumerate(SLOTS)}}
    event={"event_id":"legacy-training-1","source_training_id":1,"race_id":raw["race_id"],"horse_number":1,
         "race_date":"20210105","training_date":"20210102","identity_verified":True,"horse_id":"123",
         "event_validity":"training_date_before_race","quarantined":False,"source_db_sha256":"a"*64,
         "source_record_sha256":hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest(),
         "legacy_pit_class":"B_SOURCE_DATE_VALID_BUT_NOT_CAPTURE_TIME_VERIFIED","quarantine_reasons":"[]",
         "pit_class":"C_INVALID_OR_UNKNOWN","capture_provenance":"capture_time_unknown",
         "source_url":None,"source_hash":None,"retrieved_at":None,"historical_backfill":None,
         "raw_original_available":False,"distance_mapping_verified":False,"model_input_allowed":False,"is_last_training_verified":False}
    for source,dest in {"horse_name":"horse_name","training_date":"training_date_raw","course":"course_raw","track_condition":"track_condition_raw",
         "rider":"rider","position":"position","pace":"pace_raw","grade":"grade_raw","comment":"comment_raw","created_at":"source_db_created_at",
         "is_last_training":"legacy_is_last_training"}.items(): event[dest]=raw[source]
    event.update(course_normalized="美W",track_condition="良",pace_category="馬也",grade_category="B")
    clocks=[]
    for i,slot in enumerate(SLOTS):
        value=raw[slot]
        clocks.append({"source_training_id":1,"event_id":"legacy-training-1","original_slot":slot,
           "measurement_kind":"cumulative" if i<5 else "lap","original_slot_index":i%5,
           "original_value":json.dumps(value),"original_value_type":"null" if value is None else "real",
           "time_seconds":value,"value_status":"MISSING" if value is None else "PRESENT_NUMERIC_DISTANCE_UNVERIFIED",
           "missing_reason":"UNKNOWN" if value is None else None,"measurement_distance":None,"lap_from_distance":None,"lap_to_distance":None,
           "distance_mapping_verified":False,"model_input_allowed":False,
           **{key:event[key] for key in ("race_id","horse_id","horse_number","training_date","course_normalized","track_condition","pit_class","quarantined")}})
    return raw,event,clocks


def test_lossless_fixture_and_unknown_provenance_pass_without_mutation():
    raw,event,clocks=fixture()
    before=deepcopy((raw,event,clocks))
    relation,reasons,missing=compare_record(raw,event,clocks,"20210105","123","a"*64)
    assert relation=="training_date_before_race" and not reasons and len(missing)==1
    assert (raw,event,clocks)==before


@pytest.mark.parametrize("key,value",[("pit_class","B_HISTORICAL_EVENT_VALID"),("historical_backfill",True),
    ("retrieved_at","2021-01-03T00:00:00Z"),("source_hash","b"*64),("model_input_allowed",True),
    ("grade_raw",4),("is_last_training_verified",True),("comment_raw","changed"),("horse_id","456"),
    ("course_normalized","CW"),("quarantined",True)])
def test_event_corruption_or_provenance_upgrade_rejected(key,value):
    raw,event,clocks=fixture();event[key]=value
    with pytest.raises(ValueError): compare_record(raw,event,clocks,"20210105","123","a"*64)


@pytest.mark.parametrize("key,value",[("measurement_distance",1200),("lap_from_distance",1000),
    ("original_slot","time_5f"),("original_value","0"),("time_seconds",0.0),("missing_reason","STRUCTURAL_NOT_REPORTED"),
    ("original_value_type","real"),("model_input_allowed",True),("pit_class","A_STRICT_PIT")])
def test_cell_corruption_imputation_or_mapping_rejected(key,value):
    raw,event,clocks=fixture();clocks[0][key]=value
    with pytest.raises(ValueError): compare_record(raw,event,clocks,"20210105","123","a"*64)


def test_slot_count_and_order_required():
    raw,event,clocks=fixture()
    with pytest.raises(ValueError): compare_record(raw,event,clocks[:-1],"20210105","123","a"*64)
    clocks[1],clocks[2]=clocks[2],clocks[1]
    with pytest.raises(ValueError): compare_record(raw,event,clocks,"20210105","123","a"*64)


@pytest.mark.parametrize("value,valid",[("2020/02/29",True),("2021/02/29",False),("2021/01/01suffix",False),(None,False)])
def test_independent_calendar_has_no_year_inference(value,valid):
    assert (day(value) is not None)==valid


def test_number_rejects_boolean_fraction_and_nonfinite():
    assert all(number(x) is None for x in [True,0,1.5,float("inf"),None])
    assert number("18")==18


def test_manifest_hash_and_path_containment(tmp_path):
    file=tmp_path/"artifact.txt";file.write_text("fixture",encoding="utf-8")
    digest=hashlib.sha256(file.read_bytes()).hexdigest()
    assert file_map(tmp_path,{"artifact.txt":{"sha256":digest,"bytes":7}})==1
    with pytest.raises(ValueError):file_map(tmp_path,{"artifact.txt":"a"*64})
    with pytest.raises(ValueError):file_map(tmp_path,{"../other.txt":digest})
