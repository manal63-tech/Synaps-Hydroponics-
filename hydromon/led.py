"""LED automation state.

Stores the LED mode/schedule requested from the dashboard and produces the
``automation`` object the ESP32 firmware's ``parseAutomation()`` already
knows how to read (``led_mode``, ``led_state``, ``led_on_hour``,
``led_off_hour``). This object simply didn't exist in the ``/predict``
response before, so the firmware's manual/auto toggle had nothing to react
to.

The ESP32 remains the source of truth for the *physical* relay state - it
reports what the relay is actually doing via ``actuator_status`` on every
POST, and that reported state is what gets persisted per-reading
(``Reading.led_on`` / ``Reading.led_mode``) for the dashboard and history.
This module only stores the *requested* mode/schedule, not the live state.
"""
from datetime import datetime

from .db_models import LedSettings
from .extensions import db

_SETTINGS_ID = 1


def get_led_settings() -> LedSettings:
    settings = LedSettings.query.get(_SETTINGS_ID)

    if settings is None:
        settings = LedSettings(
            id=_SETTINGS_ID, mode="auto", manual_state=False, on_hour=6, off_hour=20
        )
        db.session.add(settings)
        db.session.commit()

    return settings


def set_led_settings(mode=None, manual_state=None, on_hour=None, off_hour=None) -> LedSettings:
    settings = get_led_settings()

    if mode is not None:
        if mode not in ("auto", "manual"):
            raise ValueError("led mode doit etre 'auto' ou 'manual'.")
        settings.mode = mode

    if manual_state is not None:
        settings.manual_state = bool(manual_state)

    if on_hour is not None:
        on_hour = int(on_hour)
        if not (0 <= on_hour <= 23):
            raise ValueError("led_on_hour doit etre entre 0 et 23.")
        settings.on_hour = on_hour

    if off_hour is not None:
        off_hour = int(off_hour)
        if not (0 <= off_hour <= 23):
            raise ValueError("led_off_hour doit etre entre 0 et 23.")
        settings.off_hour = off_hour

    settings.updated_at = datetime.utcnow()
    db.session.commit()
    return settings


def automation_payload(settings: LedSettings | None = None) -> dict:
    """Build the ``automation`` object expected by the ESP32 firmware."""
    if settings is None:
        settings = get_led_settings()

    return {
        "led_mode": settings.mode,
        "led_state": settings.manual_state,
        "led_on_hour": settings.on_hour,
        "led_off_hour": settings.off_hour,
    }
