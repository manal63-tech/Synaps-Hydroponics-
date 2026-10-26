"""Integration checks for the Hydromon Flask backend and rule-based decisions."""
import json
from datetime import datetime, timedelta

from hydromon import create_app
from hydromon.ai import predict_anomaly
from hydromon.config import Config
from hydromon.extensions import db
from hydromon.db_models import Reading
from hydromon import services

def test_pipeline():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        OCSVM_MODEL_PATH = "missing-ocsvm-model.pkl"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)

    with app.app_context():
        db.create_all()
        client = app.test_client()

        # 1. Health check
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.get_json()["status"] == "running"
        print("✅ 1. Health check passed.")

        # 2. Test Nominal (Ideal) Conditions
        ideal_payload = {
            "temperature": 22.0,
            "humidity": 65.0,
            "tds": 750.0,
            "ph": 6.0,
            "water_level": 75.0,
            "water_temperature": 19.5,
            "temperature_outside": 24.0,
            "humidity_outside": 55.0,
            "actuator_status": {"led": True, "led_mode": "auto", "pump": True, "fan": False}
        }
        res = client.post("/predict", json=ideal_payload)
        assert res.status_code == 200
        data = res.get_json()
        assert data["final_decision"] == "Healthy"
        assert data["actuator_command"] == "NONE"
        assert data["severity"] == "normal"
        assert data["level3"]["ph_status"] == "ok"
        assert "dap" in data
        assert "automation" in data
        print("✅ 2. Nominal conditions test passed.")

        for tds_value, expected_status, humidity_value in (
            (499.9, "critical", 65.1),
            (500.0, "attention", 65.2),
            (560.0, "ok", 65.3),
            (900.0, "ok", 65.4),
            (900.1, "attention", 65.5),
        ):
            res = client.post("/predict", json={
                **ideal_payload,
                "tds": tds_value,
                "humidity": humidity_value,
            })
            assert res.status_code == 200
            result = res.get_json()
            assert result["level3"]["tds_status"] == expected_status
            has_tds_alert = any(
                recommendation["type"] == "tds"
                for recommendation in result["recommendations"]
            )
            assert has_tds_alert == (expected_status != "ok")

        # 3. Test High pH (Alkalinization)
        high_ph_payload = {
            "temperature": 23.0,
            "humidity": 65.6,
            "tds": 750.0,
            "ph": 6.7,
            "water_level": 70.0,
            "water_temperature": 20.0
        }
        res = client.post("/predict", json=high_ph_payload)
        assert res.status_code == 200
        data = res.get_json()
        assert data["final_decision"] == "Investigate"
        assert data["actuator_command"] == "MANUAL_REVIEW"
        assert data["level3"]["ph_status"] == "attention"
        assert any(rec["type"] == "ph" for rec in data["recommendations"])
        print("✅ 3. High pH test passed.")

        # 4. Test Low TDS (Nutrient Deficiency)
        low_tds_payload = {
            "temperature": 22.0,
            "humidity": 64.9,
            "tds": 530.0,
            "ph": 6.0,
            "water_level": 70.0,
            "water_temperature": 20.0
        }
        res = client.post("/predict", json=low_tds_payload)
        assert res.status_code == 200
        data = res.get_json()
        assert data["final_decision"] == "Investigate"
        assert data["actuator_command"] == "MANUAL_REVIEW"
        assert data["level3"]["tds_status"] == "attention"
        assert any(rec["type"] == "tds" for rec in data["recommendations"])
        print("✅ 4. Low TDS test passed.")

        # 5. Test Hot Water (> 24°C)
        hot_water_payload = {
            "temperature": 26.0,
            "humidity": 70.0,
            "tds": 750.0,
            "ph": 6.0,
            "water_level": 70.0,
            "water_temperature": 25.0
        }
        res = client.post("/predict", json=hot_water_payload)
        assert res.status_code == 200
        data = res.get_json()
        assert data["final_decision"] == "Investigate"
        assert data["level3"]["water_temp_status"] == "attention"
        assert any(rec["type"] == "water_temp" for rec in data["recommendations"])
        print("✅ 5. Hot water (>24°C) test passed.")

        # 6. Test Physical Fault (Negative TDS / Sensor Failure)
        faulty_payload = {
            "temperature": 22.0,
            "humidity": 65.0,
            "tds": -150.0,
            "ph": 6.0,
            "water_level": 70.0,
            "water_temperature": 20.0
        }
        res = client.post("/predict", json=faulty_payload)
        assert res.status_code == 200
        data = res.get_json()
        assert data["final_decision"] == "Sensor Fault"
        assert data["actuator_command"] == "MANUAL_HARDWARE_CHECK"
        assert data["severity"] == "sensor_fault"
        assert "tds" in data["level1"]["invalid_sensors"]
        print("✅ 6. Physical sensor fault test passed.")

        # 7. Test /api/latest and /api/history
        res_latest = client.get("/api/latest")
        assert res_latest.status_code == 200
        assert res_latest.get_json()["final_decision"] == "Sensor Fault"

        res_hist = client.get("/api/history")
        assert res_hist.status_code == 200
        assert isinstance(res_hist.get_json(), list)
        print("✅ 7. Real-time cache & history API endpoints passed.")

        # 8. Test Dashboard route /
        res_dash = client.get("/")
        assert res_dash.status_code == 200
        assert b"Synaps Hydro" in res_dash.data
        assert b'"ideal"' in res_dash.data
        assert b'560' in res_dash.data
        print("✅ 8. Dashboard HTML rendering passed.")

    print("\n🎉 ALL 8 INTEGRATION TESTS COMPLETED SUCCESSFULLY!")

