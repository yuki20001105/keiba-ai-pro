"""V4 orchestration of the unchanged independent V3/baseline auditor.

The experimental unique-I/O checker remains offline: measured elapsed time did
not improve, so reduced read bytes alone are not a reason to enable it here.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'python-api')]


def configured_auditor():
    spec=importlib.util.spec_from_file_location('_last3f_resume_audit_v3_private_v4',ROOT/'scripts/audit_last3f_resume_v3.py')
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.__file__=str(Path(__file__).resolve())
    return engine


def audit_resume(original_run,original_output,resumed_run,output_dir,**kwargs):
    return configured_auditor().audit_resume(original_run,original_output,resumed_run,output_dir,**kwargs)
