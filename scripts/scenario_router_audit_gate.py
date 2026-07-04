from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_STEPS = [
    "health_check",
    "scenario_router_e2e",
    "scenario_router_auto_recovery_e2e",
    "py_compile",
    "import_smoke",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "baseline_window": 10,
    "min_baseline_samples": 5,
    "duration_warn_multiplier": 2.0,
    "duration_fail_multiplier": 4.0,
    "min_last_10_success_rate": 0.8,
    "fail_on_flaky": False,
    "fail_on_duration_spike": False,
    "strict": False,
    "required_steps": list(DEFAULT_REQUIRED_STEPS),
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = ln.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue
    return rows


def _compute_last10_success_rate(history: list[dict[str, Any]]) -> float:
    last = history[-10:]
    if not last:
        return 0.0
    ok = sum(1 for r in last if str(r.get("overall_status") or "") == "PASS")
    return ok / float(len(last))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _normalize_required_steps(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        raw = [x.strip() for x in value.split(",")]
        return [x for x in raw if x]
    return list(DEFAULT_REQUIRED_STEPS)


def _normalize_partial_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "baseline_window" in cfg:
        out["baseline_window"] = max(1, _safe_int(cfg.get("baseline_window"), DEFAULT_CONFIG["baseline_window"]))
    if "min_baseline_samples" in cfg:
        out["min_baseline_samples"] = max(
            1, _safe_int(cfg.get("min_baseline_samples"), DEFAULT_CONFIG["min_baseline_samples"])
        )
    if "duration_warn_multiplier" in cfg:
        out["duration_warn_multiplier"] = max(
            1.0, _safe_float(cfg.get("duration_warn_multiplier"), DEFAULT_CONFIG["duration_warn_multiplier"])
        )
    if "duration_fail_multiplier" in cfg:
        out["duration_fail_multiplier"] = max(
            1.0, _safe_float(cfg.get("duration_fail_multiplier"), DEFAULT_CONFIG["duration_fail_multiplier"])
        )
    if "min_last_10_success_rate" in cfg:
        out["min_last_10_success_rate"] = _safe_float(
            cfg.get("min_last_10_success_rate"), DEFAULT_CONFIG["min_last_10_success_rate"]
        )
    if "fail_on_flaky" in cfg:
        out["fail_on_flaky"] = _safe_bool(cfg.get("fail_on_flaky"), DEFAULT_CONFIG["fail_on_flaky"])
    if "fail_on_duration_spike" in cfg:
        out["fail_on_duration_spike"] = _safe_bool(
            cfg.get("fail_on_duration_spike"), DEFAULT_CONFIG["fail_on_duration_spike"]
        )
    if "strict" in cfg:
        out["strict"] = _safe_bool(cfg.get("strict"), DEFAULT_CONFIG["strict"])
    if "required_steps" in cfg:
        out["required_steps"] = _normalize_required_steps(cfg.get("required_steps"))
    return out


def _load_presets(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore

        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}
    presets_obj = raw.get("presets") if isinstance(raw.get("presets"), dict) else raw
    if not isinstance(presets_obj, dict):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for k, v in presets_obj.items():
        if not isinstance(v, dict):
            continue
        out[str(k)] = _normalize_partial_config(v)
    return out


def _resolve_effective_config(args: argparse.Namespace) -> tuple[str, dict[str, Any], str, list[str]]:
    preset_name = str(args.preset or "").strip()
    preset_file = Path(str(args.preset_file or "scripts/scenario_router_audit_gate_presets.yaml"))
    presets = _load_presets(preset_file)

    effective: dict[str, Any] = dict(DEFAULT_CONFIG)
    warnings: list[str] = []
    if not preset_file.exists():
        warnings.append(f"preset file not found: {preset_file}")

    if preset_name:
        if preset_name not in presets:
            known = ", ".join(sorted(presets.keys())) if presets else "none"
            raise ValueError(
                f"unknown preset '{preset_name}' (file: {preset_file}, available: {known})"
            )
        effective.update(presets[preset_name])

    cli_cfg: dict[str, Any] = {}
    if args.strict is not None:
        cli_cfg["strict"] = bool(args.strict)
    if args.min_last_10_success_rate is not None:
        cli_cfg["min_last_10_success_rate"] = args.min_last_10_success_rate
    if args.fail_on_flaky is not None:
        cli_cfg["fail_on_flaky"] = bool(args.fail_on_flaky)
    if args.baseline_window is not None:
        cli_cfg["baseline_window"] = args.baseline_window
    if args.duration_warn_multiplier is not None:
        cli_cfg["duration_warn_multiplier"] = args.duration_warn_multiplier
    if args.duration_fail_multiplier is not None:
        cli_cfg["duration_fail_multiplier"] = args.duration_fail_multiplier
    if args.min_baseline_samples is not None:
        cli_cfg["min_baseline_samples"] = args.min_baseline_samples
    if args.fail_on_duration_spike is not None:
        cli_cfg["fail_on_duration_spike"] = bool(args.fail_on_duration_spike)
    if args.required_step is not None:
        cli_cfg["required_steps"] = list(args.required_step)

    effective.update(_normalize_partial_config(cli_cfg))
    if not effective.get("required_steps"):
        effective["required_steps"] = list(DEFAULT_REQUIRED_STEPS)

    return (preset_name or "none"), effective, str(preset_file), warnings


def _result_step_durations(result: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in (result.get("steps") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "")
        if not name:
            continue
        out[name] = _safe_float(s.get("duration_sec"), 0.0)
    return out


def _history_step_durations(row: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in (row.get("steps") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("step_name") or "")
        if not name:
            continue
        out[name] = _safe_float(s.get("duration_sec"), 0.0)
    return out


def _last_history_is_current_run(history: list[dict[str, Any]], result: dict[str, Any]) -> bool:
    if not history:
        return False
    curr = _result_step_durations(result)
    if not curr:
        return False
    last = _history_step_durations(history[-1])
    if not last:
        return False
    shared = set(curr.keys()) & set(last.keys())
    if not shared:
        return False
    matched = 0
    for nm in shared:
        cv = _safe_float(curr.get(nm), 0.0)
        lv = _safe_float(last.get(nm), 0.0)
        if abs(cv - lv) <= 0.01:
            matched += 1
    return matched >= max(1, min(2, len(shared)))


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    q = max(0.0, min(100.0, float(p))) / 100.0
    s = sorted(float(x) for x in values)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return float(s[lo])
    frac = pos - lo
    return float(s[lo] * (1.0 - frac) + s[hi] * frac)


def _evaluate_duration_baseline(
    *,
    result: dict[str, Any],
    history: list[dict[str, Any]],
    baseline_window: int,
    duration_warn_multiplier: float,
    duration_fail_multiplier: float,
    min_baseline_samples: int,
) -> list[dict[str, Any]]:
    current = _result_step_durations(result)
    rows = list(history)
    if _last_history_is_current_run(rows, result):
        rows = rows[:-1]
    if baseline_window > 0 and len(rows) > baseline_window:
        rows = rows[-baseline_window:]

    samples_by_step: dict[str, list[float]] = {}
    for row in rows:
        step_map = _history_step_durations(row)
        for nm, dur in step_map.items():
            samples_by_step.setdefault(nm, []).append(_safe_float(dur, 0.0))

    out: list[dict[str, Any]] = []
    for nm, cur in current.items():
        vals = [x for x in samples_by_step.get(nm, []) if x >= 0]
        sample_count = len(vals)
        median = float(statistics.median(vals)) if vals else 0.0
        mean = float(statistics.mean(vals)) if vals else 0.0
        p95 = _percentile(vals, 95.0) if vals else 0.0
        warn_thr = median * float(duration_warn_multiplier) if median > 0 else 0.0
        fail_thr = median * float(duration_fail_multiplier) if median > 0 else 0.0

        status = "OK"
        note = ""
        if sample_count < int(min_baseline_samples):
            status = "INFO"
            note = "insufficient baseline samples"
        elif median <= 0:
            status = "INFO"
            note = "baseline median is zero; threshold is not reliable"
        elif cur > fail_thr:
            status = "FAIL"
        elif cur > warn_thr:
            status = "WARN"

        out.append(
            {
                "step_name": nm,
                "current_duration_sec": _safe_float(cur, 0.0),
                "sample_count": sample_count,
                "median_duration_sec": median,
                "mean_duration_sec": mean,
                "p95_duration_sec": p95,
                "warn_threshold_sec": warn_thr,
                "fail_threshold_sec": fail_thr,
                "duration_warn_multiplier": float(duration_warn_multiplier),
                "duration_fail_multiplier": float(duration_fail_multiplier),
                "status": status,
                "note": note,
            }
        )
    out.sort(key=lambda x: str(x.get("step_name") or ""))
    return out


def _compute_flaky(history: list[dict[str, Any]], result: dict[str, Any]) -> bool:
    trend = result.get("trend") if isinstance(result.get("trend"), dict) else {}
    if bool(trend.get("flaky_warning")):
        return True
    last5 = history[-5:]
    if last5:
        sts = {str(r.get("overall_status") or "") for r in last5}
        if ("PASS" in sts) and ("FAIL" in sts):
            return True
    return False


def _same_failure_recurring(history: list[dict[str, Any]]) -> tuple[bool, str]:
    last3 = history[-3:]
    fts = [str(r.get("failure_type") or "") for r in last3 if str(r.get("failure_type") or "") not in {"", "NONE"}]
    if not fts:
        return False, ""
    cnt = Counter(fts)
    top, n = cnt.most_common(1)[0]
    return (n >= 2), top


def _duration_spike(history: list[dict[str, Any]]) -> tuple[bool, str]:
    if len(history) < 2:
        return False, ""
    prev = history[-2]
    curr = history[-1]

    p_map: dict[str, float] = {}
    for s in (prev.get("steps") or []):
        if isinstance(s, dict):
            p_map[str(s.get("step_name") or "")] = float(s.get("duration_sec") or 0.0)

    for s in (curr.get("steps") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("step_name") or "")
        cv = float(s.get("duration_sec") or 0.0)
        pv = float(p_map.get(name) or 0.0)
        if pv > 0 and cv >= (pv * 2.0):
            return True, name
    return False, ""


def evaluate_gate(
    *,
    result: dict[str, Any],
    history: list[dict[str, Any]],
    strict: bool,
    min_last_10_success_rate: float,
    fail_on_flaky: bool,
    baseline_window: int,
    duration_warn_multiplier: float,
    duration_fail_multiplier: float,
    min_baseline_samples: int,
    fail_on_duration_spike: bool,
    required_steps: list[str],
) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    latest_status = str(summary.get("overall_status") or "")
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    sandbox = result.get("sandbox") if isinstance(result.get("sandbox"), dict) else {}
    trend = result.get("trend") if isinstance(result.get("trend"), dict) else {}
    stderr_summary = result.get("stderr_summary") if isinstance(result.get("stderr_summary"), dict) else {}

    if latest_status != "PASS":
        reasons.append("latest overall_status is not PASS")

    stderr_class = str(stderr_summary.get("classification") or "")
    stderr_is_noise = bool(stderr_summary.get("is_noise"))
    if stderr_class and stderr_class != "NO_STDERR":
        if stderr_is_noise:
            warnings.append(f"stderr classified as noise: {stderr_class}")
        else:
            warnings.append(f"stderr classified as real-error signal: {stderr_class}")

    if latest_status == "PASS" and stderr_class and not stderr_is_noise:
        # Keep PASS/WARN/FAIL semantics stable: stderr real-error signal alone should not fail gate
        # when structured status and required steps are successful, but should be visible as warning.
        warnings.append("structured result is PASS; treating stderr real-error signal as warning")

    required_steps_set = {str(x).strip() for x in required_steps if str(x).strip()}
    failed_steps: list[str] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        nm = str(s.get("name") or "")
        st = str(s.get("status") or "")
        if nm in required_steps_set and st != "PASS":
            failed_steps.append(nm)
    if failed_steps:
        reasons.append(f"required steps failed: {', '.join(failed_steps)}")

    cleanup_status = str(sandbox.get("cleanup_status") or "")
    sandbox_enabled = bool(sandbox.get("enabled"))
    if cleanup_status.startswith("cleanup_failed"):
        reasons.append("sandbox cleanup failed")
    elif sandbox_enabled and cleanup_status != "deleted":
        warnings.append(f"sandbox cleanup_status is {cleanup_status}")

    last_10_rate = float(trend.get("last_10_success_rate") or 0.0)
    if not trend and history:
        last_10_rate = _compute_last10_success_rate(history)
    if last_10_rate < float(min_last_10_success_rate):
        reasons.append(
            f"last_10_success_rate below threshold: {last_10_rate:.2%} < {float(min_last_10_success_rate):.2%}"
        )

    flaky_warning = bool(trend.get("flaky_warning")) if trend else _compute_flaky(history, result)
    if flaky_warning:
        if bool(fail_on_flaky) or bool(strict):
            reasons.append("flaky_warning=true under strict/fail-on-flaky policy")
        else:
            warnings.append("flaky_warning=true")

    recurring, recurring_ft = _same_failure_recurring(history)
    if recurring:
        warnings.append(f"same failure_type recurred in recent runs: {recurring_ft}")

    spike, spike_step = _duration_spike(history)
    if spike:
        warnings.append(f"step duration previous-run spike detected (>=2x, auxiliary): {spike_step}")

    baseline_evaluation: list[dict[str, Any]] = []
    try:
        baseline_evaluation = _evaluate_duration_baseline(
            result=result,
            history=history,
            baseline_window=int(baseline_window),
            duration_warn_multiplier=float(duration_warn_multiplier),
            duration_fail_multiplier=float(duration_fail_multiplier),
            min_baseline_samples=int(min_baseline_samples),
        )
        baseline_warn_steps = [str(x.get("step_name") or "") for x in baseline_evaluation if str(x.get("status") or "") == "WARN"]
        baseline_fail_steps = [str(x.get("step_name") or "") for x in baseline_evaluation if str(x.get("status") or "") == "FAIL"]
        if baseline_warn_steps:
            warnings.append(f"duration baseline warn threshold exceeded: {', '.join(baseline_warn_steps)}")
        if baseline_fail_steps:
            msg = f"duration baseline fail threshold exceeded: {', '.join(baseline_fail_steps)}"
            if bool(fail_on_duration_spike):
                reasons.append(msg)
            else:
                warnings.append(msg)
    except Exception as e:
        warnings.append(f"baseline evaluation skipped due to calculation error: {e}")

    if len(history) < 3:
        warnings.append("history size is small; reliability trend is not stable yet")

    gate_status = "PASS"
    exit_code = 0
    if reasons:
        gate_status = "FAIL"
        exit_code = 1
    elif warnings:
        gate_status = "WARN"

    triage = result.get("triage") if isinstance(result.get("triage"), dict) else {}
    suggested = str(triage.get("rerun_command") or "")
    if gate_status == "FAIL" and str(triage.get("likely_cause") or ""):
        suggested_next_action = f"{str(triage.get('likely_cause'))} | rerun: {suggested}"
    elif gate_status == "WARN":
        suggested_next_action = "Review warnings and keep monitoring trend before tightening strict mode."
    else:
        suggested_next_action = "Quality gate passed. Continue normal audit cadence."

    return {
        "gate_status": gate_status,
        "exit_code": exit_code,
        "reasons": reasons,
        "warnings": warnings,
        "latest_status": latest_status,
        "last_10_success_rate": last_10_rate,
        "flaky_warning": flaky_warning,
        "failed_steps": failed_steps,
        "baseline_evaluation": baseline_evaluation,
        "baseline_config": {
            "baseline_window": int(baseline_window),
            "duration_warn_multiplier": float(duration_warn_multiplier),
            "duration_fail_multiplier": float(duration_fail_multiplier),
            "min_baseline_samples": int(min_baseline_samples),
            "fail_on_duration_spike": bool(fail_on_duration_spike),
        },
        "suggested_next_action": suggested_next_action,
    }


def _render_md(data: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Scenario Router Audit Gate")
    lines.append("")
    lines.append(f"- gate_status: {data.get('gate_status', '')}")
    lines.append(f"- exit_code: {data.get('exit_code', 0)}")
    lines.append(f"- latest_status: {data.get('latest_status', '')}")
    lines.append(f"- last_10_success_rate: {float(data.get('last_10_success_rate', 0.0)):.2%}")
    lines.append(f"- flaky_warning: {bool(data.get('flaky_warning'))}")
    lines.append(f"- suggested_next_action: {data.get('suggested_next_action', '')}")
    cfg = data.get("effective_config") if isinstance(data.get("effective_config"), dict) else {}
    lines.append(f"- baseline_window: {int(cfg.get('baseline_window') or 0)}")
    lines.append(f"- duration_warn_multiplier: {float(cfg.get('duration_warn_multiplier') or 0.0):.2f}")
    lines.append(f"- duration_fail_multiplier: {float(cfg.get('duration_fail_multiplier') or 0.0):.2f}")
    lines.append(f"- min_baseline_samples: {int(cfg.get('min_baseline_samples') or 0)}")
    lines.append(f"- min_last_10_success_rate: {float(cfg.get('min_last_10_success_rate') or 0.0):.2%}")
    lines.append(f"- strict: {bool(cfg.get('strict'))}")
    lines.append(f"- fail_on_flaky: {bool(cfg.get('fail_on_flaky'))}")
    lines.append(f"- fail_on_duration_spike: {bool(cfg.get('fail_on_duration_spike'))}")
    lines.append("")
    lines.append("## Applied Preset")
    lines.append("")
    lines.append(f"- applied_preset: {str(data.get('applied_preset') or 'none')}")
    lines.append(f"- preset_file: {str(data.get('preset_file') or '')}")
    req = cfg.get("required_steps") if isinstance(cfg.get("required_steps"), list) else []
    lines.append("- required_steps:")
    if req:
        for x in req:
            lines.append(f"  - {str(x)}")
    else:
        lines.append("  - (none)")

    p_warn = data.get("preset_warnings") if isinstance(data.get("preset_warnings"), list) else []
    if p_warn:
        lines.append("- preset_warnings:")
        for x in p_warn:
            lines.append(f"  - {str(x)}")
    lines.append("")
    lines.append("## Reasons")
    lines.append("")
    reasons = data.get("reasons") if isinstance(data.get("reasons"), list) else []
    if reasons:
        for r in reasons:
            lines.append(f"- {r}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Warnings")
    lines.append("")
    warns = data.get("warnings") if isinstance(data.get("warnings"), list) else []
    if warns:
        for w in warns:
            lines.append(f"- {w}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Baseline Evaluation")
    lines.append("")
    baseline = data.get("baseline_evaluation") if isinstance(data.get("baseline_evaluation"), list) else []
    if baseline:
        lines.append("| step_name | status | current_sec | sample_count | median_sec | mean_sec | p95_sec | warn_threshold_sec | fail_threshold_sec | note |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
        for x in baseline:
            note = str(x.get("note") or "").replace("|", "\\|")
            lines.append(
                "| "
                + f"{str(x.get('step_name') or '')} | {str(x.get('status') or '')} | "
                + f"{_safe_float(x.get('current_duration_sec'), 0.0):.2f} | {int(x.get('sample_count') or 0)} | "
                + f"{_safe_float(x.get('median_duration_sec'), 0.0):.2f} | {_safe_float(x.get('mean_duration_sec'), 0.0):.2f} | "
                + f"{_safe_float(x.get('p95_duration_sec'), 0.0):.2f} | {_safe_float(x.get('warn_threshold_sec'), 0.0):.2f} | "
                + f"{_safe_float(x.get('fail_threshold_sec'), 0.0):.2f} | {note} |"
            )
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scenario Router audit quality gate")
    p.add_argument("--result-json", default="reports/scenario_router_audit_result.json")
    p.add_argument("--history-jsonl", default="reports/scenario_router_audit_history.jsonl")
    p.add_argument("--output-json", default="reports/scenario_router_audit_gate.json")
    p.add_argument("--output-md", default="reports/scenario_router_audit_gate.md")
    p.add_argument("--preset", default="")
    p.add_argument("--preset-file", default="scripts/scenario_router_audit_gate_presets.yaml")
    p.add_argument("--strict", dest="strict", action="store_true", default=None)
    p.add_argument("--no-strict", dest="strict", action="store_false")
    p.add_argument("--min-last-10-success-rate", type=float, default=None)
    p.add_argument("--fail-on-flaky", dest="fail_on_flaky", action="store_true", default=None)
    p.add_argument("--no-fail-on-flaky", dest="fail_on_flaky", action="store_false")
    p.add_argument("--baseline-window", type=int, default=None)
    p.add_argument("--duration-warn-multiplier", type=float, default=None)
    p.add_argument("--duration-fail-multiplier", type=float, default=None)
    p.add_argument("--min-baseline-samples", type=int, default=None)
    p.add_argument("--fail-on-duration-spike", dest="fail_on_duration_spike", action="store_true", default=None)
    p.add_argument("--no-fail-on-duration-spike", dest="fail_on_duration_spike", action="store_false")
    p.add_argument("--required-step", action="append", default=None)
    return p


def main() -> int:
    args = _parser().parse_args()
    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    try:
        applied_preset, effective_cfg, preset_file, preset_warnings = _resolve_effective_config(args)
    except ValueError as e:
        data = {
            "gate_status": "FAIL",
            "exit_code": 1,
            "reasons": [str(e)],
            "warnings": [],
            "latest_status": "",
            "last_10_success_rate": 0.0,
            "flaky_warning": False,
            "failed_steps": [],
            "baseline_evaluation": [],
            "applied_preset": str(args.preset or "none"),
            "preset_file": str(args.preset_file or "scripts/scenario_router_audit_gate_presets.yaml"),
            "preset_warnings": [],
            "effective_config": dict(DEFAULT_CONFIG),
            "suggested_next_action": "Fix preset selection and retry quality gate.",
        }
        out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_md.write_text(_render_md(data), encoding="utf-8")
        return 1

    if not Path(args.result_json).exists() or not Path(args.history_jsonl).exists():
        missing = []
        if not Path(args.result_json).exists():
            missing.append("result_json")
        if not Path(args.history_jsonl).exists():
            missing.append("history_jsonl")
        data = {
            "gate_status": "FAIL",
            "exit_code": 1,
            "reasons": [f"missing input files: {', '.join(missing)}"],
            "warnings": [],
            "latest_status": "",
            "last_10_success_rate": 0.0,
            "flaky_warning": False,
            "failed_steps": [],
            "baseline_evaluation": [],
            "applied_preset": applied_preset,
            "preset_file": preset_file,
            "preset_warnings": preset_warnings,
            "effective_config": effective_cfg,
            "suggested_next_action": "Run audit pipeline first and retry quality gate.",
        }
        out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_md.write_text(_render_md(data), encoding="utf-8")
        return 1

    result = _read_json(Path(args.result_json))
    history = _read_jsonl(Path(args.history_jsonl))

    data = evaluate_gate(
        result=result,
        history=history,
        strict=bool(effective_cfg.get("strict")),
        min_last_10_success_rate=_safe_float(effective_cfg.get("min_last_10_success_rate"), 0.8),
        fail_on_flaky=bool(effective_cfg.get("fail_on_flaky")),
        baseline_window=max(1, _safe_int(effective_cfg.get("baseline_window"), 10)),
        duration_warn_multiplier=max(1.0, _safe_float(effective_cfg.get("duration_warn_multiplier"), 2.0)),
        duration_fail_multiplier=max(1.0, _safe_float(effective_cfg.get("duration_fail_multiplier"), 4.0)),
        min_baseline_samples=max(1, _safe_int(effective_cfg.get("min_baseline_samples"), 5)),
        fail_on_duration_spike=bool(effective_cfg.get("fail_on_duration_spike")),
        required_steps=_normalize_required_steps(effective_cfg.get("required_steps")),
    )
    data["applied_preset"] = applied_preset
    data["preset_file"] = preset_file
    data["preset_warnings"] = preset_warnings
    data["effective_config"] = effective_cfg
    if "baseline_config" in data:
        del data["baseline_config"]

    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(_render_md(data), encoding="utf-8")
    return int(data.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
