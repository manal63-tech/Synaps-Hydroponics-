"""JSON API routes."""
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, send_file

from ..services import (
    export_readings_excel,
    get_history,
    get_latest,
    get_led_info,
    get_plantation_info,
    log_invalid_payload,
    process_payload,
    save_reading,
    update_led_settings,
    update_plantation_date,
)

api_bp = Blueprint("api", __name__)


@api_bp.route("/predict", methods=["POST"])
def predict():
    data = request.get_json(silent=True)

    if data is None:
        log_invalid_payload(None, "JSON invalide ou manquant.")
        return jsonify({
            "error": "JSON invalide ou manquant."
        }), 400

    try:
        result = process_payload(data)
    except Exception as exc:
        log_invalid_payload(data, exc)
        return jsonify({
            "error": str(exc)
        }), 400

    save_reading(result)

    return jsonify(result)


@api_bp.route("/api/sensor-data", methods=["POST"])
def sensor_data():
    return predict()


@api_bp.route("/api/latest", methods=["GET"])
def api_latest():
    latest_result = get_latest()

    if latest_result is None:
        return jsonify({
            "status": "waiting",
            "message": "Aucune donnee recue pour le moment."
        })

    return jsonify(latest_result)


@api_bp.route("/api/history", methods=["GET"])
def api_history():
    start_value = request.args.get("start")
    end_value = request.args.get("end")
    if bool(start_value) != bool(end_value):
        return jsonify({"error": "Les bornes start et end doivent etre fournies ensemble."}), 400

    try:
        start = _parse_history_bound(start_value) if start_value else None
        end = _parse_history_bound(end_value) if end_value else None
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if start is not None and end is not None and start >= end:
        return jsonify({"error": "La borne start doit preceder la borne end."}), 400

    return jsonify(get_history(start=start, end=end))


def _parse_history_bound(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Date invalide. Utilisez un horodatage ISO-8601.") from exc

    if parsed.tzinfo is None:
        raise ValueError("Les horodatages doivent inclure leur fuseau horaire.")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


@api_bp.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({
        "status": "running",
        "message": "Flask API is working"
    })


# --- Plantation date --------------------------------------------------

@api_bp.route("/api/plantation-date", methods=["GET"])
def api_get_plantation_date():
    return jsonify(get_plantation_info())


@api_bp.route("/api/plantation-date", methods=["POST"])
def api_set_plantation_date():
    data = request.get_json(silent=True) or {}

    try:
        result = update_plantation_date(data.get("plantation_date"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


# --- LED automation -----------------------------------------------

@api_bp.route("/api/led", methods=["GET"])
def api_get_led():
    return jsonify(get_led_info())


@api_bp.route("/api/led", methods=["POST"])
def api_set_led():
    data = request.get_json(silent=True) or {}

    try:
        result = update_led_settings(
            mode=data.get("mode"),
            manual_state=data.get("manual_state"),
            on_hour=data.get("on_hour"),
            off_hour=data.get("off_hour"),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


# --- Excel export -------------------------------------------------

@api_bp.route("/api/export/excel", methods=["GET"])
def api_export_excel():
    password = current_app.config.get("EXPORT_PASSWORD", "manalpfe2026")
    token = request.args.get("token") or request.headers.get("X-EXPORT-TOKEN")

    if token != password:
        return jsonify({"error": "Acces refuse, token invalide."}), 403

    buffer = export_readings_excel()
    filename = f"hydromon_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