def test_latest_waits_for_new_reading_instead_of_showing_stale_database_data():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    previous_cache = services._latest_reading_cache

    try:
        with app.app_context():
            db.session.add(Reading(
                temperature=23.0,
                humidity=65.0,
                tds=800.0,
                ph=0.0,
                growth_days=1,
                final_decision="Sensor Fault",
                actuator_command="MANUAL_HARDWARE_CHECK",
                result_json=json.dumps({"timestamp": "2026-09-07T14:12:51"}),
            ))
            db.session.commit()
            services._latest_reading_cache = None

            response = app.test_client().get("/api/latest")

            assert response.status_code == 200
            assert response.get_json()["status"] == "waiting"
    finally:
        services._latest_reading_cache = previous_cache


def test_partial_dht_reading_is_available_live_and_missing_sensors_are_empty():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    previous_cache = services._latest_reading_cache

    try:
        with app.app_context():
            services._latest_reading_cache = None
            client = app.test_client()
            response = client.post("/api/sensor-data", json={
                "temperature": 23.5,
                "humidity": 61.0,
            })

            assert response.status_code == 200
            latest = client.get("/api/latest").get_json()
            assert latest["sensor_data"]["temperature"] == 23.5
            assert latest["sensor_data"]["humidity"] == 61.0
            assert latest["sensor_data"]["ph"] is None
            assert latest["sensor_data"]["tds"] is None
            assert "level2" not in latest
            assert "anomaly" not in latest
            assert latest["severity"] == "normal"
            assert latest["recommendations"][0]["type"] == "normal"
            assert Reading.query.count() == 0
    finally:
        services._latest_reading_cache = previous_cache


def test_history_api_filters_custom_utc_interval_and_returns_recorded_time():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    start = datetime(2026, 10, 9, 10)

    with app.app_context():
        db.create_all()
        for hour in range(3):
            db.session.add(Reading(
                created_at=start.replace(hour=10 + hour),
                temperature=22.0,
                humidity=60.0,
                tds=700.0,
                ph=6.0,
                growth_days=1,
                final_decision="Healthy",
                actuator_command="NONE",
                result_json=json.dumps({
                    "timestamp": f"2026-10-09T{10 + hour:02}:00:00",
                    "severity": "normal",
                }),
            ))
        db.session.commit()

        response = app.test_client().get(
            "/api/history?start=2026-10-09T11:00:00%2B00:00"
            "&end=2026-10-09T13:00:00%2B00:00"
        )

        assert response.status_code == 200
        history = response.get_json()
        assert [row["timestamp"] for row in history] == [
            "2026-10-09T11:00:00",
            "2026-10-09T12:00:00",
        ]
        assert [row["recorded_at"] for row in history] == [
            "2026-10-09T11:00:00Z",
            "2026-10-09T12:00:00Z",
        ]


