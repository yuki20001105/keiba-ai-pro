from __future__ import annotations

import asyncio
import time
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Awaitable, Callable

from scraping.downloader import fetch_db_race_list_html, fetch_race_list_sub_html
from scraping.event_bus import EventBus, EventCounter, LoggerEventHandler
from scraping.factories import (
    parser_factory,
    repository_factory,
    transformer_factory,
    validator_factory,
)
from scraping.job_queue import mark_date_status
from scraping.pipeline_factory import PipelineFactory
from scraping.recovery_engine import POLICY_ABORT, POLICY_RETRY, resolve_recovery
from scraping.storage import _save_scraped_date_sqlite
from scraping.task_factory import TaskFactory
from scraping.task_models import ScrapeTask
from scraping.task_queue_factory import TaskQueueFactory
from scraping.task_runner import TaskRunner
from scraping.validators.rule_engine import validate_race_ids_by_rules


async def run_scraping_pipeline(
    *,
    job_id: str,
    job: dict,
    dates: list[str],
    force_rescrape: bool,
    scraped_dates: set,
    ultimate_db: Path,
    jobs_db_path: Path,
    start_time: float,
    calendar_was_blocked: bool,
    job_params: dict,
    cancel_flags: dict[str, bool],
    persist_job: Callable[[str, dict], None],
    log_scrape_event: Callable[..., None],
    scrape_and_save_race: Callable[..., Awaitable[int]],
    jitter: Callable[[float, float], float],
    new_session: Callable[[], Any],
    login_netkeiba: Callable[[Any], Awaitable[bool]],
    logger: Any,
    session_rotate_days: int,
    cooldown_every_days: int,
    pre_sleep_recent: float,
    pre_sleep_old: float,
    inter_race_sleep_base: float,
    post_sleep_recent: float,
    post_sleep_old: float,
    block_threshold: int = 15,
) -> dict:
    total = len(dates)
    counter = {"races": 0, "horses": 0}
    counter_lock = asyncio.Lock()
    consecutive_block_count = 0
    failed_days = 0
    total_errors = 0
    task_totals = {"TOTAL": 0, "SUCCESS": 0, "FAILED": 0, "SKIP": 0}
    severity_totals = {"INFO": 0, "WARNING": 0, "ERROR": 0, "FATAL": 0}
    policy_totals = {"RETRY": 0, "SKIP": 0, "ABORT": 0, "CONTINUE": 0}
    quality_counts: dict[str, int] = {}
    lineage_records: list[dict[str, Any]] = []

    event_bus = EventBus()
    event_counter = EventCounter()
    event_bus.subscribe("*", event_counter.on_event)
    event_bus.subscribe("*", LoggerEventHandler(logger).on_event)

    def _publish(name: str, payload: dict[str, Any] | None = None) -> None:
        event_bus.publish(name, payload or {})

    parse_db_list = parser_factory("db_race_list")
    parse_race_sub = parser_factory("race_list_sub")
    validate_race_ids = validator_factory("race_ids")
    transform_race_ids = transformer_factory("race_ids")
    _repo = repository_factory("scraping", db_path=ultimate_db)
    domain_factory = PipelineFactory()
    race_pipeline = domain_factory.get("race")
    task_factory = TaskFactory(race_pipeline=race_pipeline)
    queue_factory = TaskQueueFactory()

    session = new_session()
    try:
        _publish("pipeline.started", {"job_id": job_id, "total_dates": total})
        oikiri_enabled = await login_netkeiba(session)
        if oikiri_enabled:
            logger.info("調教タイム取得: 有効（プレミアム会員ログイン済み）")
        else:
            logger.info("調教タイム取得: スキップ（NETKEIBA_EMAIL/PASSWORD 未設定またはログイン失敗）")
        await asyncio.sleep(jitter(1.5))

        job_pedigree_cache: dict = {}

        for i, date in enumerate(dates):
            _publish("date.started", {"job_id": job_id, "date": date, "index": i + 1, "total": total})
            if cancel_flags.get(job_id):
                logger.info(f"ジョブ {job_id} キャンセル要求を検知 → 中断")
                _publish("pipeline.cancelled", {"job_id": job_id, "date": date, "index": i + 1})
                job["status"] = "cancelled"
                job["progress"]["message"] = f"キャンセル済み ({i}/{total}日目)"
                persist_job(job_id, job)
                return {
                    "cancelled": True,
                    "saved_races": counter["races"],
                    "saved_horses": counter["horses"],
                    "failed_days": failed_days,
                    "total_errors": total_errors,
                    "processed_dates": i,
                    "event_counts": event_counter.snapshot(),
                }

            mark_date_status(jobs_db_path, date, "RUNNING", job_id=job_id, bump_attempts=True)

            if i > 0 and i % session_rotate_days == 0:
                logger.info(f"セッションローテーション ({i}/{total}日目) → UA・Cookie・コネクション一新")
                await session.close()
                await asyncio.sleep(jitter(20.0, ratio=0.2))
                session = new_session()
                oikiri_enabled = await login_netkeiba(session)
                await asyncio.sleep(jitter(2.0))
                consecutive_block_count = 0

            if i > 0 and i % cooldown_every_days == 0:
                logger.info(f"クールダウン ({i}/{total}日目) → 5分間休憩（レートリミット解除待ち）")
                await asyncio.sleep(300.0)

            errors: list[str] = []
            days_ago = (date_cls.today() - date_cls(int(date[:4]), int(date[4:6]), int(date[6:8]))).days
            is_recent = days_ago <= 30
            pre_sleep = jitter(pre_sleep_recent if is_recent else pre_sleep_old)
            inter_race_sleep = jitter(inter_race_sleep_base)
            post_sleep = jitter(post_sleep_recent if is_recent else post_sleep_old)
            logger.debug(
                f"{date} [{i+1}/{total}]: "
                f"pre_sleep={pre_sleep:.2f}s inter_race={inter_race_sleep:.2f}s post_sleep={post_sleep:.2f}s"
                f" is_recent={is_recent}"
            )

            if date in scraped_dates:
                logger.info(f"{date}: 取得済み（scraped_dates）→ スキップ")
                _publish("date.skipped", {"job_id": job_id, "date": date, "reason": "already_scraped"})
                mark_date_status(jobs_db_path, date, "SKIP", job_id=job_id)
                job["progress"] = {
                    "done": i + 1,
                    "total": total,
                    "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (スキップ含む)",
                    "saved_races": counter["races"],
                    "saved_horses": counter["horses"],
                    "params": job_params,
                }
                continue

            day_races_before = counter["races"]
            race_ids: list[str] = []
            ip_blocked = False
            lineage: dict[str, Any] = {
                "date": date,
                "downloader_ids": 0,
                "parser_ids": 0,
                "validator_in": 0,
                "validator_out": 0,
                "tasks_total": 0,
                "tasks_success": 0,
                "tasks_failed": 0,
                "tasks_skip": 0,
                "repository_saved": 0,
                "download_time_sec": 0.0,
                "parse_time_sec": 0.0,
                "validate_time_sec": 0.0,
                "insert_time_sec": 0.0,
                "cache_hit": 0,
                "parser_version": "race_parser_v2",
                "rule_version": "rules.yaml_v1",
            }

            try:
                await asyncio.sleep(pre_sleep)

                _t_dl = time.perf_counter()
                dl_result = await fetch_db_race_list_html(session, date, use_cache=not force_rescrape)
                lineage["download_time_sec"] += float(time.perf_counter() - _t_dl)
                if dl_result.status_code == 200 and dl_result.html:
                    lineage["cache_hit"] = 1 if dl_result.cache_hit else 0
                    _t_parse = time.perf_counter()
                    race_ids = parse_db_list(dl_result.html)
                    lineage["parse_time_sec"] += float(time.perf_counter() - _t_parse)
                    lineage["downloader_ids"] = len(race_ids)
                    _publish("race_ids.downloaded", {"job_id": job_id, "date": date, "count": len(race_ids), "cache_hit": int(dl_result.cache_hit)})
                    consecutive_block_count = 0
                    if dl_result.cache_hit:
                        logger.info(f"{date}: HTML cache hit ({len(race_ids)} race ids)")
                elif dl_result.status_code == 400:
                    body_len = len(dl_result.html or "")
                    if body_len == 0:
                        consecutive_block_count += 1
                        if calendar_was_blocked:
                            logger.info(
                                f"{date}: HTTP 400 空 → IPブロック中のためスキップ"
                                f"（連続{consecutive_block_count}日）"
                            )
                        else:
                            logger.info(f"{date}: HTTP 400 空レスポンス → 非開催日としてスキップ")
                        log_scrape_event(
                            job_id,
                            "http_400",
                            date_processing=date,
                            pre_sleep_actual=pre_sleep,
                            inter_race_sleep_actual=inter_race_sleep,
                            post_sleep_actual=post_sleep,
                            is_recent=is_recent,
                            consecutive_400_count=consecutive_block_count,
                            races_scraped=counter["races"],
                            days_scraped=i,
                            note="calendar_blocked" if calendar_was_blocked else "possible_non_race_day",
                        )
                        if consecutive_block_count >= block_threshold:
                            ip_blocked = True
                            logger.error(
                                f"連続 {consecutive_block_count} 日 HTTP400 → IPブロック判定。"
                                f" VPNのIP変更後に再実行してください。 最終処理日付: {date}"
                            )
                            log_scrape_event(
                                job_id,
                                "forced_stop",
                                date_processing=date,
                                pre_sleep_actual=pre_sleep,
                                inter_race_sleep_actual=inter_race_sleep,
                                post_sleep_actual=post_sleep,
                                is_recent=is_recent,
                                consecutive_400_count=consecutive_block_count,
                                races_scraped=counter["races"],
                                days_scraped=i,
                                note=f"consecutive_400_threshold_{block_threshold}",
                            )
                            raise RuntimeError(
                                f"HTTP 400 連続 {consecutive_block_count} 日 → IPブロック検知。"
                                f" VPNのIP変更後に再実行してください。 最終処理日付: {date}"
                            )
                    else:
                        consecutive_block_count = 0
                        logger.info(f"{date}: HTTP 400 → 未開催または削除済み日付 ({body_len}B)")
                elif dl_result.status_code in (403, 429, 503):
                    ip_blocked = True
                    _publish("date.blocked", {"job_id": job_id, "date": date, "status_code": dl_result.status_code})
                    logger.error(
                        f"{date}: HTTP {dl_result.status_code} → IPブロック/アクセス拒否。"
                        f" VPNのIP変更後に再実行してください。 最終処理日付: {date}"
                    )
                    raise RuntimeError(
                        f"HTTP {dl_result.status_code} IPブロック検知 → 即停止。"
                        f" 最終処理日付: {date}"
                    )
                else:
                    logger.warning(f"{date}: db.netkeiba.com HTTP {dl_result.status_code} / {dl_result.error}")

                if not race_ids and is_recent:
                    try:
                        _t_dl_sub = time.perf_counter()
                        dl_sub = await fetch_race_list_sub_html(date)
                        lineage["download_time_sec"] += float(time.perf_counter() - _t_dl_sub)
                        if dl_sub.status_code == 200 and dl_sub.html:
                            _t_parse_sub = time.perf_counter()
                            race_ids = parse_race_sub(dl_sub.html)
                            lineage["parse_time_sec"] += float(time.perf_counter() - _t_parse_sub)
                            lineage["downloader_ids"] = len(race_ids)
                            _publish("race_ids.downloaded", {"job_id": job_id, "date": date, "count": len(race_ids), "source": "race_list_sub"})
                            logger.info(f"{date}: race.netkeiba.com から {len(race_ids)} レースID検出 (race_list_sub)")
                        else:
                            logger.warning(
                                f"{date}: レース一覧 HTTP {dl_sub.status_code} (db/race 両方失敗) → スキップ"
                            )
                            job["progress"] = {
                                "done": i + 1,
                                "total": total,
                                "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (HTTP {dl_sub.status_code}スキップ)",
                                "saved_races": counter["races"],
                                "saved_horses": counter["horses"],
                                "params": job_params,
                            }
                            if dl_sub.status_code in (403, 429, 503):
                                logger.warning(f"{date}: HTTP {dl_sub.status_code} → 60秒待機（IPブロック回避）")
                                await asyncio.sleep(60.0)
                            mark_date_status(jobs_db_path, date, "FAILED", job_id=job_id, error=f"race_list_sub_http_{dl_sub.status_code}")
                            continue
                    except Exception as exc:
                        logger.warning(f"{date}: race.netkeiba.com 取得失敗: {exc}")

                _t_parse_norm = time.perf_counter()
                race_ids = transform_race_ids(race_ids)
                lineage["parse_time_sec"] += float(time.perf_counter() - _t_parse_norm)
                lineage["parser_ids"] = len(race_ids)
                lineage["validator_in"] = len(race_ids)
                _t_val = time.perf_counter()
                valid = validate_race_ids(race_ids)
                if not valid.ok:
                    logger.warning(f"{date}: race_id 検証失敗 -> {valid.reason}")
                    code = valid.error_code or "E099"
                    quality_counts[code] = int(quality_counts.get(code, 0)) + 1
                    decision = resolve_recovery(code)
                    severity_totals[decision.severity] = int(severity_totals.get(decision.severity, 0)) + 1
                    policy_totals[decision.policy] = int(policy_totals.get(decision.policy, 0)) + 1
                    _publish(
                        "validation.failed",
                        {"job_id": job_id, "date": date, "error_code": code, "severity": decision.severity, "policy": decision.policy},
                    )
                    if decision.policy == POLICY_ABORT:
                        raise RuntimeError(f"FATAL validation error ({code}): {valid.reason}")
                    if decision.policy == POLICY_RETRY:
                        # Self-healing path: bypass cache and re-parse once.
                        re_dl = await fetch_db_race_list_html(session, date, use_cache=False)
                        _publish("recovery.refetch", {"job_id": job_id, "date": date, "reason": code, "source": "db_race_list"})
                        if re_dl.status_code == 200 and re_dl.html:
                            race_ids = transform_race_ids(parse_db_list(re_dl.html))
                        else:
                            race_ids = []
                    else:
                        race_ids = []

                rules_valid = validate_race_ids_by_rules(race_ids)
                if not rules_valid.ok:
                    logger.warning(f"{date}: rule engine 検証失敗 -> {rules_valid.reason}")
                    code = rules_valid.error_code or "E099"
                    quality_counts[code] = int(quality_counts.get(code, 0)) + 1
                    decision = resolve_recovery(code)
                    severity_totals[decision.severity] = int(severity_totals.get(decision.severity, 0)) + 1
                    policy_totals[decision.policy] = int(policy_totals.get(decision.policy, 0)) + 1
                    _publish(
                        "validation.rule_failed",
                        {"job_id": job_id, "date": date, "error_code": code, "severity": decision.severity, "policy": decision.policy},
                    )
                    if decision.policy == POLICY_ABORT:
                        raise RuntimeError(f"FATAL rule validation error ({code}): {rules_valid.reason}")
                    if decision.policy == POLICY_RETRY:
                        re_dl = await fetch_db_race_list_html(session, date, use_cache=False)
                        _publish("recovery.refetch", {"job_id": job_id, "date": date, "reason": code, "source": "db_race_list"})
                        if re_dl.status_code == 200 and re_dl.html:
                            race_ids = transform_race_ids(parse_db_list(re_dl.html))
                        else:
                            race_ids = []
                    else:
                        race_ids = []
                lineage["validate_time_sec"] += float(time.perf_counter() - _t_val)
                lineage["validator_out"] = len(race_ids)

                logger.info(
                    f"{date} [{i+1}/{total}]: {len(race_ids)}レースID検出"
                    + (" (race_list_sub)" if not race_ids else "")
                )

                task_queue = queue_factory.create(
                    db_path=jobs_db_path,
                    job_id=job_id,
                    queue_name=f"race:{date}",
                )
                for idx, race_id in enumerate(race_ids):
                    task_queue.enqueue(
                        ScrapeTask(
                            task_id=f"{job_id}:{date}:race:{idx}:{race_id}",
                            task_type="race",
                            payload={"race_id": race_id, "date": date},
                            max_attempts=2,
                        )
                    )

                runner = TaskRunner(retry_backoff_seconds=(3.0, 10.0, 30.0))

                async def _task_handler(task: ScrapeTask) -> dict[str, Any]:
                    _publish("task.started", {"job_id": job_id, "date": date, "task_id": task.task_id, "task_type": task.task_type})
                    executor = task_factory.get_executor(task.task_type)
                    result = await executor.execute(
                        task,
                        {
                            "session": session,
                            "db_path": _repo.db_path,
                            "oikiri_enabled": oikiri_enabled,
                            "force_rescrape": bool(force_rescrape or task.meta.get("invalidate_cache")),
                            "errors": errors,
                            "scrape_and_save_race": scrape_and_save_race,
                            "pedigree_cache": job_pedigree_cache,
                        },
                    )
                    if result.get("ok") and not result.get("skip"):
                        saved_horses = int(result.get("saved_horses", 0))
                        async with counter_lock:
                            counter["races"] += 1
                            counter["horses"] += saved_horses
                            job["progress"] = {
                                "done": i,
                                "total": total,
                                "message": f"{i}/{total}日処理中 | {counter['races']}レース・{counter['horses']}頭保存済み",
                                "saved_races": counter["races"],
                                "saved_horses": counter["horses"],
                                "params": job_params,
                            }
                    return result

                _t_task = time.perf_counter()
                task_stats = await runner.run(
                    task_queue,
                    _task_handler,
                    event_publisher=lambda n, p: _publish(n, {"job_id": job_id, "date": date, **(p or {})}),
                )
                lineage["insert_time_sec"] += float(time.perf_counter() - _t_task)
                task_totals["TOTAL"] += int(task_stats.get("TOTAL", 0))
                task_totals["SUCCESS"] += int(task_stats.get("SUCCESS", 0))
                task_totals["FAILED"] += int(task_stats.get("FAILED", 0))
                task_totals["SKIP"] += int(task_stats.get("SKIP", 0))
                severity_totals["INFO"] += int(task_stats.get("SEVERITY_INFO", 0))
                severity_totals["WARNING"] += int(task_stats.get("SEVERITY_WARNING", 0))
                severity_totals["ERROR"] += int(task_stats.get("SEVERITY_ERROR", 0))
                severity_totals["FATAL"] += int(task_stats.get("SEVERITY_FATAL", 0))
                policy_totals["RETRY"] += int(task_stats.get("POLICY_RETRY", 0))
                policy_totals["SKIP"] += int(task_stats.get("POLICY_SKIP", 0))
                policy_totals["ABORT"] += int(task_stats.get("POLICY_ABORT", 0))
                policy_totals["CONTINUE"] += int(task_stats.get("POLICY_CONTINUE", 0))
                lineage["tasks_total"] = int(task_stats.get("TOTAL", 0))
                lineage["tasks_success"] = int(task_stats.get("SUCCESS", 0))
                lineage["tasks_failed"] = int(task_stats.get("FAILED", 0))
                lineage["tasks_skip"] = int(task_stats.get("SKIP", 0))

                if int(task_stats.get("FAILED", 0)) > 0:
                    failed_days += 1
                    total_errors += int(task_stats.get("FAILED", 0))
                if int(task_stats.get("ABORTED", 0)) > 0:
                    _publish("task.abort", {"job_id": job_id, "date": date})

                if errors:
                    logger.warning(f"{date}: エラー一覧: {errors[:5]}")

            except RuntimeError:
                mark_date_status(jobs_db_path, date, "FAILED", job_id=job_id, error="runtime_blocked")
                _publish("date.failed", {"job_id": job_id, "date": date, "error": "runtime_blocked"})
                raise
            except Exception as exc:
                logger.error(f"ジョブ {job_id} {date} エラー: {exc}")
                failed_days += 1
                total_errors += 1
                mark_date_status(jobs_db_path, date, "FAILED", job_id=job_id, error=str(exc))
                _publish("date.failed", {"job_id": job_id, "date": date, "error": str(exc)})

            day_saved = counter["races"] - day_races_before
            lineage["repository_saved"] = int(day_saved)
            logger.info(
                f"{date} [{i+1}/{total}] 完了: 検出={len(race_ids)} 保存={day_saved} エラー={len(errors)}"
                + (f"  errors={errors[:3]}" if errors else "")
            )
            job["progress"] = {
                "done": i + 1,
                "total": total,
                "message": f"{i+1}/{total}日処理済み / {counter['races']}レース保存 (errors:{len(errors)})",
                "saved_races": counter["races"],
                "saved_horses": counter["horses"],
                "last_date": date,
                "last_errors": errors[-3:] if errors else [],
                "params": job_params,
            }

            if len(errors) > 0:
                total_errors += len(errors)

            try:
                date_age_days = (date_cls.today() - date_cls(int(date[:4]), int(date[4:6]), int(date[6:8]))).days
                if ip_blocked:
                    logger.info(f"{date}: IPブロック疑い（{day_saved}件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）")
                elif calendar_was_blocked and day_saved == 0:
                    logger.info(f"{date}: カレンダーブロック中で0件 → no_race=1 を記録しない（再スクレイプ可能）")
                elif day_saved == 0 and date_age_days < 7:
                    logger.info(f"{date}: 直近7日以内で0件保存 → no_race=1 を記録しない（再スクレイプ可能）")
                else:
                    await asyncio.to_thread(_save_scraped_date_sqlite, ultimate_db, date, day_saved)
            except Exception:
                pass

            persist_job(job_id, job)

            if errors:
                failed_days += 1
                mark_date_status(jobs_db_path, date, "FAILED", job_id=job_id, error=(errors[-1] if errors else None))
                _publish("date.failed", {"job_id": job_id, "date": date, "error": errors[-1] if errors else "unknown"})
            elif day_saved == 0 and len(race_ids) == 0:
                mark_date_status(jobs_db_path, date, "SKIP", job_id=job_id)
                _publish("date.skipped", {"job_id": job_id, "date": date, "reason": "no_race_ids"})
            else:
                mark_date_status(jobs_db_path, date, "SUCCESS", job_id=job_id)
                _publish("date.completed", {"job_id": job_id, "date": date, "saved": day_saved})

            if i < total - 1:
                await asyncio.sleep(post_sleep)

            lineage_records.append(lineage)

    finally:
        await session.close()

    elapsed = time.time() - start_time
    _publish("pipeline.completed", {"job_id": job_id, "processed_dates": len(dates), "saved_races": counter["races"]})
    return {
        "cancelled": False,
        "saved_races": counter["races"],
        "saved_horses": counter["horses"],
        "failed_days": failed_days,
        "total_errors": total_errors,
        "task_totals": task_totals,
        "severity_totals": severity_totals,
        "policy_totals": policy_totals,
        "quality_counts": quality_counts,
        "lineage_records": lineage_records,
        "event_counts": event_counter.snapshot(),
        "processed_dates": len(dates),
        "elapsed": elapsed,
    }
