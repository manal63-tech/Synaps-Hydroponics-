"""Runtime integration for the One-Class SVM exported by the analysis notebook."""
import math
import pickle
from pathlib import Path
from typing import Any, Dict, Tuple

import pandas as pd
from flask import current_app

from .rule_config import get_rule_metadata

_MODEL_CACHE_KEY = "hydromon_ocsvm_artifact"
_FEATURES = ("pH", "TDS", "DHT_temp", "DHT_humidity", "water_temp")


def _validate_artifact(artifact: Any) -> Tuple[Any, Dict[str, Any]]:
    if not isinstance(artifact, dict):
        raise ValueError("Le fichier modèle doit contenir un dictionnaire.")

    model = artifact.get("model")
    meta = artifact.get("meta")
    if not callable(getattr(model, "score_samples", None)):
        raise ValueError("Le modèle exporté ne fournit pas score_samples().")
    if not isinstance(meta, dict) or tuple(meta.get("features", ())) != _FEATURES:
        raise ValueError("Les caractéristiques du modèle ne correspondent pas au pipeline du notebook.")

    try:
        threshold = float(meta["threshold"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Le seuil calibré est absent ou invalide.") from exc
    if not math.isfinite(threshold):
        raise ValueError("Le seuil calibré doit être un nombre fini.")

    configured_bounds = {
        rule["field"]: (float(rule["min"]), float(rule["max"]))
        for rule in get_rule_metadata()["physical_rules"]["sensors"].values()
    }
    bounds = meta.get("bounds")
    if not isinstance(bounds, dict):
        raise ValueError("Les métadonnées bounds sont absentes.")
    for feature in _FEATURES:
        try:
            lower, upper = (float(value) for value in bounds[feature])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Les limites bounds de {feature} sont invalides.") from exc
        if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
            raise ValueError(f"Les limites bounds de {feature} sont invalides.")
        if (lower, upper) != configured_bounds[feature]:
            raise ValueError(f"Les limites physiques de {feature} diffèrent des règles configurées.")

    safe_ranges = meta.get("safe_ranges")
    if not isinstance(safe_ranges, dict):
        raise ValueError("Les métadonnées safe_ranges sont absentes.")
    for feature, values in safe_ranges.items():
        try:
            lower, upper = (float(value) for value in values)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"La plage safe_ranges de {feature} est invalide.") from exc
        if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
            raise ValueError(f"La plage safe_ranges de {feature} est invalide.")
    meta = dict(meta)
    meta["threshold"] = threshold
    return model, meta


def _load_artifact() -> Dict[str, Any]:
    cached = current_app.extensions.get(_MODEL_CACHE_KEY)
    if cached is not None:
        return cached

    model_path = Path(current_app.config["OCSVM_MODEL_PATH"])
    if not model_path.is_file():
        artifact_state = {
            "available": False,
            "status": "artifact_missing",
            "model_name": "One-Class SVM",
            "reason": f"Fichier modèle absent : {model_path.name}",
        }
        current_app.extensions[_MODEL_CACHE_KEY] = artifact_state
        return artifact_state

    try:
        with model_path.open("rb") as handle:
            model, meta = _validate_artifact(pickle.load(handle))
    except (
        OSError,
        pickle.UnpicklingError,
        EOFError,
        AttributeError,
        ImportError,
        ValueError,
        TypeError,
    ):
        current_app.logger.exception("Unable to load the One-Class SVM artifact at %s", model_path)
        artifact_state = {
            "available": False,
            "status": "error",
            "model_name": "One-Class SVM",
            "reason": "Le fichier modèle est illisible ou incompatible. Consultez les journaux serveur.",
        }
        current_app.extensions[_MODEL_CACHE_KEY] = artifact_state
        return artifact_state

    artifact_state = {
        "available": True,
        "status": "ready",
        "model_name": "One-Class SVM",
        "model": model,
        "meta": meta,
        "reason": None,
    }
    current_app.extensions[_MODEL_CACHE_KEY] = artifact_state
    return artifact_state


def get_model_status() -> Dict[str, Any]:
    """Return model readiness and only the notebook metadata useful to the UI."""
    artifact = _load_artifact()
    status = {
        key: value for key, value in artifact.items()
        if key not in ("model", "meta")
    }
    meta = artifact.get("meta")
    if meta is not None:
        status.update({
            "features": list(meta["features"]),
            "threshold": meta["threshold"],
            "validation_quantile": meta.get("val_quantile"),
            "training_rows": meta.get("train_rows"),
            "sessions": meta.get("sessions"),
        })
    else:
        status["features"] = list(_FEATURES)
    return status


def predict_anomaly(reading: Dict[str, Any]) -> Dict[str, Any]:
    """Return an experimental statistical anomaly result, separate from safety rules."""
    artifact = _load_artifact()
    if not artifact["available"]:
        return {
            "available": False,
            "status": artifact["status"],
            "score": None,
            "threshold": None,
            "anomaly": None,
            "reasons": [artifact["reason"]],
        }

    meta = artifact["meta"]
    values: Dict[str, float] = {}
    for feature in meta["features"]:
        try:
            value = float(reading[feature])
        except (KeyError, TypeError, ValueError):
            return {
                "available": True,
                "status": "incomplete",
                "score": None,
                "threshold": meta["threshold"],
                "anomaly": None,
                "reasons": [f"{feature} manquant ou non numérique."],
            }

        lower, upper = meta["bounds"][feature]
        if not math.isfinite(value) or not lower <= value <= upper:
            return {
                "available": True,
                "status": "invalid",
                "score": None,
                "threshold": meta["threshold"],
                "anomaly": None,
                "reasons": [f"{feature} hors limites physiques [{lower}, {upper}]."],
            }
        values[feature] = value

    if values["DHT_temp"] == 20.0 and values["DHT_humidity"] == 70.0:
        return {
            "available": True,
            "status": "invalid",
            "score": None,
            "threshold": meta["threshold"],
            "anomaly": None,
            "reasons": ["DHT : valeur par défaut (20 °C / 70 %) interprétée comme une erreur de lecture."],
        }

    try:
        score = float(-artifact["model"].score_samples(
            pd.DataFrame([values], columns=meta["features"])
        )[0])
    except (ValueError, RuntimeError, TypeError, AttributeError):
        current_app.logger.exception("One-Class SVM inference failed")
        return {
            "available": True,
            "status": "error",
            "score": None,
            "threshold": meta["threshold"],
            "anomaly": None,
            "reasons": ["L'inférence IA a échoué. Consultez les journaux serveur."],
        }

    if not math.isfinite(score):
        current_app.logger.error("One-Class SVM returned a non-finite anomaly score")
        return {
            "available": True,
            "status": "error",
            "score": None,
            "threshold": meta["threshold"],
            "anomaly": None,
            "reasons": ["Le modèle a renvoyé un score invalide."],
        }

    is_anomaly = score > meta["threshold"]
    out_of_range = [
        f"{feature} hors plage agronomique [{lower:g}, {upper:g}]"
        for feature, (lower, upper) in meta["safe_ranges"].items()
        if not lower <= values[feature] <= upper
    ]
    status = "critical" if out_of_range else "investigate" if is_anomaly else "normal"
    reasons = out_of_range
    if is_anomaly:
        reasons.append("Combinaison statistiquement rare selon le One-Class SVM.")

    return {
        "available": True,
        "status": status,
        "score": round(score, 4),
        "threshold": meta["threshold"],
        "anomaly": is_anomaly,
        "reasons": reasons,
    }