def test_history_api_rejects_incomplete_or_invalid_custom_ranges():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    client = app.test_client()

    assert client.get("/api/history?start=2026-10-09T10:00:00Z").status_code == 400
    assert client.get(
        "/api/history?start=not-a-date&end=2026-10-09T12:00:00Z"
    ).status_code == 400
    assert client.get(
        "/api/history?start=2026-10-09T12:00:00Z&end=2026-10-09T10:00:00Z"
    ).status_code == 400


def test_persistence_records_decision_transitions_without_repeating_every_packet():
    previous = (
        services._last_db_save_time,
        services._last_db_severity,
        services._last_db_decision,
    )
    result = {
        "severity": "investigate",
        "final_decision": "Investigate",
    }

    try:
        services._last_db_save_time = datetime.utcnow()
        services._last_db_severity = "investigate"
        services._last_db_decision = "Investigate"
        assert not services.should_persist_to_db(result)
        assert services.should_persist_to_db({
            "severity": "critical",
            "final_decision": "Critical",
        })
        assert services.should_persist_to_db({
            "severity": "normal",
            "final_decision": "Healthy",
        })

        services._last_db_save_time = datetime.utcnow() - timedelta(minutes=15)
        assert services.should_persist_to_db(result)
    finally:
        (
            services._last_db_save_time,
            services._last_db_severity,
            services._last_db_decision,
        ) = previous


def test_physically_invalid_context_sensors_create_sensor_fault_alerts():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    base = {
        "temperature": 22.0,
        "humidity": 65.0,
        "tds": 700.0,
        "ph": 6.0,
        "water_temperature": 20.0,
    }

    with app.app_context():
        db.create_all()
        for sensor_data, field in (
            ({"water_level": -2.0}, "water_level"),
            ({"humidity_outside": 101.0}, "outside_humidity"),
        ):
            result = services.process_payload({**base, **sensor_data})
            assert result["severity"] == "sensor_fault"
            assert field in result["level1"]["context_issues"]
            assert result["recommendations"][0]["type"] == "sensor_fault"


def test_metadata_rules_cover_physical_agronomic_missing_frozen_and_jump_cases():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)

    with app.app_context():
        state = app.extensions["hydromon_sensor_rule_state"] = {}
        base = {
            "temperature": 22.0,
            "humidity": 60.0,
            "tds": 700.0,
            "ph": 6.0,
            "water_temperature": 20.0,
            "water_level": 50.0,
        }

        impossible = services.process_payload({**base, "ph": -1.0})
        assert impossible["severity"] == "sensor_fault"
        assert "ph" in impossible["level1"]["invalid_sensors"]
        assert "pH mesuré : -1" in impossible["recommendations"][0]["message"]
        assert "0–14 pH" in impossible["recommendations"][0]["message"]

        state.clear()
        attention = services.process_payload({**base, "ph": 5.4})
        assert attention["severity"] == "investigate"
        assert attention["level3"]["ph_status"] == "attention"
        assert attention["recommendations"][0]["sensor"] == "ph"
        assert "idéal:" in attention["recommendations"][0]["message"]

        state.clear()
        critical = services.process_payload({**base, "ph": 7.0})
        assert critical["severity"] == "critical"
        assert critical["level3"]["ph_status"] == "critical"

        state.clear()
        missing = services.process_payload({"temperature": 22.0})
        assert missing["severity"] == "normal"
        assert missing["sensor_data"]["ph"] is None
        assert missing["level3"]["ph_status"] is None
        assert not any(item["type"] == "incomplete_data" for item in missing["recommendations"])

        for payload, rule_key, expected in (
            ({"ph": 5.2}, "ph_status", "attention"),
            ({"ph": 5.19}, "ph_status", "critical"),
            ({"ph": 6.8}, "ph_status", "attention"),
            ({"ph": 6.81}, "ph_status", "critical"),
            ({"tds": 500.0}, "tds_status", "attention"),
            ({"tds": 499.9}, "tds_status", "critical"),
            ({"tds": 1000.0}, "tds_status", "attention"),
            ({"tds": 1000.1}, "tds_status", "critical"),
            ({"water_temperature": 25.0}, "water_temp_status", "attention"),
            ({"water_temperature": 27.0}, "water_temp_status", "critical"),
            ({"water_temperature": 15.0}, "water_temp_status", "attention"),
            ({"water_temperature": 14.9}, "water_temp_status", "critical"),
            ({"temperature": 29.0}, "air_temp_status", "critical"),
            ({"temperature": 28.0}, "air_temp_status", "attention"),
            ({"humidity": 35.0}, "air_humidity_status", "critical"),
            ({"humidity": 40.0}, "air_humidity_status", "attention"),
            ({"humidity": 80.0}, "air_humidity_status", "attention"),
            ({"water_level": 20.0}, "water_level_status", "attention"),
            ({"water_level": 15.0}, "water_level_status", "critical"),
        ):
            state.clear()
            result = services.process_payload(payload)
            assert result["level3"][rule_key] == expected

        state.clear()
        freeze_n = app.extensions["hydromon_rule_metadata"]["physical_rules"][
            "frozen_detection"
        ]["consecutive_readings"]
        frozen = None
        for _ in range(freeze_n):
            frozen = services.process_payload({"ph": 6.0})
        assert frozen is not None
        assert frozen["severity"] == "sensor_fault"
        assert frozen["level1"]["frozen_sensors"]["ph"]["repeat_count"] == freeze_n

        state.clear()
        services.process_payload({"ph": 6.0})
        state["ph"]["timestamp"] -= timedelta(seconds=15)
        state["ph"]["jump_timestamp"] -= timedelta(seconds=15)
        jump = services.process_payload({"ph": 4.4})
        assert jump["severity"] == "sensor_fault"
        assert "ph" in jump["level1"]["impossible_jumps"]


