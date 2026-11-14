"""Minimal supplementary sources for historical JRA acquisition, not inference."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import re
import time

from app_config import logger
from scraping.acquisition_horse_history import has_history_table, prepare_history
from scraping.acquisition_scope import is_jra_race_id
from scraping.acquisition_http_policy import fetch_source_text
from scraping.fetch_pipeline import FetchResult, record_fetch_metric
from scraping.horse_history_ajax import history_url
from scraping.parsed_cache import collect_sources, get_parsed, parser_version, put_parsed
from scraping.pedigree_identity import parse_parent_identities
from scraping.saved_horse_reuse import _present


PUBLIC_INPUTS_VERSION = "historical-jra-required-public-v1"
_ACTIVE: ContextVar[bool] = ContextVar("required_public_acquisition", default=False)
_PARENTS = ("sire", "dam", "damsire")


@contextmanager
def required_public_acquisition():
    token = _ACTIVE.set(True)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def required_public_target(
    horse_id: str, target_date: str | None, race_id: str | None = None,
) -> bool:
    if (not _ACTIVE.get() or not re.fullmatch(r"[0-9A-Za-z]{10}", str(horse_id))
            or str(horse_id).startswith("B") or not is_jra_race_id(race_id, target_date)):
        return False
    try:
        target = datetime.strptime(target_date or "", "%Y%m%d").date()
    except ValueError:
        return False
    return target < datetime.now().date() - timedelta(days=30)


def public_input_capabilities() -> dict:
    return {
        "required_public_inputs_version": PUBLIC_INPUTS_VERSION,
        "required_public_inputs_scope": "historical_jra_older_than_30_days",
        "profile_totals_are_model_inputs": False,
        "prior_results_auto_imported": False,
    }


def _version() -> str:
    return parser_version(__file__) + parser_version(
        str(Path(__file__).with_name("pedigree_identity.py"))
    ) + parser_version(str(Path(__file__).with_name("acquisition_horse_history.py")))


def _fetch_metadata(response: FetchResult) -> dict:
    return {
        "http_status": response.status,
        "transport_source": response.source,
        "attempts": response.attempts,
        "error": response.error,
    }


async def _history_source(session, url: str, resume_key: str, **kwargs):
    response, body = await fetch_source_text(
        session, url, cache_ttl_sec=6 * 60 * 60,
        resume_key=resume_key, allow_redirects=False, **kwargs,
    )
    snapshot = response.cache_snapshot
    if (response.source in {"cache", "resume-cache"} and snapshot is not None
            and snapshot["fetched_at"] < time.time() - 6 * 60 * 60):
        # A body cached by an older seven-day profile caller must not make a
        # fresh Ajax table appear to have complete, matching lifetime coverage.
        return await fetch_source_text(
            session, url, cache_ttl_sec=6 * 60 * 60,
            resume_key=resume_key, allow_redirects=False, force_refresh=True, **kwargs,
        )
    return response, body


async def _pedigree(session, horse_id: str) -> dict:
    version = _version()
    cached = get_parsed("required-public-pedigree", horse_id, version)
    if cached is not None:
        record_fetch_metric("required_public_pedigree_reuse")
        return cached
    url = f"https://db.netkeiba.com/horse/ped/{horse_id}/"
    source = {"url": url, "child_id": horse_id, "status": "unresolved",
              "historical_prestart_capture_proven": False}
    result = {"_pedigree_source": source}
    with collect_sources() as sources:
        response, html = await fetch_source_text(
            session, url, cache_ttl_sec=30 * 24 * 60 * 60,
            resume_key=f"horse:{horse_id}:required-pedigree", allow_redirects=False,
        )
        source["source_snapshot"] = response.cache_snapshot
        source["fetch"] = _fetch_metadata(response)
        if response.status != 200:
            source["reason"] = f"pedigree-http-{response.status}"
        else:
            try:
                parents = parse_parent_identities(html, horse_id, url)
            except ValueError as exc:
                source["reason"] = str(exc)
            else:
                for field in _PARENTS:
                    result[field] = parents[field]["name"]
                    result[field + "_id"] = parents[field]["horse_id"]
                source["status"] = "validated"
    if source["status"] != "validated":
        logger.warning("Required pedigree unresolved: horse=%s reason=%s",
                       horse_id, source["reason"])
    # An archived empty/rejected response remains unresolved, but need not be
    # reparsed at every appearance. Source refresh invalidates this entry.
    put_parsed("required-public-pedigree", horse_id, version, result, sources,
               max_age_sec=30 * 24 * 60 * 60 if source["status"] == "validated" else 6 * 60 * 60)
    return result


async def scrape_required_horse_inputs(
    session, horse_id: str, target_date: str, parse_fields, current_pedigree: dict | None = None,
) -> dict:
    """Keep legacy strict-past fields, not present-day totals or biography."""
    url = f"https://db.netkeiba.com/horse/{horse_id}/"
    history = {
        "status": "unresolved", "target_date": target_date, "profile_url": url,
        "prior_results_imported": False,
    }
    result = {"_career_history_source": history}
    response, profile = await _history_source(
        session, url, f"horse:{horse_id}:required-profile",
    )
    history["profile_source_snapshot"] = response.cache_snapshot
    history["profile_fetch"] = _fetch_metadata(response)
    if response.status != 200:
        history["error"] = f"profile-http-{response.status}"
    else:
        try:
            # Verify the child before spending an Ajax request.
            from scraping.acquisition_horse_history import validate_profile_child
            validate_profile_child(profile, horse_id)
            ajax = None
            if not has_history_table(profile):
                ajax_url = history_url(horse_id)
                history["history_url"] = ajax_url
                ajax_response, ajax = await _history_source(
                    session, ajax_url, f"horse:{horse_id}:career-ajax", max_retries=1,
                )
                history["history_source_snapshot"] = ajax_response.cache_snapshot
                history["history_fetch"] = _fetch_metadata(ajax_response)
                if ajax_response.status != 200:
                    raise ValueError(f"career-ajax-http-{ajax_response.status}")
            strict_html, audit = prepare_history(profile, ajax, horse_id, target_date)
            history.update(audit)
        except ValueError as exc:
            history["error"] = str(exc)
        else:
            from scraping.horse import _HTML_PARSERS
            parsed = await asyncio.get_running_loop().run_in_executor(
                _HTML_PARSERS, parse_fields, strict_html, horse_id,
            )
            result.update({key: value for key, value in parsed["fields"].items()
                           if key.startswith(("prev_", "prev2_"))})
            record_fetch_metric("required_public_history_validated")
    if history["status"] != "validated":
        logger.warning("Required career unresolved: horse=%s target=%s reason=%s",
                       horse_id, target_date, history["error"])
    pedigree = await _pedigree(session, horse_id)
    conflicts = [
        field for parent in _PARENTS for field in (parent, parent + "_id")
        if _present((current_pedigree or {}).get(field)) and field in pedigree
        and (current_pedigree or {})[field] != pedigree[field]
    ]
    if conflicts:
        source = {**pedigree["_pedigree_source"], "status": "conflict",
                  "reason": "known-parent-conflict:" + ",".join(conflicts),
                  "observed_parents": {field: pedigree[field] for parent in _PARENTS
                                       for field in (parent, parent + "_id") if field in pedigree}}
        result["_pedigree_source"] = source
        logger.warning("Required pedigree conflict: horse=%s fields=%s", horse_id, conflicts)
    else:
        result.update(pedigree)
    result["_required_public_inputs"] = {
        "policy": PUBLIC_INPUTS_VERSION,
        "history_source_status": history["status"],
        "public_career_coverage_verified": (
            history["status"] == "validated"
            and history.get("coverage_matches_published_career_count") is True
            and history.get("prior_race_links_complete") is True
            and history.get("unknown_prior_start_status_rows") == 0
        ),
        "pedigree_source_status": result["_pedigree_source"]["status"],
        "prior_results_imported": False,
        "profile_totals_are_model_inputs": False,
    }
    return result
