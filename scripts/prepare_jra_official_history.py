from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keiba"))

from keiba_ai.jra_official_history import (  # noqa: E402
    INDEX_SCHEMA,
    JRA_TERMS_URL,
    MANIFEST_SCHEMA,
    discover_source_index,
    download_source_index,
    parse_official_result_pdf,
    sha256_file,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _index(args: argparse.Namespace) -> dict[str, object]:
    result = discover_source_index(args.years)
    _write_json(args.output, result)
    return {
        "schema": "jra-official-source-index-command-v1",
        "mode": "index",
        "output": str(args.output.resolve()),
        "year_count": len(result["years"]),
        "document_count": len(result["documents"]),
        "downloaded": False,
    }


def _download(args: argparse.Namespace) -> dict[str, object]:
    index = json.loads(args.index.read_text(encoding="utf-8"))
    if index.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"source index schema must be {INDEX_SCHEMA}")
    result = download_source_index(
        index,
        args.download_root,
        delay_seconds=args.delay_seconds,
        max_files=args.max_files,
        workers=args.workers,
    )
    _write_json(args.output, result)
    return {
        "schema": "jra-official-source-download-command-v1",
        "mode": "download",
        "output": str(args.output.resolve()),
        "download_root": str(args.download_root.resolve()),
        "downloaded_document_count": result["downloaded_document_count"],
        "download_complete": result["download_complete"],
    }


def _convert_document(
    task: tuple[str, dict[str, object]],
) -> tuple[
    list[dict[str, object]] | None,
    dict[str, object] | None,
    dict[str, object],
    str | None,
]:
    download_root_value, item = task
    download_root = Path(download_root_value).resolve()
    pdf_path = (download_root / str(item["path"])).resolve()
    try:
        pdf_path.relative_to(download_root)
    except ValueError as exc:
        raise ValueError("downloaded PDF path escapes the download root") from exc
    try:
        records, report = parse_official_result_pdf(
            pdf_path,
            source_url=str(item["url"]),
            expected_sha256=str(item["sha256"]),
        )
    except Exception as exc:
        return None, None, item, f"{type(exc).__name__}: {exc}"
    return records, report, item, None