def test_history_removes_legacy_ml_result_and_recalculates_rule_decision():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    with app.app_context():
        db.create_all()
        db.session.add(Reading(
            temperature=22.0,
            humidity=65.0,
            tds=800.0,
            ph=6.0,
            water_temperature=20.0,
            growth_days=1,
            final_decision="Investigate",
            actuator_command="MANUAL_REVIEW",
            anomaly="True",
            anomaly_score=1.5,
            result_json=json.dumps({
                "timestamp": "2026-10-12T10:00:00",
                "severity": "investigate",
                "final_decision": "Investigate",
                "actuator_command": "MANUAL_REVIEW",
                "ai_detection": {"available": True, "anomaly": True, "score": 1.5},
                "anomaly": True,
                "anomaly_score": 1.5,
                "recommendations": [{
                    "type": "ai_anomaly",
                    "level": "warning",
                    "title": "Combinaison statistiquement rare",
                    "message": "A verifier",
                }],
                "sensor_data": {
                    "temperature": 22.0,
                    "humidity": 65.0,
                    "tds": 800.0,
                    "ph": 6.0,
                    "water_temperature": 20.0,
                    "water_level": None,
                    "temperature_outside": None,
                    "humidity_outside": None,
                },
            }),
        ))
        db.session.commit()

        response = app.test_client().get("/api/history")
        assert response.status_code == 200
        [historical] = response.get_json()
        assert historical["severity"] == "normal"
        assert historical["final_decision"] == "Healthy"
        assert "ai_detection" not in historical
        assert "anomaly" not in historical
        assert "anomaly_score" not in historical
        assert all(item["type"] != "ai_anomaly" for item in historical["recommendations"])


def test_dashboard_returns_rule_decision_without_exposing_ml_result():
    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        OCSVM_MODEL_PATH = "missing-ocsvm-model.pkl"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    client = app.test_client()

    assert client.get("/api/ai/status").status_code == 404

    response = client.post("/predict", json={
        "temperature": 22.0,
        "humidity": 65.0,
        "tds": 800.0,
        "ph": 6.0,
        "water_temperature": 20.0,
    })
    result = response.get_json()
    assert response.status_code == 200
    assert result["final_decision"] == "Healthy"
    assert "ai_detection" not in result
    assert "anomaly" not in result


