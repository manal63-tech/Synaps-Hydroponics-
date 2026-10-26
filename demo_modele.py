"""Démonstration reproductible du modèle avec des mesures entièrement fictives."""
from pathlib import Path

from hydromon import create_app
from hydromon.ai import get_model_status, predict_anomaly
from hydromon.config import Config
from hydromon.services import process_payload


class DemoConfig(Config):
    """Keep this demonstration isolated from the real application database."""

    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    OCSVM_MODEL_PATH = Path(__file__).resolve().parent / "ocsvm_model.pkl"
    ENABLE_EXPORT_SCHEDULER = False


CASES = (
    {
        "name": "Nominal - valeurs fictives",
        "payload": {
            "temperature": 23.8,
            "humidity": 79.85,
            "tds": 1054.5,
            "ph": 5.96,
            "water_level": 75,
            "water_temperature": 22.78,
        },
        "model_status": "normal",
        "anomaly": False,
        "decision": "Healthy",
    },
    {
        "name": "Combinaison statistiquement rare - valeurs fictives",
        "payload": {
            "temperature": 35,
            "humidity": 65,
            "tds": 800,
            "ph": 6,
            "water_level": 75,
            "water_temperature": 20,
        },
        "model_status": "investigate",
        "anomaly": True,
        "decision": "Healthy",
    },
    {
        "name": "pH hors plage agronomique - valeurs fictives",
        "payload": {
            "temperature": 22,
            "humidity": 65,
            "tds": 800,
            "ph": 4.5,
            "water_level": 75,
            "water_temperature": 20,
        },
        "model_status": "critical",
        "anomaly": True,
        "decision": "Investigate",
    },
    {
        "name": "Temperature d'eau hors limites physiques - valeurs fictives",
        "payload": {
            "temperature": 22,
            "humidity": 65,
            "tds": 800,
            "ph": 6,
            "water_level": 75,
            "water_temperature": 50,
        },
        "model_status": "invalid",
        "anomaly": None,
        "decision": "Sensor Fault",
    },
)


def main() -> None:
    app = create_app(DemoConfig)

    with app.app_context():
        model_status = get_model_status()
        if not model_status["available"]:
            raise SystemExit(
                "Modèle indisponible : "
                f"{model_status.get('reason', 'cause inconnue')}"
            )

        print("DEMO HYDROMON - DONNEES FICTIVES, AUCUNE MESURE REELLE")
        print(
            f"Modele : {model_status['model_name']} | "
            f"statut : {model_status['status']} | "
            f"lignes d'entrainement : {model_status['training_rows']}"
        )
        print(
            f"Seuil calibre (quantile "
            f"{model_status['validation_quantile']:.0%}) : "
            f"{model_status['threshold']:.4f}"
        )
        print("Une anomalie IA signifie 'combinaison rare', pas 'panne certaine'.\n")

        failures = []
        for case in CASES:
            payload = case["payload"]
            result = process_payload(payload)
            detection = predict_anomaly({
                "pH": payload.get("ph"),
                "TDS": payload.get("tds"),
                "DHT_temp": payload.get("temperature"),
                "DHT_humidity": payload.get("humidity"),
                "water_temp": payload.get("water_temperature"),
            })
            passed = (
                detection["status"] == case["model_status"]
                and detection["anomaly"] is case["anomaly"]
                and result["final_decision"] == case["decision"]
            )
            marker = "OK" if passed else "ÉCHEC"
            print(
                f"[{marker}] {case['name']}\n"
                f"  Entrees : {case['payload']}\n"
                f"  PoC modèle (séparée) : {detection['status']} | "
                f"score : {detection['score']} | "
                f"seuil : {detection['threshold']}\n"
                f"  Décision dashboard par règles : {result['final_decision']} | "
                f"actionneur : {result['actuator_command']}"
            )
            if detection["reasons"]:
                print(f"  Explication : {'; '.join(detection['reasons'])}")
            if not passed:
                failures.append(case["name"])

        if failures:
            raise SystemExit(f"\nDémonstration en échec : {', '.join(failures)}")

        print("\nResultat : les 4 scenarios ont produit les resultats attendus.")
        print("Aucun releve n'a ete envoye a un capteur ni sauvegarde dans hydromon.db.")


if __name__ == "__main__":
    main()
