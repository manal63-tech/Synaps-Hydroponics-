"""Deterministic monitoring using metadata-configured physical and crop rules."""
from datetime import datetime
from typing import Any, Dict, Optional

from .rule_config import get_rule_metadata
from .safety import check_physical_validity

_SEVERITY_RANK = {
    "normal": 0,
    "investigate": 1,
    "critical": 2,
    "sensor_fault": 3,
}
_DISPLAY_SEVERITY = {
    "normal": "Healthy",
    "investigate": "Investigate",
    "critical": "Critical",
    "sensor_fault": "Sensor Fault",
}


def _contains(value: float, band: Dict[str, Any]) -> bool:
    if "min" in band:
        if band.get("min_inclusive", True):
            if value < band["min"]:
                return False
        elif value <= band["min"]:
            return False
    if "max" in band:
        if band.get("max_inclusive", True):
            if value > band["max"]:
                return False
        elif value >= band["max"]:
            return False
    return True


def _agronomic_status(value: Optional[float], rule: Dict[str, Any]) -> Optional[str]:
    if value is None:
        return None
    for status in ("ideal", "attention", "critical"):
        if any(_contains(value, band) for band in rule.get(status, [])):
            return "ok" if status == "ideal" else status
    return "critical"


def _format_band(band: Dict[str, Any]) -> str:
    if "min" in band and "max" in band:
        return f"{band['min']:g}–{band['max']:g}"
    if "min" in band:
        return f">{band['min']:g}" if not band.get("min_inclusive", True) else f"≥{band['min']:g}"
    if "max" in band:
        return f"<{band['max']:g}" if not band.get("max_inclusive", True) else f"≤{band['max']:g}"
    raise ValueError(f"Plage de regle invalide: {band!r}")


def _format_ranges(rule: Dict[str, Any]) -> str:
    labels = {"ideal": "idéal", "attention": "attention", "critical": "critique"}
    return "; ".join(
        f"{labels[status]}: {' ou '.join(_format_band(band) for band in rule.get(status, []))} "
        f"{rule['unit']}"
        for status in ("ideal", "attention", "critical")
        if rule.get(status)
    )


def _make_recommendation(
    *,
    sensor: str,
    sensor_rule: Dict[str, Any],
    value: Any,
    unit: str,
    severity: str,
    expected: str,
    action: str,
    rule_type: Optional[str] = None,
) -> Dict[str, Any]:
    label = sensor_rule["label"]
    shown_value = f"{value:g} {unit}".strip()
    title = (
        f"Défaut capteur — {label}"
        if severity == "sensor_fault"
        else f"{label} — {severity}"
    )
    message = (
        f"{label} mesuré : {shown_value}. Plage attendue : {expected}. "
        f"Action : {action}"
    )
    return {
        "type": rule_type or sensor,
        "level": severity,
        "title": title,
        "message": message,
        "sensor": sensor,
        "sensor_label": label,
        "value": value,
        "unit": unit,
        "expected_range": expected,
        "action": action,
        "details": True,
    }


def deterministic_decision(
    row: Dict[str, Any],
    measurement_state: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assess supplied measurements without manufacturing missing values."""
    metadata = metadata or get_rule_metadata()
    physical_rules = metadata["physical_rules"]
    agronomic_rules = metadata["agronomic_rules"]
    physical = check_physical_validity(
        row, state=measurement_state, metadata=metadata
    )
    physical_sensors = physical_rules["sensors"]
    agronomic_sensors = agronomic_rules["sensors"]
    recommendations = []
    reasons = []
    level3: Dict[str, Optional[str]] = {}
    severity = "normal"

    def raise_severity(candidate: str) -> None:
        nonlocal severity
        if _SEVERITY_RANK[candidate] > _SEVERITY_RANK[severity]:
            severity = candidate

    issue_groups = (
        ("invalid_sensors", physical["invalid_sensors"], "Valeur hors bornes physiques"),
        ("context_issues", physical["context_issues"], "Valeur hors bornes physiques"),
        ("frozen_sensors", physical["frozen_sensors"], "Valeur figée"),
        ("impossible_jumps", physical["impossible_jumps"], "Saut impossible"),
    )
    fault_sensors = set()
    for group, issues, fault_title in issue_groups:
        for sensor, issue in issues.items():
            fault_sensors.add(sensor)
            rule = physical_sensors[sensor]
            expected = f"{issue['min']:g}–{issue['max']:g} {issue['unit']}"
            if group == "frozen_sensors":
                expected += (
                    f"; variation attendue avant {issue['required_readings']} "
                    "lectures identiques"
                )
            elif group == "impossible_jumps":
                expected += (
                    f"; saut maximal {issue['permitted_change']:.2g} "
                    f"{issue['unit']} sur l'intervalle observe"
                )
            action = metadata.get(
                "sensor_fault_action",
                "Verifier le branchement et l'etat du capteur avant toute intervention sur la culture.",
            )
            recommendation = _make_recommendation(
                sensor=sensor,
                sensor_rule=rule,
                value=issue["value"],
                unit=issue["unit"],
                severity="sensor_fault",
                expected=expected,
                action=action,
                rule_type="sensor_fault",
            )
            recommendation["fault_reason"] = fault_title
            if group == "impossible_jumps":
                recommendation["previous_value"] = issue["previous_value"]
            elif group == "frozen_sensors":
                recommendation["consecutive_readings"] = issue["repeat_count"]
            recommendations.append(recommendation)
            reasons.append(recommendation["message"])

    for sensor, rule in agronomic_sensors.items():
        physical_rule = physical_sensors[sensor]
        value = row.get(physical_rule["field"])
        status = None if sensor in fault_sensors else _agronomic_status(value, rule)
        level3[rule.get("status_key", f"{sensor}_status")] = status
        if status in ("attention", "critical"):
            raise_severity(status == "attention" and "investigate" or "critical")
            action = metadata["manual_actions"].get(
                sensor, "Verifier manuellement la mesure et les conditions de culture."
            )
            recommendation = _make_recommendation(
                sensor=sensor,
                sensor_rule=physical_rule,
                value=value,
                unit=rule["unit"],
                severity=status,
                expected=_format_ranges(rule),
                action=action,
                rule_type=rule.get("alert_type"),
            )
            recommendations.append(recommendation)
            reasons.append(recommendation["message"])

    if fault_sensors:
        raise_severity("sensor_fault")

    if not recommendations:
        recommendations.append({
            "type": "normal",
            "level": "success",
            "title": "État normal",
            "message": "Les mesures disponibles sont dans leurs plages idéales.",
        })
        reasons.append("Les mesures disponibles sont dans leurs plages idéales.")

    if severity == "sensor_fault":
        actuator_command = "MANUAL_HARDWARE_CHECK"
    elif severity == "normal":
        actuator_command = "NONE"
    else:
        actuator_command = "MANUAL_REVIEW"

    return {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "final_decision": _DISPLAY_SEVERITY[severity],
        "actuator_command": actuator_command,
        "user_message": " ".join(item["message"] for item in recommendations),
        "user_action": recommendations[0]["action"] if recommendations[0].get("action") else recommendations[0]["message"],
        "severity": severity,
        "level1": {
            "invalid_sensors": physical["invalid_sensors"],
            "context_issues": physical["context_issues"],
            "frozen_sensors": physical["frozen_sensors"],
            "impossible_jumps": physical["impossible_jumps"],
        },
        "level3": level3,
        "recommendations": recommendations,
        "reasons": reasons,
    }