def test_ai_prediction_uses_notebook_threshold_and_keeps_rules_decision_separate():
    class FixedScoreModel:
        def score_samples(self, frame):
            assert list(frame.columns) == [
                "pH", "TDS", "DHT_temp", "DHT_humidity", "water_temp"
            ]
            return [-1.5]

    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        OCSVM_MODEL_PATH = "unused-ocsvm-model.pkl"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    app.extensions["hydromon_ocsvm_artifact"] = {
        "available": True,
        "status": "ready",
        "model_name": "One-Class SVM",
        "model": FixedScoreModel(),
        "meta": {
            "features": ["pH", "TDS", "DHT_temp", "DHT_humidity", "water_temp"],
            "threshold": 1.0,
            "bounds": {
                "pH": (0, 14),
                "TDS": (0, 3000),
                "DHT_temp": (-10, 60),
                "DHT_humidity": (0, 100),
                "water_temp": (0, 45),
            },
            "safe_ranges": {
                "pH": (5.5, 6.5),
                "TDS": (500, 1500),
                "water_temp": (18, 26),
            },
            "train_rows": 10,
            "sessions": [16],
        },
        "reason": None,
    }
    reading = {
        "pH": 6.0,
        "TDS": 800.0,
        "DHT_temp": 22.0,
        "DHT_humidity": 65.0,
        "water_temp": 20.0,
    }

    with app.app_context():
        ai_result = predict_anomaly(reading)
        assert ai_result["status"] == "investigate"
        assert ai_result["anomaly"] is True
        assert ai_result["score"] == 1.5

        out_of_range = predict_anomaly({**reading, "pH": 7.0})
        assert out_of_range["status"] == "critical"
        assert out_of_range["anomaly"] is True

        incomplete = predict_anomaly({**reading, "TDS": None})
        assert incomplete["status"] == "incomplete"
        assert incomplete["score"] is None


def test_dashboard_rule_decision_is_independent_of_loaded_ai_model():
    class FixedScoreModel:
        def score_samples(self, frame):
            raise AssertionError("Dashboard pipeline must not run the AI model")

    class IsolatedConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        OCSVM_MODEL_PATH = "unused-ocsvm-model.pkl"
        TESTING = True
        ENABLE_EXPORT_SCHEDULER = False

    app = create_app(IsolatedConfig)
    app.extensions["hydromon_ocsvm_artifact"] = {
        "available": True,
        "status": "ready",
        "model_name": "One-Class SVM",
        "model": FixedScoreModel(),
        "meta": {
            "features": ["pH", "TDS", "DHT_temp", "DHT_humidity", "water_temp"],
            "threshold": 1.0,
            "bounds": {
                "pH": (0, 14),
                "TDS": (0, 3000),
                "DHT_temp": (-10, 60),
                "DHT_humidity": (0, 100),
                "water_temp": (0, 45),
            },
            "safe_ranges": {
                "pH": (5.5, 6.5),
                "TDS": (500, 1500),
                "water_temp": (18, 26),
            },
        },
        "reason": None,
    }

    with app.app_context():
        result = services.process_payload({
            "temperature": 22.0,
            "humidity": 65.0,
            "tds": 800.0,
            "ph": 6.0,
            "water_temperature": 20.0,
        })
        assert result["severity"] == "normal"
        assert result["final_decision"] == "Healthy"
        assert result["actuator_command"] == "NONE"
        assert "ai_detection" not in result

        critical = services.process_payload({
            "temperature": 22.0,
            "humidity": 65.0,
            "tds": 800.0,
            "ph": 7.0,
            "water_temperature": 20.0,
        })
        assert critical["severity"] == "critical"
        assert critical["final_decision"] == "Critical"
        assert critical["actuator_command"] == "MANUAL_REVIEW"
        assert "ai_detection" not in critical

        default_dht = services.process_payload({
            "temperature": 20.0,
            "humidity": 70.0,
            "tds": 800.0,
            "ph": 6.0,
            "water_temperature": 20.0,
        })
        assert default_dht["severity"] == "normal"
        assert default_dht["final_decision"] == "Healthy"
        assert default_dht["actuator_command"] == "NONE"
        assert "ai_detection" not in default_dht


if __name__ == "__main__":
    test_pipeline()
