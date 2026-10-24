"""V4 operational binding of the unchanged 55-job V3/V2 research protocol."""
from __future__ import annotations
import argparse
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'python-api'),str(ROOT/'keiba')]
OUTPUT=ROOT/'reports/generated/context-repaired-v4'


def configured_engine():
    spec=importlib.util.spec_from_file_location('_context_research_v3_private_v4',ROOT/'scripts/run_repaired_context_research_v3.py')
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.OUTPUT=OUTPUT
    engine.WRITER_NAMES=engine.WRITER_NAMES|{'repair_last3f_gaps_v4.py'}
    original_exclusive=engine.exclusive
    def exclusive(path,value):
        if Path(path).name=='registered_plan.json':
            value=deepcopy(value)
            value.update(runner_path='scripts/run_repaired_context_research_v4.py',
                runner_sha256=engine.sha(__file__),local_implementation_version='v4-cache-processes-audit-baseline')
            value['code_sha256']['scripts/run_repaired_context_research_v4.py']=engine.sha(__file__)
        return original_exclusive(path,value)
    engine.exclusive=exclusive
    return engine


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--register',action='store_true');mode.add_argument('--run',action='store_true')
    parser.add_argument('--repair-run',type=Path);parser.add_argument('--repair-output',type=Path)
    parser.add_argument('--repair-writer-entrypoint',action='append',default=[])
    args=parser.parse_args();engine=configured_engine()
    if args.register:
        if not args.repair_run or not args.repair_output:parser.error('explicit repair paths required')
        engine.register(args.run_dir,repair_run=args.repair_run,repair_output=args.repair_output,
                        writer_entrypoints=args.repair_writer_entrypoint)
    else:
        if args.repair_run or args.repair_output or args.repair_writer_entrypoint:parser.error('sealed registration only')
        engine.execute(args.run_dir)


if __name__=='__main__':main()
