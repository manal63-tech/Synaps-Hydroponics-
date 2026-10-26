"""Inbound payload normalisation.

Accepts the various field aliases the sensor clients may send and produces a
canonical, validated payload dict.

``growth_days`` is intentionally NOT accepted here anymore: it is now
derived from the plantation date (see ``plantation.py``) at prediction time,
instead of being sent by the client. Previously the ESP32 never sent it at
all, so every single reading silently used the same fixed default (14) -
that bug is fixed by removing the field entirely.
"""

import math


def normalize_payload(data):
    sensor_aliases = {
        "temperature": ["temperature", "temp", "airTemp"],
        "humidity": ["humidity", "hum", "airHumidity"],
        "tds": ["tds", "TDS"],
        "ph": ["ph", "pH"],
        "water_level": ["water_level", "waterLevel"],
        "water_temperature": ["water_temperature", "waterTemperature", "waterTemp"],
        "temperature_outside": ["temperature_outside", "tempOutside", "airTempOutside"],
        "humidity_outside": ["humidity_outside", "humOutside", "airHumidityOutside"],
    }

    def _to_float(value, field_name):
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Valeur numerique invalide pour {field_name}: {value!r}")

        if math.isnan(result) or math.isinf(result):
            raise ValueError(f"Valeur numerique invalide pour {field_name}: {value!r}")

        return result

    def optional_float(aliases):
        found = next(
            (alias for alias in aliases if alias in data and data.get(alias) is not None), None
        )
        return None if found is None else _to_float(data[found], found)

    normalized = {
        canonical: optional_float(aliases)
        for canonical, aliases in sensor_aliases.items()
    }
    if all(value is None for value in normalized.values()):
        raise ValueError("Aucune mesure de capteur valide n'a ete fournie.")

    # The ESP32 reports the actual relay/pump/fan state in a nested
    # "actuator_status" object; also silently dropped before.
    actuator_status = data.get("actuator_status") or {}

    return {
        **normalized,
        "led_on": bool(actuator_status["led"]) if "led" in actuator_status else None,
        "led_mode": actuator_status.get("led_mode"),
        "pump_on": bool(actuator_status["pump"]) if "pump" in actuator_status else None,
        "fan_on": bool(actuator_status["fan"]) if "fan" in actuator_status else None,
    }
