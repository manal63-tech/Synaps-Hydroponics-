"""Database models."""
from datetime import date, datetime

from .extensions import db


class Reading(db.Model):
    """A single sensor reading and the decision produced for it.

    The full decision dict returned to clients is preserved verbatim in
    ``result_json`` so the API responses are identical to the previous
    in-memory implementation; the flattened columns exist for querying,
    indexing and reporting (and for the Excel export).
    """

    __tablename__ = "readings"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True
    )

    # --- Sensor inputs ---
    temperature = db.Column(db.Float, nullable=False)  # air temperature, inside
    humidity = db.Column(db.Float, nullable=False)      # air humidity, inside
    temperature_outside = db.Column(db.Float, nullable=True)
    humidity_outside = db.Column(db.Float, nullable=True)
    water_temperature = db.Column(db.Float, nullable=True)
    tds = db.Column(db.Float, nullable=False)
    ph = db.Column(db.Float, nullable=False)
    water_level = db.Column(db.Float, nullable=True)

    # Growth day / DAP is no longer sent by the client: it is derived from
    # PlantationConfig.plantation_date at prediction time and stored here
    # denormalised, exactly as before, so historical rows stay queryable.
    growth_days = db.Column(db.Integer, nullable=False)

    # --- Actuator state as reported by the ESP32 (informational only; the
    # ESP32 remains the source of truth for the physical relay state) ---
    led_on = db.Column(db.Boolean, nullable=True)
    led_mode = db.Column(db.String(16), nullable=True)  # "auto" | "manual"
    pump_on = db.Column(db.Boolean, nullable=True)
    fan_on = db.Column(db.Boolean, nullable=True)

    # --- Decision summary (denormalised for querying) ---
    final_decision = db.Column(db.String(64), nullable=False)
    actuator_command = db.Column(db.String(64), nullable=False)
    anomaly = db.Column(db.String(64), nullable=True)
    anomaly_score = db.Column(db.Float, nullable=True)
    severity = db.Column(db.String(32), nullable=True)

    # --- Full decision payload (verbatim API response) ---
    result_json = db.Column(db.Text, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Reading {self.id} {self.final_decision} @ {self.created_at}>"


class PlantationConfig(db.Model):
    """Single-row config holding the current crop's plantation date.

    Growth day (DAP - days after planting) used to be typed in per-reading
    (and the ESP32 never actually sent it, so it silently defaulted to 14
    every time). Instead the grower sets the plantation date once from the
    dashboard, and every reading derives its growth day from
    ``(today - plantation_date).days + 1``.
    """

    __tablename__ = "plantation_config"

    id = db.Column(db.Integer, primary_key=True)
    plantation_date = db.Column(db.Date, nullable=False, default=date.today)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class LedSettings(db.Model):
    """Single-row config for the LED relay, set from the dashboard.

    Mirrors the ``automation`` object the ESP32 firmware's
    ``parseAutomation()`` already expects back on every ``/predict``
    response (``led_mode``, ``led_state``, ``led_on_hour``,
    ``led_off_hour``) - that response field simply didn't exist before.
    """

    __tablename__ = "led_settings"

    id = db.Column(db.Integer, primary_key=True)
    mode = db.Column(db.String(16), nullable=False, default="auto")  # auto | manual
    manual_state = db.Column(db.Boolean, nullable=False, default=False)
    on_hour = db.Column(db.Integer, nullable=False, default=6)
    off_hour = db.Column(db.Integer, nullable=False, default=20)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class InvalidPayload(db.Model):
    """Audit log for rejected ``/predict`` payloads.

    Previously a malformed/incomplete payload returned a 400 and left no
    trace anywhere, which made intermittent ESP32/WiFi/sensor issues
    impossible to diagnose after the fact. Every rejected payload is now
    logged here with the reason it was rejected.
    """

    __tablename__ = "invalid_payloads"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    raw_payload = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<InvalidPayload {self.id} @ {self.created_at}>"
