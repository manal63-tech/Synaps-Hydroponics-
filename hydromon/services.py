"""Application service layer.

Bridges inbound payloads, the 3-level decision pipeline (see decision.py)
and the database. Live readings update immediately; history records decision
transitions and otherwise samples at 15-minute intervals.
"""
import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flask import current_app

from .db_models import InvalidPayload, Reading
from .extensions import db
from .schemas import normalize_payload

# Global memory cache for instantaneous real-time monitoring
_state_lock = threading.Lock()
_latest_reading_cache: Optional[Dict[str, Any]] = None
_last_db_save_time: Optional[datetime] = None
_last_db_severity: Optional[str] = None
_last_db_decision: Optional[str] = None


def process_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a sensor payload, apply deterministic safety rules,
    and update the real-time cache.

    Growth day (DAP) is still computed and stored for record-keeping /
    future use; it does not feed the current decision rules.
    """
    from .decision import deterministic_decision
    from .led import automation_payload
    from .plantation import compute_growth_days, get_plantation_date

    payload = normalize_payload(data)

    plantation_date = get_plantation_date()
    growth_days = compute_growth_days(plantation_date)

    # Canonical keys expected by safety.py / decision.py.
    row = {
        "pH": payload["ph"],
        "TDS": payload["tds"],
        "DHT_temp": payload["temperature"],
        "DHT_humidity": payload["humidity"],
        "water_temp": payload["water_temperature"],
        "water_level": payload["water_level"],
        "temperature_outside": payload["temperature_outside"],
        "humidity_outside": payload["humidity_outside"],
    }

    payload["growth_days"] = growth_days
    payload["plantation_date"] = plantation_date.isoformat()
    with _state_lock:
        measurement_state = current_app.extensions.setdefault(
            "hydromon_sensor_rule_state", {}
        )
        result = deterministic_decision(row, measurement_state=measurement_state)
        result["sensor_data"] = payload

        # Sync keys for ESP32 and Dashboard.
        result["dap"] = growth_days
        result["automation"] = automation_payload()

        # Update real-time memory state immediately.
        global _latest_reading_cache
        _latest_reading_cache = result

    return result


def should_persist_to_db(result: Dict[str, Any], force: bool = False) -> bool:
    """Determine whether to persist this reading into the SQLite database.

    Persists if:
    - force is True
    - No previous record in this session
    - 15 minutes elapsed since last DB entry
    - A decision/severity transition
    """
    if force:
        return True

    global _last_db_save_time
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if _last_db_save_time is None:
        return True

    severity = result.get("severity")
    decision = result.get("final_decision")
    if severity != _last_db_severity or decision != _last_db_decision:
        return True

    return now - _last_db_save_time >= timedelta(minutes=15)


def save_reading(result: Dict[str, Any], force: bool = False) -> Optional[Reading]:
    """Persist a decision result as a Reading row according to the sampling policy."""
    payload = result["sensor_data"]
    if any(payload.get(field) is None for field in ("temperature", "humidity", "tds", "ph")):
        return None

    if not should_persist_to_db(result, force=force):
        return None

    reading = Reading(
        temperature=payload["temperature"],
        humidity=payload["humidity"],
        temperature_outside=payload.get("temperature_outside"),
        humidity_outside=payload.get("humidity_outside"),
        water_temperature=payload.get("water_temperature"),
        tds=payload["tds"],
        ph=payload["ph"],
        growth_days=payload["growth_days"],
        water_level=payload["water_level"],
        led_on=payload.get("led_on"),
        led_mode=payload.get("led_mode"),
        pump_on=payload.get("pump_on"),
        fan_on=payload.get("fan_on"),
        final_decision=result["final_decision"],
        actuator_command=result["actuator_command"],
        severity=result.get("severity"),
        result_json=json.dumps(result),
    )

    db.session.add(reading)
    db.session.commit()

    global _last_db_save_time, _last_db_severity, _last_db_decision
    _last_db_save_time = datetime.now(timezone.utc).replace(tzinfo=None)
    _last_db_severity = result.get("severity")
    _last_db_decision = result.get("final_decision")

    return reading


def log_invalid_payload(raw_data: Any, error: Any) -> InvalidPayload:
    """Persist a rejected payload for post-mortem analysis."""
    entry = InvalidPayload(
        raw_payload=json.dumps(raw_data) if raw_data is not None else None,
        error_message=str(error),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


_export_thread = None
_export_stop_event = threading.Event()


def _export_worker(app, interval_minutes, export_dir):
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    with app.app_context():
        while not _export_stop_event.wait(interval_minutes * 60):
            try:
                buffer = export_readings_excel()
                filename = export_dir / f"hydromon_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                with open(filename, "wb") as handle:
                    handle.write(buffer.getvalue())
                app.logger.info("Generated scheduled export: %s", filename)
            except Exception as exc:
                app.logger.exception("Scheduled export failed")


def start_export_scheduler(app):
    global _export_thread, _export_stop_event

    if _export_thread is not None and _export_thread.is_alive():
        return

    _export_stop_event.clear()
    interval = app.config.get("EXPORT_INTERVAL_MINUTES", 15)
    export_dir = app.config.get("EXPORT_DIR", Path("exports"))

    _export_thread = threading.Thread(
        target=_export_worker,
        args=(app, interval, export_dir),
        daemon=True,
        name="HydromonExportScheduler",
    )
    _export_thread.start()


def get_history(start: Optional[datetime] = None, end: Optional[datetime] = None):
    """Return stored results chronologically, optionally within a UTC interval."""
    limit = current_app.config["MAX_HISTORY"]
    query = Reading.query
    if start is None and end is None:
        rows = query.order_by(Reading.id.desc()).limit(limit).all()
        rows.reverse()
    else:
        if start is not None:
            query = query.filter(Reading.created_at >= start)
        if end is not None:
            query = query.filter(Reading.created_at < end)
        rows = query.order_by(Reading.created_at.asc(), Reading.id.asc()).all()

    history = []
    for row in rows:
        result = json.loads(row.result_json)
        if "ai_detection" in result:
            sensor_data = result.get("sensor_data") or {}
            from .decision import deterministic_decision

            rule_result = deterministic_decision({
                "pH": sensor_data.get("ph"),
                "TDS": sensor_data.get("tds"),
                "DHT_temp": sensor_data.get("temperature"),
                "DHT_humidity": sensor_data.get("humidity"),
                "water_temp": sensor_data.get("water_temperature"),
                "water_level": sensor_data.get("water_level"),
                "temperature_outside": sensor_data.get("temperature_outside"),
                "humidity_outside": sensor_data.get("humidity_outside"),
            })
            for key in (
                "final_decision", "actuator_command", "user_message", "user_action",
                "severity", "level1", "level3", "recommendations", "reasons",
            ):
                result[key] = rule_result[key]
            result.pop("ai_detection", None)
            result.pop("anomaly", None)
            result.pop("anomaly_score", None)
        result["recorded_at"] = row.created_at.isoformat(timespec="seconds") + "Z"
        history.append(result)
    return history


def get_latest():
    """Return the latest reading received by this running application process.

    Persisted readings belong to history; using one as a live fallback after
    restart would present stale sensor data as if it were current.
    """
    global _latest_reading_cache
    with _state_lock:
        return _latest_reading_cache


# --- Plantation date -------------------------------------------------------

def get_plantation_info():
    from .plantation import compute_growth_days, get_plantation_date

    plantation_date = get_plantation_date()
    return {
        "plantation_date": plantation_date.isoformat(),
        "growth_days": compute_growth_days(plantation_date),
    }


def update_plantation_date(date_str):
    from .plantation import compute_growth_days, set_plantation_date

    if not date_str:
        raise ValueError("Le champ plantation_date est requis (format AAAA-MM-JJ).")

    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Date invalide, format attendu AAAA-MM-JJ.") from exc

    if parsed > date.today():
        raise ValueError("La date de plantation ne peut pas etre dans le futur.")

    config = set_plantation_date(parsed)

    return {
        "plantation_date": config.plantation_date.isoformat(),
        "growth_days": compute_growth_days(config.plantation_date),
    }


# --- LED automation ----------------------------------------------------

def get_led_info():
    from .led import get_led_settings

    settings = get_led_settings()
    return {
        "mode": settings.mode,
        "manual_state": settings.manual_state,
        "on_hour": settings.on_hour,
        "off_hour": settings.off_hour,
    }


def update_led_settings(mode=None, manual_state=None, on_hour=None, off_hour=None):
    from .led import set_led_settings

    settings = set_led_settings(
        mode=mode, manual_state=manual_state, on_hour=on_hour, off_hour=off_hour
    )

    return {
        "mode": settings.mode,
        "manual_state": settings.manual_state,
        "on_hour": settings.on_hour,
        "off_hour": settings.off_hour,
    }


# --- Excel export ------------------------------------------------------

def export_readings_excel():
    """Build an in-memory .xlsx workbook of every stored reading."""
    import pandas as pd
    from io import BytesIO

    rows = Reading.query.order_by(Reading.id.asc()).all()

    records = [{
        "ID": r.id,
        "Horodatage (UTC)": r.created_at.isoformat(sep=" ", timespec="seconds"),
        "Temp. interieure (C)": r.temperature,
        "Humidite interieure (%)": r.humidity,
        "Temp. exterieure (C)": r.temperature_outside,
        "Humidite exterieure (%)": r.humidity_outside,
        "Temp. eau (C)": r.water_temperature,
        "TDS (ppm)": r.tds,
        "pH": r.ph,
        "Niveau d'eau (%)": r.water_level,
        "Jours apres plantation": r.growth_days,
        "LED": (
            "ON" if r.led_on is True else "OFF" if r.led_on is False else "N/A"
        ),
        "Mode LED": r.led_mode or "N/A",
        "Pompe": (
            "ON" if r.pump_on is True else "OFF" if r.pump_on is False else "N/A"
        ),
        "Ventilateur": (
            "ON" if r.fan_on is True else "OFF" if r.fan_on is False else "N/A"
        ),
        "Decision": r.final_decision,
        "Commande actionneur": r.actuator_command,
        "Severite": r.severity,
    } for r in rows]

    df = pd.DataFrame(records)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Mesures")
        workbook = writer.book
        workbook.security.workbookPassword = current_app.config.get("EXPORT_PASSWORD", "manalpfe2026")
        workbook.security.lockStructure = True
    buffer.seek(0)

    return buffer
