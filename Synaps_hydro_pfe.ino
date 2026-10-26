// ============================================================================
// SYNAPSE HYDRO - NFT lettuce greenhouse
// ESP32 + pH + TDS + DS18B20 + 2x DHT22 + water level + TFT ST7735
// Flask real-time communication + LED-only automation
//
// Pumps: ON 24h
// Fans : ON 24h
// LED  : AUTO 06:00-20:00 or manual ON/OFF from Flask dashboard
// ============================================================================

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include <time.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include "DHTesp.h"

// -------------------- WiFi / Flask --------------------
const char* ssid = "Redmi Note 14 Pro";
const char* password = "manalyahia1234";
const char* flaskServerUrl = "http://10.207.94.10:5000/api/sensor-data";

const long TIMEZONE_OFFSET_SEC = 3600;
const char* NTP_SERVER = "pool.ntp.org";

// -------------------- Pins --------------------
#define DHTPIN_INSIDE   15
#define DHTPIN_OUTSIDE  16
#define ONE_WIRE_BUS    4

const int phPin = 34;
const int tdsPin = 35;
const int trigPin = 5;
const int echoPin = 18;


#define RELAY_PIN_LED  17
#define RELAY_ACTIVE_LOW false

#define TFT_CS   14
#define TFT_DC   27 // DATA A0
#define TFT_RST  26 // RESET 
#define TFT_MOSI 13 //SDA 
#define TFT_SCLK 12 // SCK
#define TFT_BL   33 // LED
#define TFT_INIT_TYPE INITR_BLACKTAB
#define ST7735_DARKGREY 0x7BEF

// -------------------- Sensor objects --------------------
DHTesp dhtInside;
DHTesp dhtOutside;
OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature ds18b20(&oneWire);
Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_MOSI, TFT_SCLK, TFT_RST);

// -------------------- Calibration --------------------
const float Vref = 3.3f;
const float ADC_MAX = 4095.0f;
const float CAL_V4 = 2.657f;
const float CAL_V12 = 1.306f;
//const float CAL_V4 = 2.010f;
//const float CAL_V12 = 1.414f;
const float pente_V_pH = (CAL_V4 - CAL_V12) / 8.0f;
const float V_dist = CAL_V4 - 3.0f * pente_V_pH;
const float V_offset = 0.000f;
const float K = 666.7f;
const int PH_SAMPLES = 30;
const int PH_STABLE_COUNT = 8;
const float PH_STABLE_DELTA = 0.020f;
float phLastVoltages[PH_STABLE_COUNT];
int phStableIdx = 0;

const float TANK_FULL_CM = 4.4f;    // Distance capteur → surface d'eau (réservoir plein)
const float TANK_EMPTY_CM = 16.0f;  // Distance capteur → fond (réservoir vide)

// Set these to true only when the corresponding sensor/module is connected.
// Analog inputs without a sensor can float and produce plausible-looking values.
const bool HAS_PH_SENSOR = true;
const bool HAS_TDS_SENSOR = true;
const bool HAS_WATER_TEMP_SENSOR = true;
const bool HAS_OUTSIDE_DHT_SENSOR = true;
const bool HAS_WATER_LEVEL_SENSOR = true;

// -------------------- Shared sensor data --------------------
struct SensorData {
  float ph = -999.0f;
  float tds = -999.0f;
  float waterTemp = -999.0f;
  float tempInside = -999.0f;
  float humInside = -999.0f;
  float tempOutside = -999.0f;
  float humOutside = -999.0f;
  float waterLevel = -999.0f;
};

struct LedControl {
  bool ledOn = false;
  bool manualMode = false;
  bool manualState = false;
  int onHour = 6;
  int offHour = 20;
  int dap = 0;
};

SensorData sensors;
LedControl ledControl;
SemaphoreHandle_t dataMutex;
SemaphoreHandle_t ledMutex;
bool sensorReadReady = false;

// ============================================================================
// Low-level helpers
// ============================================================================

void writeRelayPin(int pin, bool on) {
  if (RELAY_ACTIVE_LOW) digitalWrite(pin, on ? LOW : HIGH);
  else digitalWrite(pin, on ? HIGH : LOW);
}

