# HydroMon

HydroMon validates live hydroponic sensor readings with deterministic physical
and agronomic safety rules. It can also load the One-Class SVM exported by
`OCSVM_Notebook_Final.ipynb` to score unusual combinations of five sensor
readings. The AI score is advisory: it does not replace the safety rules or
control actuators.

## Setup Instructions

### Prerequisites
- **Python 3.10+** installed on your system
- A terminal-friendly code editor (recommended: [VS Code](https://code.visualstudio.com/))

### Initial Setup

1. **Clone and navigate** to the project directory:
   ```bash
   cd HydroMon
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   ```

3. **Activate the virtual environment**:
   
   | Platform | Command |
   |----------|---------|
   | Windows  | `venv\Scripts\activate` |
   | macOS/Linux | `source venv/bin/activate` |

4. **Install dependencies** (includes the notebook's analysis and plotting packages):
   ```bash
   pip install -r requirements.txt
   ```

### Dashboard decisions

The dashboard, API responses, alerts, and actuator commands use only explicit
physical and agronomic rules. The One-Class SVM is not loaded or called during
normal dashboard operation, and does not contribute to live readings or
history. The decision logic in `hydromon/decision.py` evaluates the rule
configuration in `models/metadata.json`.

### Configuring sensor and lettuce rules

`models/metadata.json` is the single configuration source for physical sensor
bounds, lettuce agronomic bands, sensor-freeze count, maximum change rates,
source notes and French manual actions. The dashboard renders its rule summary
and sensor-card ranges from this file. After editing it, restart the Flask
application to reload the settings. A missing sensor value is ignored and
remains blank; it is never replaced with an estimated value. Values outside
physical bounds, readings frozen for the configured number of samples, and
impossible jumps are reported as sensor faults. Agronomic readings are
classified as ideal, attention or critical, with the overall status reflecting
the most severe result.

The default frozen-reading rule is eight identical consecutive samples
(approximately two minutes at the firmware's 15-second reporting interval).
The TDS bands assume the NaCl 0.5 conversion scale and must be validated by
calibrating the sensor with a reference solution.

The configured lettuce TDS bands are ideal at 560–900 ppm, attention at
500–560 or 900–1,000 ppm, and critical below 500 or above 1,000 ppm. They
assume the NaCl 0.5 scale (ppm ≈ EC × 500, with EC in mS/cm). UF/IFAS HS1422
reports 1.2–1.8 mS/cm and 560–840 ppm; the project bands are configurable and
must be calibrated against the actual sensor and nutrient solution before the
displayed ppm is treated as an accurate EC-derived value. Alerts ask for a
manual check and do not diagnose crop damage or automatically change the
solution.

The experimental model and its inference code remain available only for the
separate synthetic demonstration. The model artifact path can be overridden
with the `OCSVM_MODEL_PATH` environment variable. Its score is not a
probability and is not used to make system decisions.

The notebook's external `IoTData.csv` is not included in this repository. It
is required to retrain or reproduce the model. A model score is not a
probability, and a score above the notebook's 99th-percentile validation
threshold indicates only a statistically unusual combination. It does not
establish crop damage or prove predictive accuracy.

### Reproducible model demonstration

Run `python demo_modele.py` from the project root after installing the
dependencies and placing the exported `ocsvm_model.pkl` there. The script uses
four clearly labelled synthetic readings to exercise a nominal case, a
statistical anomaly, an agronomic limit violation, and a physically invalid
sensor value. It runs the rule-based dashboard decision and the model inference
separately, checks their expected outcomes, and uses an in-memory database so
it does not add demo readings to `hydromon.db`.

These invented inputs demonstrate software behavior only. They are not
measurements, training data, or evidence of predictive accuracy. The score is
not a probability; the threshold is the model's 99th-percentile validation
threshold, and the agronomic ranges should be supported by sources in the
project report.

### Grow-light control

The dashboard saves the requested light mode to the application database.
The ESP32 reads and applies the saved settings when it next submits a sensor
reading to the Flask server. If the dashboard reports that it cannot save the
setting, first check that the Flask server is running and reachable; if the
setting saves but the physical light does not change, check the ESP32
connection and its sensor-reading requests.

### Running the Application

1. **Apply database migrations**:
   ```bash
   flask db upgrade
   ```

2. **Set the Flask application environment** (if needed):
   
   | Platform | Command |
   |----------|---------|
   | Windows  | `set FLASK_APP=app.py` |
   | macOS/Linux | `export FLASK_APP=app.py` |

3. **Start the server so the ESP32 can reach it**:

   Find the computer's IPv4 address on the same Wi-Fi network as the ESP32
   (on Windows, run `ipconfig` and look under the Wi-Fi adapter). Start Flask
   on all network interfaces, on port 5000:

   ```bash
   flask --app app run --host 0.0.0.0 --port 5000
   ```

   `0.0.0.0` is only Flask's listening address; it allows other devices to
   reach the server through the computer's network interfaces. In
   `Synaps_hydro_pfe.ino`, `flaskServerUrl` must use the computer's actual
   Wi-Fi IPv4 address, for example
   `http://10.207.94.10:5000/api/sensor-data`. Do not use `0.0.0.0` or
   `127.0.0.1` as the ESP32 destination. Keep the computer and ESP32 on the
   same Wi-Fi/hotspot and allow inbound TCP port 5000 in Windows Firewall.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **"Package not found" errors** | Ensure your virtual environment is activated (`venv\Scripts\activate` or `source venv/bin/activate`) |
| **Database failure** | Verify that the `instance/` folder was automatically created. If not, create it manually. |
| **Port already in use** | Run with a different port: `flask run --debug --port=5001` |

---

## Additional Commands

| Command | Description |
|---------|-------------|
| `flask shell` | Opens an interactive Python shell with the app context |
| `flask db migrate -m "message"` | Generate a new migration |
| `flask db downgrade` | Rollback the last migration |
| `python -m pytest` | Run tests (if configured) |

---
