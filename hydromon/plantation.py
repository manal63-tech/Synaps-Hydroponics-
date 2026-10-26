"""Plantation-date tracking.

The grower sets a single plantation (transplant) date from the dashboard.
"Growth Days" (a.k.a. DAP - days after planting) is derived from it instead
of being typed in per-reading or sent by the ESP32. This removes a value
that was previously always wrong (the firmware never sent it, so it
silently defaulted to a fixed 14 on every single reading).

Growth day is stored for record-keeping but no longer drives the decision
pipeline itself (decision.py's Level 3 safe ranges are fixed, not
growth-stage dependent) -- revisit this if the PFE later calls for
stage-aware ranges.
"""
from datetime import date, datetime

from .db_models import PlantationConfig
from .extensions import db

_CONFIG_ID = 1


def get_plantation_config() -> PlantationConfig:
    """Return the single plantation-config row, creating it (today) if absent."""
    config = PlantationConfig.query.get(_CONFIG_ID)

    if config is None:
        config = PlantationConfig(id=_CONFIG_ID, plantation_date=date.today())
        db.session.add(config)
        db.session.commit()

    return config


def get_plantation_date() -> date:
    return get_plantation_config().plantation_date


def set_plantation_date(new_date: date) -> PlantationConfig:
    config = get_plantation_config()
    config.plantation_date = new_date
    config.updated_at = datetime.utcnow()
    db.session.commit()
    return config


def compute_growth_days(plantation_date: date | None = None, today: date | None = None) -> int:
    """Days after planting, 1-indexed (the planting day itself counts as day 1).

    Clamped to a minimum of 1 so a plantation date accidentally set in the
    future never produces a zero/negative growth day.
    """
    if plantation_date is None:
        plantation_date = get_plantation_date()

    if today is None:
        today = date.today()

    delta_days = (today - plantation_date).days + 1
    return max(1, delta_days)