void initRelays() {
  pinMode(RELAY_PIN_LED, OUTPUT);

  writeRelayPin(RELAY_PIN_LED, false);
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.printf("[WiFi] Connecting to %s", ssid);

  while (WiFi.status() != WL_CONNECTED) {
    delay(1000);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] Connected. IP=%s\n", WiFi.localIP().toString().c_str());
}

void initNtpTime() {
  configTime(TIMEZONE_OFFSET_SEC, 0, NTP_SERVER);
  struct tm timeinfo;
  for (int i = 0; i < 15; i++) {
    if (getLocalTime(&timeinfo)) {
      Serial.printf("[NTP] OK %02d:%02d:%02d\n", timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
      return;
    }
    delay(1000);
  }
  Serial.println("[NTP] Not synchronized yet.");
}

float readVoltageAvg(int pin, int samples = PH_SAMPLES) {
  long sum = 0;
  for (int i = 0; i < samples; i++) {
    sum += analogRead(pin);
    delay(5);
  }
  return (sum / (float)samples) * (Vref / ADC_MAX);
}

float voltageToPh(float voltage) {
  return 7.0f + (V_dist - voltage) / pente_V_pH;
}

bool isPhStable(float voltage) {
  phLastVoltages[phStableIdx] = voltage;
  phStableIdx = (phStableIdx + 1) % PH_STABLE_COUNT;

  float minV = phLastVoltages[0];
  float maxV = phLastVoltages[0];
  for (int i = 1; i < PH_STABLE_COUNT; i++) {
    if (phLastVoltages[i] < minV) minV = phLastVoltages[i];
    if (phLastVoltages[i] > maxV) maxV = phLastVoltages[i];
  }
  return (maxV - minV) <= PH_STABLE_DELTA;
}

void readDhtSensor(DHTesp &sensor, float &temp, float &hum, const char* label) {
  TempAndHumidity data = sensor.getTempAndHumidity();
  if (sensor.getStatus() != DHTesp::ERROR_NONE ||
      !isfinite(data.temperature) || !isfinite(data.humidity) ||
      data.temperature < -20.0f || data.temperature > 80.0f ||
      data.humidity < 0.0f || data.humidity > 100.0f) {
    Serial.printf("[DHT22] %s error: %s\n", label, sensor.getStatusString());
    temp = -999.0f;
    hum = -999.0f;
    return;
  }
  temp = data.temperature;
  hum = data.humidity;
}

float readWaterLevel() {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  long duration = pulseIn(echoPin, HIGH, 30000);
  if (duration == 0) return -999.0f;

  float distance = duration * 0.034f / 2.0f;
  float level = (TANK_EMPTY_CM - distance) / (TANK_EMPTY_CM - TANK_FULL_CM) * 100.0f;
  if (level < 0) level = 0;
  if (level > 100) level = 100;
  return level;
}

// ============================================================================
// TFT
// ============================================================================

void printTftValue(int x, int y, int w, const char* text, uint16_t color = ST7735_YELLOW) {
  tft.fillRect(x, y, w, 10, ST7735_BLACK);
  tft.setCursor(x, y);
  tft.setTextColor(color);
  tft.setTextSize(1);
  tft.print(text);
}

void initTft() {
  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  tft.initR(TFT_INIT_TYPE);
  tft.setRotation(2);
  tft.fillScreen(ST7735_BLACK);
  tft.setTextColor(ST7735_CYAN);
  tft.setTextSize(1);
  tft.setCursor(2, 2);
  tft.print("SYNAPSE HYDRO");
  tft.drawFastHLine(0, 12, 160, ST7735_DARKGREY);
  tft.setTextColor(ST7735_WHITE);
  tft.setCursor(2,16);  tft.print("pH:");
  tft.setCursor(2,27);  tft.print("TDS:");
  tft.setCursor(2,38);  tft.print("W Temp:");
  tft.setCursor(2,49);  tft.print("W Level:");
  tft.setCursor(2,60);  tft.print("Temp In:");
  tft.setCursor(2,71);  tft.print("Temp Out:");
  tft.setCursor(2,82);  tft.print("DAP:");
  tft.setCursor(2,93);  tft.print("LED:");
}

void updateTftDisplay(const SensorData &s, const LedControl &l, bool phStable) {
  char buf[32];
  const int x = 38;
  const int w = 120;

  if (s.ph <= -900) {
    printTftValue(x, 16, w, "OFF", ST7735_RED);
  } else {
    snprintf(buf, sizeof(buf), "%.2f%s", s.ph, phStable ? "*" : "");
    printTftValue(x, 16, w, buf, phStable ? ST7735_GREEN : ST7735_YELLOW);
  }

  if (s.tds <= -900) {
    printTftValue(x, 27, w, "OFF", ST7735_RED);
  } else {
    snprintf(buf, sizeof(buf), "%d ppm", (int)s.tds);
    printTftValue(x, 27, w, buf);
  }

  snprintf(buf, sizeof(buf), "%.1f C", s.waterTemp);
  printTftValue(x, 38, w, s.waterTemp <= -900 ? "ERR" : buf, s.waterTemp <= -900 ? ST7735_RED : ST7735_YELLOW);

  if (s.waterLevel <= -900) {
    printTftValue(x, 49, w, "OFF", ST7735_RED);
  } else {
    snprintf(buf, sizeof(buf), "%.0f %%", s.waterLevel);
    printTftValue(x, 49, w, buf);
  }

  snprintf(buf, sizeof(buf), "%.0fC %.0f%%", s.tempInside, s.humInside);
  printTftValue(x, 60, w, s.tempInside <= -900 ? "OFF" : buf, s.tempInside <= -900 ? ST7735_RED : ST7735_YELLOW);

  snprintf(buf, sizeof(buf), "%.0fC %.0f%%", s.tempOutside, s.humOutside);
  printTftValue(x, 71, w, s.tempOutside <= -900 ? "OFF" : buf, s.tempOutside <= -900 ? ST7735_RED : ST7735_YELLOW);

  snprintf(buf, sizeof(buf), "J+%d", l.dap);
  printTftValue(x, 82, w, buf, ST7735_CYAN);

  snprintf(buf, sizeof(buf), "%s %s", l.ledOn ? "ON" : "OFF", l.manualMode ? "M" : "A");
  printTftValue(x, 93, w, buf, l.ledOn ? ST7735_GREEN : ST7735_RED);
}

// ============================================================================
// LED automation
// ============================================================================
bool scheduledLedState(int onHour, int offHour) {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) {
    Serial.println("[LED] Heure NTP indisponible - LED forcee ON par securite");
    return true;   // <- repli sur ON plutot que OFF : plus sur pour une plante (pas de nuit forcee)
  }
  int hour = timeinfo.tm_hour;
  bool result = (onHour < offHour) ? (hour >= onHour && hour < offHour)
                                    : (hour >= onHour || hour < offHour);
  Serial.printf("[LED] Heure actuelle: %02d:%02d -> LED %s\n", hour, timeinfo.tm_min, result ? "ON" : "OFF");
  return result;
}

