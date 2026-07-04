from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _default_rules() -> dict[str, Any]:
    return {
        "trend": {"short_window": 7, "long_window": 30},
        "thresholds": {
            "parser_survival": {"mode": "min", "warning": 0.90, "critical": 0.80, "action": "notify"},
            "repository_survival": {"mode": "min", "warning": 0.95, "critical": 0.90, "action": "retry"},
            "retry_rate": {"mode": "max", "warning": 0.15, "critical": 0.25, "action": "retry"},
            "quality_score": {"mode": "min", "warning": 90.0, "critical": 80.0, "action": "notify"},
        },
        "error_codes": {
            "E003": {"warning": 10, "critical": 30, "action": "invalidate_cache"},
            "E202": {"warning": 10, "critical": 20, "action": "reconnect"},
        },
        "quality_gate": {"min_score": 95.0, "required_success_rate": 0.90, "block_on_critical": True},
    }


def _rules_path() -> Path:
    return Path(__file__).with_name("alert_rules.yaml")


def load_alert_rules(path: Path | None = None) -> dict[str, Any]:
    p = path or _rules_path()
    base = _default_rules()
    if not p.exists():
        return base
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return base
    if not isinstance(raw, dict):
        return base

    out = dict(base)
    for key in ("trend", "thresholds", "error_codes", "quality_gate"):
        node = raw.get(key)
        if isinstance(node, dict):
            merged = dict(base.get(key, {}))
            merged.update(node)
            out[key] = merged
    return out


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values)) / float(len(values))


def _metric_series(history: list[dict], metric_path: tuple[str, str]) -> list[float]:
    group, key = metric_path
    vals: list[float] = []
    for row in history:
        node = row.get(group) or {}
        try:
            vals.append(float(node.get(key, 0.0)))
        except Exception:
            vals.append(0.0)
    return vals


def analyze_quality_trends(history: list[dict], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = rules or load_alert_rules()
    trend_cfg = cfg.get("trend") or {}
    short_window = max(1, int(trend_cfg.get("short_window", 7)))
    long_window = max(short_window, int(trend_cfg.get("long_window", 30)))
    items = list(history)

    if not items:
        return {
            "window": {"short": short_window, "long": long_window},
            "latest": {},
            "metrics": {},
        }

    metrics_map = {
        "parser_survival": ("rates", "parser_survival"),
        "repository_survival": ("rates", "repository_survival"),
        "retry_rate": ("rates", "retry_rate"),
        "success_rate": ("rates", "success_rate"),
        "quality_score": ("metrics", "quality_score"),
    }

    out_metrics: dict[str, Any] = {}
    for name, path in metrics_map.items():
        series = _metric_series(items, path)
        current = float(series[0]) if series else 0.0
        short_avg = _avg(series[:short_window])
        long_avg = _avg(series[:long_window])
        out_metrics[name] = {
            "current": current,
            "short_avg": short_avg,
            "long_avg": long_avg,
            "delta_short_vs_long": short_avg - long_avg,
            "delta_current_vs_long": current - long_avg,
        }

    return {
        "window": {"short": short_window, "long": long_window},
        "latest": items[0],
        "metrics": out_metrics,
    }


def _check_threshold(value: float, mode: str, warning: float, critical: float) -> str | None:
    m = str(mode or "min").lower()
    if m == "max":
        if value >= float(critical):
            return "critical"
        if value >= float(warning):
            return "warning"
        return None
    if value <= float(critical):
        return "critical"
    if value <= float(warning):
        return "warning"
    return None


def generate_alerts(
    history: list[dict],
    summary: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = rules or load_alert_rules()
    trends = analyze_quality_trends(history, cfg)
    threshold_rules = cfg.get("thresholds") or {}

    alerts: list[dict[str, Any]] = []
    for metric_name, rule in threshold_rules.items():
        if not isinstance(rule, dict):
            continue
        metric = (trends.get("metrics") or {}).get(metric_name) or {}
        current = float(metric.get("current", 0.0))
        warning = float(rule.get("warning", 0.0))
        critical = float(rule.get("critical", 0.0))
        mode = str(rule.get("mode", "min"))
        level = _check_threshold(current, mode, warning, critical)
        if not level:
            continue
        alerts.append(
            {
                "kind": "threshold",
                "severity": level,
                "metric": metric_name,
                "value": current,
                "warning": warning,
                "critical": critical,
                "mode": mode,
                "action": str(rule.get("action", "notify")),
                "message": f"{metric_name} breached {level} threshold: {current:.4f}",
            }
        )

    code_rules = cfg.get("error_codes") or {}
    top_codes = summary.get("top_error_codes") or []
    code_count_map = {str(item.get("code")): int(item.get("count", 0)) for item in top_codes}

    for code, rule in code_rules.items():
        if not isinstance(rule, dict):
            continue
        current = int(code_count_map.get(str(code), 0))
        warning = int(rule.get("warning", 0))
        critical = int(rule.get("critical", 0))
        level = None
        if current >= critical:
            level = "critical"
        elif current >= warning:
            level = "warning"
        if not level:
            continue
        alerts.append(
            {
                "kind": "error_code",
                "severity": level,
                "metric": str(code),
                "value": current,
                "warning": warning,
                "critical": critical,
                "mode": "max",
                "action": str(rule.get("action", "notify")),
                "message": f"error code {code} count elevated: {current}",
            }
        )

    alerts.sort(key=lambda a: (0 if a.get("severity") == "critical" else 1, str(a.get("metric"))))
    return alerts


def evaluate_dataset_gate(
    history: list[dict],
    summary: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = rules or load_alert_rules()
    gate = cfg.get("quality_gate") or {}
    min_score = float(gate.get("min_score", 95.0))
    required_success_rate = float(gate.get("required_success_rate", 0.90))
    block_on_critical = bool(gate.get("block_on_critical", True))

    latest = history[0] if history else {}
    latest_metrics = latest.get("metrics") or {}
    latest_rates = latest.get("rates") or {}

    score = float(latest_metrics.get("quality_score", 0.0))
    success_rate = float(latest_rates.get("success_rate", 0.0))
    alerts = generate_alerts(history, summary, cfg)
    critical_alerts = [a for a in alerts if str(a.get("severity")) == "critical"]

    reasons: list[str] = []
    if score < min_score:
        reasons.append(f"quality_score {score:.1f} < required {min_score:.1f}")
    if success_rate < required_success_rate:
        reasons.append(
            f"success_rate {success_rate:.3f} < required {required_success_rate:.3f}"
        )
    if block_on_critical and critical_alerts:
        reasons.append(f"critical_alerts={len(critical_alerts)}")

    allow_training = len(reasons) == 0
    recommended_actions = sorted({str(a.get("action", "notify")) for a in alerts})

    return {
        "allow_training": allow_training,
        "reasons": reasons,
        "quality_score": score,
        "success_rate": success_rate,
        "thresholds": {
            "min_score": min_score,
            "required_success_rate": required_success_rate,
            "block_on_critical": block_on_critical,
        },
        "alert_counts": {
            "total": len(alerts),
            "critical": len(critical_alerts),
            "warning": len([a for a in alerts if str(a.get("severity")) == "warning"]),
        },
        "recommended_actions": recommended_actions,
    }
