"""Physical plausibility, stuck-sensor and impossible-jump checks."""
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .rule_config import get_rule_metadata


def check_physical_validity(
    row: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Check supplied readings against physical and temporal sensor rules."""
    metadata = metadata or get_rule_metadata()
    state = state if state is not None else {}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    physical_rules = metadata["physical_rules"]
    invalid_sensors: Dict[str, Any] = {}
    context_issues: Dict[str, Any] = {}
    frozen_sensors: Dict[str, Any] = {}
    impossible_jumps: Dict[str, Any] = {}
    freeze_config = physical_rules.get("frozen_detection", {})
    jump_config = physical_rules.get("jump_detection", {})
    repeat_limit = int(freeze_config["consecutive_readings"])
    reference_interval = float(jump_config["reference_interval_seconds"])
    minimum_interval = float(jump_config["minimum_interval_seconds"])
    maximum_gap = float(jump_config["maximum_gap_seconds"])
    max_changes = physical_rules.get("maximum_change_per_15_seconds", {})

    for sensor, rule in physical_rules["sensors"].items():
        value = row.get(rule["field"])
        if value is None:
            state.pop(sensor, None)
            continue

        lower, upper = rule["min"], rule["max"]
        previous = state.get(sensor)
        if value < lower or value > upper:
            issue = {
                "value": value,
                "min": lower,
                "max": upper,
                "unit": rule["unit"],
            }
            target = context_issues if rule.get("context") else invalid_sensors
            target[sensor] = issue
            state.pop(sensor, None)
            continue

        repeat_count = (
            previous["repeat_count"] + 1
            if previous is not None and value == previous["value"]
            else 1
        )
        jump_value = (
            previous.get("jump_value", previous["value"])
            if previous is not None
            else value
        )
        jump_timestamp = (
            previous.get("jump_timestamp", previous["timestamp"])
            if previous is not None
            else now
        )
        if freeze_config.get("enabled", True) and repeat_count >= repeat_limit:
            frozen_sensors[sensor] = {
                "value": value,
                "repeat_count": repeat_count,
                "required_readings": repeat_limit,
                "min": lower,
                "max": upper,
                "unit": rule["unit"],
            }

        if (
            previous is not None
            and jump_config.get("enabled", True)
            and sensor in max_changes
        ):
            elapsed = (now - jump_timestamp).total_seconds()
            if minimum_interval <= elapsed <= maximum_gap:
                permitted_change = (
                    float(max_changes[sensor]) * elapsed / reference_interval
                )
                change = abs(value - jump_value)
                if change > permitted_change:
                    impossible_jumps[sensor] = {
                        "value": value,
                        "previous_value": jump_value,
                        "change": change,
                        "permitted_change": permitted_change,
                        "unit": rule["unit"],
                        "min": lower,
                        "max": upper,
                    }
                jump_value, jump_timestamp = value, now
            elif elapsed > maximum_gap:
                jump_value, jump_timestamp = value, now

        state[sensor] = {
            "value": value,
            "timestamp": now,
            "repeat_count": repeat_count,
            "jump_value": jump_value,
            "jump_timestamp": jump_timestamp,
        }

    return {
        "blocking": bool(
            invalid_sensors or context_issues or frozen_sensors or impossible_jumps
        ),
        "invalid_sensors": invalid_sensors,
        "context_issues": context_issues,
        "frozen_sensors": frozen_sensors,
        "impossible_jumps": impossible_jumps,
    }
