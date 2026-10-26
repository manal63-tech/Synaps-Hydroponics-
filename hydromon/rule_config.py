"""Load the editable physical and agronomic rules used by the application."""
import json
from pathlib import Path
from typing import Any, Dict

from flask import current_app, has_app_context

RULES_METADATA_PATH = Path(__file__).resolve().parents[1] / "models" / "metadata.json"


def load_rule_metadata() -> Dict[str, Any]:
    """Load rule metadata from models/metadata.json and surface config errors."""
    try:
        with RULES_METADATA_PATH.open(encoding="utf-8") as metadata_file:
            metadata = json.load(metadata_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Impossible de charger la configuration des regles "
            f"({RULES_METADATA_PATH}): {exc}"
        ) from exc

    if not isinstance(metadata, dict):
        raise ValueError("La configuration des regles doit etre un objet JSON.")
    for section in ("physical_rules", "agronomic_rules", "manual_actions"):
        if not isinstance(metadata.get(section), dict):
            raise ValueError(f"La section {section} est absente ou invalide.")
    if not isinstance(metadata["physical_rules"].get("sensors"), dict):
        raise ValueError("Les bornes physiques des capteurs sont absentes.")
    if not isinstance(metadata["agronomic_rules"].get("sensors"), dict):
        raise ValueError("Les seuils agronomiques des capteurs sont absents.")
    return metadata


def get_rule_metadata() -> Dict[str, Any]:
    """Return the app's loaded rule metadata, or load it outside an app context."""
    if has_app_context():
        configured = current_app.extensions.get("hydromon_rule_metadata")
        if configured is not None:
            return configured
    return load_rule_metadata()