void updateLedRelay() {
  if (xSemaphoreTake(ledMutex, portMAX_DELAY)) {
    if (ledControl.manualMode) {
      ledControl.ledOn = ledControl.manualState;
    } else {
      ledControl.ledOn = scheduledLedState(ledControl.onHour, ledControl.offHour);
    }
    writeRelayPin(RELAY_PIN_LED, ledControl.ledOn);
    xSemaphoreGive(ledMutex);
  }
}

void parseAutomation(JsonObject automation) {
  if (automation.isNull()) return;
  if (xSemaphoreTake(ledMutex, portMAX_DELAY)) {
    const char* ledMode = automation["led_mode"];
    if (ledMode && strcmp(ledMode, "manual") == 0) ledControl.manualMode = true;
    if (ledMode && strcmp(ledMode, "auto") == 0) ledControl.manualMode = false;
    if (automation.containsKey("led_state")) ledControl.manualState = automation["led_state"];
    if (automation.containsKey("led_on_hour")) ledControl.onHour = automation["led_on_hour"];
    if (automation.containsKey("led_off_hour")) ledControl.offHour = automation["led_off_hour"];
    xSemaphoreGive(ledMutex);
  }
}

// ============================================================================
// Tasks
// ============================================================================

void taskReadSensors(void *pvParameters) {
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);

  for (;;) {
    SensorData s;

    ds18b20.requestTemperatures();
    if (HAS_WATER_TEMP_SENSOR) {
      s.waterTemp = ds18b20.getTempCByIndex(0);
      if (s.waterTemp == DEVICE_DISCONNECTED_C ||
          !isfinite(s.waterTemp) || s.waterTemp < -5.0f || s.waterTemp > 60.0f) {
        s.waterTemp = -999.0f;
      }
    }

    bool phStable = false;
    if (HAS_PH_SENSOR) {
      float phVoltage = readVoltageAvg(phPin);
      float measuredPh = voltageToPh(phVoltage);
      if (isfinite(measuredPh) && measuredPh >= 0.0f && measuredPh <= 14.0f) {
        s.ph = measuredPh;
        phStable = isPhStable(phVoltage);
      }
    }

    if (HAS_TDS_SENSOR) {
      int rawTds = analogRead(tdsPin);
      float tdsVoltage = rawTds * (Vref / ADC_MAX);
      float net = tdsVoltage - V_offset;
      if (net < 0) net = 0;
      float measuredTds = K * net;
      if (isfinite(measuredTds) && measuredTds >= 0.0f && measuredTds <= 5000.0f) {
        s.tds = measuredTds;
      }
    }

    readDhtSensor(dhtInside, s.tempInside, s.humInside, "INSIDE");
    if (HAS_OUTSIDE_DHT_SENSOR) {
      readDhtSensor(dhtOutside, s.tempOutside, s.humOutside, "OUTSIDE");
    }
    if (HAS_WATER_LEVEL_SENSOR) {
      s.waterLevel = readWaterLevel();
    }

    if (xSemaphoreTake(dataMutex, portMAX_DELAY)) {
      sensors = s;
      sensorReadReady = true;
      xSemaphoreGive(dataMutex);
    }

    LedControl l;
    if (xSemaphoreTake(ledMutex, portMAX_DELAY)) {
      l = ledControl;
      xSemaphoreGive(ledMutex);
    }
    updateTftDisplay(s, l, phStable);

    Serial.printf("[SENSORS] pH ");
    if (s.ph <= -900) Serial.print("OFF");
    else Serial.printf("%.2f", s.ph);
    Serial.printf(" | TDS ");
    if (s.tds <= -900) Serial.print("OFF");
    else Serial.printf("%.0f", s.tds);
    Serial.printf(" | Water ");
    if (s.waterTemp <= -900) Serial.print("OFF");
    else Serial.printf("%.1fC", s.waterTemp);
    Serial.printf(" | Level ");
    if (s.waterLevel <= -900) Serial.print("OFF");
    else Serial.printf("%.0f%%", s.waterLevel);
    Serial.printf(" | In ");
    if (s.tempInside <= -900 || s.humInside <= -900) Serial.print("OFF");
    else Serial.printf("%.1fC %.0f%%", s.tempInside, s.humInside);
    Serial.printf(" | Out ");
    if (s.tempOutside <= -900 || s.humOutside <= -900) Serial.print("OFF");
    else Serial.printf("%.1fC %.0f%%", s.tempOutside, s.humOutside);
    Serial.println();
    vTaskDelay(pdMS_TO_TICKS(2000));
  }
}

