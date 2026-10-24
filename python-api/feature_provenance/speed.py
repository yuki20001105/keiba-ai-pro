"""Evidence-only audit of the archived external speed-index acquisition path.

This module deliberately provides no Speed Figure calculator or replacement
formula. The historical pipeline stores provider display values, not a locally
specified per-start index. Current m/s and speed-deviation features are distinct.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess

REVISION = "05f6fac8db50193a8bc2aed1e6b892decf23c69b"
SOURCE_PATHS = ("python-api/scraping/speed_figure.py", "python-api/scraping/storage.py",
                "python-api/scraping/jobs.py", "keiba/keiba_ai/feature_engineering.py",
                "python-api/routers/train.py")
FIELDS = {
    "max_index": ("sk__max_index", "最高（過去1年の最高タイム指数）"),
    "avg_5_index": ("sk__average_index", "５走平均"),
    "dist_max_index": ("sk__max_distance_index", "距離最高"),
    "course_max_index": ("sk__max_course_index", "コース最高"),
    "index_last": ("sk__index1", "前走"),
    "index_2ago": ("sk__index2", "2走前"),
    "index_3ago": ("sk__index3", "3走前"),
}
OFFICIAL_SOURCES = [
    {"url": "https://support.keiba.netkeiba.com/hc/ja/articles/18933212307353",
     "title": "タイム指数､指数偏差値って？", "reviewed_date": "2026-09-20",
     "finding": "Time is a basis, but race development, position and interference also affect the index. No reproducible equation, coefficients or rounding specification found in this article."},
    {"url": "https://race.netkeiba.com/race/speed.html?race_id=202205050108&rf=shutuba_submenu",
     "title": "タイム指数(標準)の見方・2022年11月5日東京8R", "reviewed_date": "2026-09-20",
     "finding": "Guide states one-year maximum; five-start average restricted to current turf/dirt among the last five starts; distance/course corresponding maxima; previous-start indices. This is a current guide, not a versioned historical algorithm."},
    {"url": "https://support.keiba.netkeiba.com/hc/ja/articles/20606040028953",
     "title": "タイム指数の平均に「※」がついているのはどういう意味？", "reviewed_date": "2026-09-20",
     "finding": "The average warning concerns nonuniform race conditions. The exact exception to the same-surface guide and historical marker mapping are unresolved."},
    {"url": "https://support.keiba.netkeiba.com/hc/ja/articles/18930182696985",
     "title": "レース情報(タイム指数等)の更新時間は？", "reviewed_date": "2026-09-20",
     "finding": "Current schedule describes prerace updates, but does not prove when stored historical DB rows became available."},
    {"url": "https://support.keiba.netkeiba.com/hc/ja/articles/53032933157145",
     "title": "タイム指数マスターって？", "reviewed_date": "2026-09-20",
     "finding": "Master is a distinct enhanced index including weight corrections and individual-lap components; not interchangeable with this standard-index parser."},
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_objects(repo):
    """Read fixed Git objects without checking out/importing their code."""
    result = {}
    for path in SOURCE_PATHS:
        spec = REVISION + ":" + path
        content = subprocess.check_output(["git", "show", spec], cwd=repo)
        object_id = subprocess.check_output(["git", "rev-parse", spec], cwd=repo, text=True).strip()
        result[path] = {"text": content.decode("utf-8"), "sha256": sha(content), "git_blob_oid": object_id,
                        "git_revision": REVISION, "byte_count": len(content)}
    return result


def function_evidence(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return {"function": name, "line_start": node.lineno, "line_end": node.end_lineno}
    raise ValueError("expected source function is unavailable: " + name)


def replay_display_parser(source, html):
    """Synthetic-fixture replay of only the archived pure integer-cell parser.

    Never imports the historical HTTP module. No requests, app configuration,
    logging sinks, database writer, or whole-module top-level code execute.
    """
    from bs4 import BeautifulSoup
    from typing import Optional
    tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_parse_index_value"]
    if len(nodes) != 1:
        raise ValueError("one archived display parser required")
    # Review-bound archived source is fixed by source_objects(). Synthetic
    # tests may pass equivalent fixtures; this API is not a user-code sandbox.
    namespace = {"Optional": Optional}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "archived-speed-display-parser", "exec"), namespace)
    return namespace["_parse_index_value"](BeautifulSoup(html, "lxml").find("td"))


def formula_audit(sources, *, current_feature_source, current_producer_exists):
    source = sources[SOURCE_PATHS[0]]["text"]
    storage = sources[SOURCE_PATHS[1]]["text"]
    legacy = sources[SOURCE_PATHS[3]]["text"]
    required = ["https://race.netkeiba.com/race/speed.html?race_id={race_id}", "SpeedIndex_Table",
                "Sort_Function_Data_Hidden", "return int(val)"]
    if any(text not in source for text in required) or any(selector not in source for selector, _ in FIELDS.values()):
        raise ValueError("archived source no longer matches audited provider-display contract")
    if "INSERT OR REPLACE INTO speed_figures" not in storage or "_ = speed_figures_df" not in current_feature_source:
        raise ValueError("storage/current-use contract differs; manual re-audit required")
    evidence = []
    for path, names in {
        SOURCE_PATHS[0]: ("_parse_index_value", "scrape_speed_figure", "_parse_speed_html"),
        SOURCE_PATHS[1]: ("_save_speed_figures",),
        SOURCE_PATHS[3]: ("_fe_speed_figures",),
    }.items():
        evidence.extend({"path": path, "revision": REVISION, **function_evidence(sources[path]["text"], name)} for name in names)
    aggregates = []
    for field, (selector, description) in FIELDS.items():
        unknowns = ["provider single-race formula and correction factors", "provider revision/correction policy",
                    "historical as-of availability", "surface and non-JRA inclusion/exclusion"]
        if field == "max_index":
            unknowns += ["exact one-year cutoff/calendar convention; one-year maximum is confirmed as a public description, not a reproducible versioned algorithm"]
        elif field == "avg_5_index":
            unknowns += ["guide says filter current surface within last five starts, but FAQ describes mixed-condition average warning; exception/version mapping unresolved",
                         "cancelled/missing-index start counting", "fewer-than-five treatment", "weighting and rounding"]
        elif field in {"dist_max_index", "course_max_index"}:
            unknowns += ["distance exact equality vs band", "course identity/venue/direction/layout conditions", "lookback duration"]
        else:
            unknowns += ["which starts are counted when starts are cancelled/overseas/jumps or their indices missing"]
        aggregates.append({"column": field, "html_selector": "td." + selector,
                           "repository_label_or_comment": description,
                           "repository_operation": "copy provider-displayed integer; no local aggregation",
                           "formal_aggregation_verified": False, "surface_separation_verified": False,
                           "public_description_available": True,
                           "public_same_surface_rule": "among most recent five starts, not five most recent same-surface starts; FAQ warning exception unresolved" if field == "avg_5_index" else None,
                           "missing_value": None, "unresolved_semantics": unknowns})
    return {
        "schema_version": "external-speed-figure-formula-audit-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "RECONSTRUCTION_VALIDATED": False,
        "classification": "B", "classification_meaning": "external index values directly scraped and stored",
        "classification_scope": "identified repository acquisition pipeline; individual DB rows lack source/hash/version provenance",
        "not_classification_A": "no local one-start speed figure formula in identified writer path",
        "not_classification_C": "seven provider aggregates are copied; downstream last-minus-previous is a separate derived feature, not how speed_figures is generated",
        "provider": {"url_template": required[0], "table": "SpeedIndex_Table", "row_classes_required": ["HorseList", "List"],
                     "minimum_td_count": 13, "requires_login_flag": True, "source_encoding": "euc-jp errors=replace",
                     "http_non200_or_exception": "empty record list", "short_html_threshold": 5000,
                     "short_html_measure": "decoded Python string length, despite old log saying bytes",
                     "raw_response_saved_by_this_function": False, "response_timestamp_recorded": False},
        "single_start_formula": {"available": False, "expression": None, "raw_required_fields": None,
                                 "constants": None, "weights": None, "distance_or_surface_correction": None,
                                 "going_or_track_variant": None, "time_standard": None, "rounding": None,
                                 "input_missingness_policy": None, "reason": "provider formula not implemented or specified in the recovered repository source"},
        "display_parser": {"numeric_type": "integer", "remove_first_sort_hidden_span": True, "strip_trailing_asterisks": True,
                           "missing_tokens": ["", "-", "未", "**", "***"],
                           "unparseable_values": None, "numeric_zero": "kept as genuine displayed zero, never substituted for missing",
                           "rounding": "none locally; decimal text rejected instead of rounded",
                           "missing_reason_recoverable_from_DB": False, "stripped_footnote_recoverable_from_DB": False,
                           "asterisk_marker_caveat": "legacy comment calls this a course marker; public average FAQ uses a warning symbol. Exact symbol/version mapping not proven; do not infer original condition support from the stored integer."},
        "aggregates": aggregates,
        "storage": {"key": ["race_id", "horse_number"], "mode": "INSERT OR REPLACE",
                    "horse_id_stored": False, "history_source_race_id_stored": False,
                    "source_date_stored": False, "provider_timestamp_stored": False,
                    "created_at_meaning": "SQLite insertion/replacement timestamp; not historical availability",
                    "provider_formula_version_stored": False, "raw_html_hash_stored": False},
        "legacy_feature_usage": {"join_keys": ["race_id", "horse_number"], "duplicate_policy": "keep first",
                                 "source_history_cutoff_checked": False, "avg_5_index_used": False,
                                 "copied_columns": ["index_last", "index_2ago", "index_3ago", "max_index", "course_max_index", "dist_max_index"],
                                 "only_local_derived_formula": "sf_index_trend = index_last - index_2ago if both nonmissing else NaN"},
        "current_feature_usage": {"archived_producer_exists_in_current_tree": current_producer_exists,
                                  "speed_figures_df": "accepted compatibility argument but ignored",
                                  "separate_non_equivalent_metrics": ["prev_speed_index = distance/time_seconds", "speed_deviation = training-baseline-normalized metres per second"],
                                  "using_these_as_external_index_replacement_permitted": False},
        "required_point_in_time_contract": {"history_date_strictly_before_target_date": True, "same_day_without_start_order_excluded": True,
                                            "future_outcome_normalization_forbidden": True, "local_missing_value": "NaN, never zero-fill",
                                            "required_missing_reasons": ["no_history", "insufficient_history", "raw_field_missing", "condition_history_missing", "formula_unavailable"],
                                            "current_reconstruction_missing_reason": "formula_unavailable",
                                            "infer_no_history_from_missing_provider_index_permitted": False},
        "reconstruction": {"RECONSTRUCTION_VALIDATED": False, "single_start_records_created": 0, "aggregate_features_created": 0,
                           "comparison_2026_performed": False, "comparison_pairs": 0, "exact_match_rate": None, "MAE": None,
                           "RMSE": None, "correlation": None, "max_absolute_difference": None,
                           "discrepancy_cause": None, "comparison_blocker": "formula_unavailable and missing per-start source linkage",
                           "evaluation_allowed": False, "status": "STOP_FORMULA_UNAVAILABLE",
                           "no_proxy_formula_created": True, "no_retraining": True},
        "evidence": evidence,
        "official_public_sources": OFFICIAL_SOURCES,
        "historical_specification_caveat": "Official pages were reviewed in 2026. They document current descriptions and do not prove which exact version/exception rules produced each 2013-2022 historical value.",
        "public_formula_search_conclusion": "No reproducible single-start formula found in the inspected official pages and recovered source. This is not a claim that the provider has never published one or that it is confidential.",
        "source_objects": {path: {key: value for key, value in item.items() if key != "text"} for path, item in sources.items()},
        "search_scope": {"working_tree": "targeted hidden/no-ignore source search excluding dependencies, binaries and generated model outputs",
                         "historical_git_object": REVISION, "formula_absence_claim": "recovered producer/storage/consumer path, not a claim to know external proprietary internals",
                         "network_data_collection_requests": 0, "official_documentation_browsed": True},
    }