def _convert(args: argparse.Namespace) -> dict[str, object]:
    index = json.loads(args.index.read_text(encoding="utf-8"))
    if index.get("schema") != INDEX_SCHEMA:
        raise ValueError(f"source index schema must be {INDEX_SCHEMA}")
    downloaded = index.get("downloaded_documents")
    download_root = Path(str(index.get("download_root", ""))).resolve()
    if not isinstance(downloaded, list) or not downloaded or not download_root.is_dir():
        raise ValueError("downloaded source index has no usable local PDFs")
    if args.require_complete_index and not index.get("download_complete"):
        raise ValueError("source index download is partial; omit --require-complete-index for a pilot")

    reports: list[dict[str, object]] = []
    source_documents: list[dict[str, object]] = []
    if not 1 <= args.workers <= 8:
        raise ValueError("conversion workers must be between 1 and 8")
    args.bundle_dir.mkdir(parents=True, exist_ok=True)
    (args.bundle_dir / "conversion-failures.json").unlink(missing_ok=True)
    records_path = args.bundle_dir / "jra-official-runners.jsonl"
    partial_records_path = records_path.with_suffix(records_path.suffix + ".partial")
    tasks = [(str(download_root), item) for item in downloaded]
    runner_count = 0
    seen_source_ids: set[str] = set()
    failed_source_documents: list[dict[str, object]] = []
    with partial_records_path.open("w", encoding="utf-8", newline="\n") as output:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            for records, report, item, error in executor.map(_convert_document, tasks):
                if error is not None:
                    failed_source_documents.append(
                        {"url": item["url"], "sha256": item["sha256"], "error": error}
                    )
                    continue
                assert records is not None and report is not None
                for record in records:
                    source_id = str(record["source_record_id"])
                    if source_id in seen_source_ids:
                        raise ValueError(f"duplicate source record across PDFs: {source_id}")
                    seen_source_ids.add(source_id)
                    output.write(
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    runner_count += 1
                reports.append(report)
                source_documents.append(
                    {
                        "url": item["url"],
                        "sha256": item["sha256"],
                        "bytes": item["bytes"],
                        "downloaded_at": item["downloaded_at"],
                    }
                )
    if failed_source_documents:
        failure_path = args.bundle_dir / "conversion-failures.json"
        _write_json(
            failure_path,
            {
                "schema": "jra-official-results-conversion-failures-v1",
                "failed_source_document_count": len(failed_source_documents),
                "failed_source_documents": failed_source_documents,
            },
        )
        raise ValueError(
            f"{len(failed_source_documents)} official PDFs failed conversion; "
            f"review {failure_path.resolve()}"
        )
    if runner_count == 0:
        raise ValueError("conversion produced no official flat-race records")
    partial_records_path.replace(records_path)
    excluded_jump_race_count = sum(
        int(report["excluded_jump_race_count"]) for report in reports
    )
    excluded_incomplete_time_glyph_race_count = sum(
        int(report["excluded_incomplete_time_glyph_race_count"])
        for report in reports
    )
    excluded_unsupported_runner_layout_race_count = sum(
        int(report["excluded_unsupported_runner_layout_race_count"])
        for report in reports
    )
    excluded_unrecoverable_race_metadata_count = sum(
        int(report["excluded_unrecoverable_race_metadata_count"])
        for report in reports
    )
    excluded_insufficient_timed_finishers_race_count = sum(
        int(report["excluded_insufficient_timed_finishers_race_count"])
        for report in reports
    )
    excluded_unsupported_source_encoding_count = sum(
        int(report["excluded_unsupported_source_encoding_count"])
        for report in reports
    )
    excluded_race_count = (
        excluded_jump_race_count
        + excluded_incomplete_time_glyph_race_count
        + excluded_unsupported_runner_layout_race_count
        + excluded_unrecoverable_race_metadata_count
        + excluded_insufficient_timed_finishers_race_count
    )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "provider": "JRA official annual results",
        "terms_url": JRA_TERMS_URL,
        "purpose": "internal-research-speed-deviation-outcomes",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "complete_races": True,
        "source_index_download_complete": bool(index.get("download_complete")),
        "source_race_exclusions_present": excluded_race_count > 0,
        "source_coverage_complete": excluded_unsupported_source_encoding_count == 0,
        "excluded_race_count": excluded_race_count,
        "excluded_source_document_count": excluded_unsupported_source_encoding_count,
        "point_in_time_odds_included": False,
        "source_index_sha256": sha256_file(args.index.resolve()),
        "source_documents": source_documents,
        "files": [
            {
                "path": records_path.name,
                "sha256": sha256_file(records_path),
                "format": "jsonl",
            }
        ],
    }
    manifest_path = args.bundle_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    conversion_report = {
        "schema": "jra-official-results-bundle-report-v1",
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "source_document_count": len(source_documents),
        "race_count": sum(int(report["race_count"]) for report in reports),
        "excluded_race_count": excluded_race_count,
        "excluded_jump_race_count": excluded_jump_race_count,
        "excluded_incomplete_time_glyph_race_count": (
            excluded_incomplete_time_glyph_race_count
        ),
        "excluded_unsupported_runner_layout_race_count": (
            excluded_unsupported_runner_layout_race_count
        ),
        "excluded_unrecoverable_race_metadata_count": (
            excluded_unrecoverable_race_metadata_count
        ),
        "excluded_insufficient_timed_finishers_race_count": (
            excluded_insufficient_timed_finishers_race_count
        ),
        "excluded_unsupported_source_encoding_count": (
            excluded_unsupported_source_encoding_count
        ),
        "runner_count": runner_count,
        "point_in_time_odds_included": False,
        "source_reports": reports,
    }
    report_path = args.bundle_dir / "conversion-report.json"
    _write_json(report_path, conversion_report)
    return {**conversion_report, "report": str(report_path.resolve())}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Index, rate-limit-download, and convert JRA official annual-result PDFs."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    index_parser = subparsers.add_parser("index")
    index_parser.add_argument("--years", type=int, nargs="+", default=list(range(2019, 2025)))
    index_parser.add_argument("--output", type=Path, required=True)
    index_parser.set_defaults(handler=_index)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--index", type=Path, required=True)
    download_parser.add_argument("--download-root", type=Path, required=True)
    download_parser.add_argument("--output", type=Path, required=True)
    download_parser.add_argument("--delay-seconds", type=float, default=1.0)
    download_parser.add_argument("--max-files", type=int)
    download_parser.add_argument("--workers", type=int, default=4)
    download_parser.set_defaults(handler=_download)

    convert_parser = subparsers.add_parser("convert")
    convert_parser.add_argument("--index", type=Path, required=True)
    convert_parser.add_argument("--bundle-dir", type=Path, required=True)
    convert_parser.add_argument("--require-complete-index", action="store_true")
    convert_parser.add_argument("--workers", type=int, default=4)
    convert_parser.set_defaults(handler=_convert)

    args = parser.parse_args(argv)
    result = args.handler(args)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