void taskLedAutomation(void *pvParameters) {
  for (;;) {
    updateLedRelay();
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

void taskSendFlask(void *pvParameters) {
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      connectWiFi();
    }

    SensorData s;
    LedControl l;
    bool haveSensorRead = false;
    if (xSemaphoreTake(dataMutex, portMAX_DELAY)) {
      s = sensors;
      haveSensorRead = sensorReadReady;
      xSemaphoreGive(dataMutex);
    }
    if (!haveSensorRead) {
      Serial.println("[Flask] Waiting for the first sensor reading.");
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }

    if (xSemaphoreTake(ledMutex, portMAX_DELAY)) {
      l = ledControl;
      xSemaphoreGive(ledMutex);
    }

    WiFiClient client;
    client.setTimeout(20); 
    HTTPClient http;
    http.setTimeout(20000);
    if (http.begin(client, flaskServerUrl)) {
      http.addHeader("Content-Type", "application/json");

      StaticJsonDocument<768> jsonDoc;
      if (s.tempInside > -900) jsonDoc["temperature"] = s.tempInside;
      if (s.humInside > -900) jsonDoc["humidity"] = s.humInside;
      if (s.tempOutside > -900) jsonDoc["temperature_outside"] = s.tempOutside;
      if (s.humOutside > -900) jsonDoc["humidity_outside"] = s.humOutside;
      if (s.waterTemp > -900) jsonDoc["water_temperature"] = s.waterTemp;
      if (s.waterLevel > -900) jsonDoc["water_level"] = s.waterLevel;
      if (s.ph > -900) jsonDoc["ph"] = s.ph;
      if (s.tds > -900) jsonDoc["tds"] = s.tds;

      const bool hasSensorReading =
        s.tempInside > -900 || s.humInside > -900 ||
        s.tempOutside > -900 || s.humOutside > -900 ||
        s.waterTemp > -900 || s.waterLevel > -900 ||
        s.ph > -900 || s.tds > -900;
      if (!hasSensorReading) {
        Serial.println("[Flask] POST skipped: no connected sensor has a valid reading.");
        http.end();
        vTaskDelay(pdMS_TO_TICKS(15000));
        continue;
      }

      JsonObject status = jsonDoc.createNestedObject("actuator_status");
      status["pump"] = true;
      status["fan"] = true;
      status["led"] = l.ledOn;
      status["led_mode"] = l.manualMode ? "manual" : "auto";

      String payload;
      serializeJson(jsonDoc, payload);
      Serial.println("[Flask] POST " + payload);

      int httpCode = http.POST(payload);
      if (httpCode > 0) {
        String response = http.getString();
        Serial.printf("[Flask] HTTP %d\n", httpCode);
        Serial.println(response);

        if (httpCode == HTTP_CODE_OK || httpCode == 201) {
          StaticJsonDocument<1536> responseDoc;
          DeserializationError err = deserializeJson(responseDoc, response);
          if (!err) {
            if (responseDoc.containsKey("automation")) {
              parseAutomation(responseDoc["automation"].as<JsonObject>());
            }
            if (responseDoc.containsKey("dap")) {
              if (xSemaphoreTake(ledMutex, portMAX_DELAY)) {
                ledControl.dap = responseDoc["dap"];
                xSemaphoreGive(ledMutex);
              }
            }
          }
        }
      } else {
        Serial.printf("[Flask] HTTP error: %s\n", http.errorToString(httpCode).c_str());
      }

      http.end();
    } else {
      Serial.println("[Flask] begin failed");
    }
    vTaskDelay(pdMS_TO_TICKS(15000));
  }
}
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== SYNAPSE HYDRO DAP LED FINAL ===");

  analogReadResolution(12);
  for (int i = 0; i < PH_STABLE_COUNT; i++) phLastVoltages[i] = 0.0f;

  dhtInside.setup(DHTPIN_INSIDE, DHTesp::DHT22);
  dhtOutside.setup(DHTPIN_OUTSIDE, DHTesp::DHT22);
  ds18b20.begin();
  ds18b20.setResolution(10);

  initRelays();
  Serial.println("=== TEST RELAIS LED ===");

  writeRelayPin(RELAY_PIN_LED, true);
  Serial.println("Relais ON");
  delay(3000);
  
  writeRelayPin(RELAY_PIN_LED, false);
  Serial.println("Relais OFF");
  delay(3000);
  
  Serial.println("=== FIN TEST ===");
    initTft();

  dataMutex = xSemaphoreCreateMutex();
  ledMutex = xSemaphoreCreateMutex();
  if (dataMutex == NULL || ledMutex == NULL) {
    Serial.println("[CRITICAL] Mutex allocation failed.");
    while (1);
  }

  connectWiFi();
  initNtpTime();

  xTaskCreatePinnedToCore(taskReadSensors, "ReadSensors", 6144, NULL, 3, NULL, 0);
  xTaskCreatePinnedToCore(taskLedAutomation, "LedAutomation", 3072, NULL, 2, NULL, 0);
  //xTaskCreatePinnedToCore(taskSendFlask, "SendFlask", 6144, NULL, 2, NULL, 1);
  xTaskCreatePinnedToCore(taskSendFlask, "SendFlask", 12288, NULL, 2, NULL, 1);
}

void loop() {
  vTaskDelay(pdMS_TO_TICKS(1000));
}
