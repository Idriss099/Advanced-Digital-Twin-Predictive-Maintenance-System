# 🏭 Advanced Digital Twin & Predictive Maintenance System

## 📌 Overview
This repository contains the core backend system for an advanced **Digital Twin and Predictive Maintenance Dashboard**. It successfully bridges the gap between IT (Information Technology) and OT (Operational Technology) by integrating IoT sensors (ESP32), Industrial Automation (Siemens PLC / Factory I/O), and Artificial Intelligence.

The system acts as a centralized brain that monitors real-time telemetry, predicts equipment faults using Machine Learning, and ensures synchronized safety across both physical hardware and simulated environments.

## ✨ Key Features
* **IT/OT Convergence:** Seamless bidirectional communication between real-world microcontrollers (ESP32 via Serial/WiFi) and industrial PLCs (S7-PLCSIM via Snap7).
* **AI-Powered Predictive Maintenance:** Integrates a `RandomForestClassifier` to analyze vibrations (X, Y, Z), temperature, current, and RPM to calculate Remaining Useful Life (RUL) and detect anomalies.
* **Unified Safety Bridge (E-Stop):** A coordinated safety mechanism that instantly halts both the real physical motor (ESP32 PWM) 



# Advanced Digital Twin & Predictive Maintenance Dashboard 🏭🤖

## 📌 Overview
An advanced, unified Digital Twin and Predictive Maintenance system designed for industrial automation. This project seamlessly bridges the gap between IT (Information Technology) and OT (Operational Technology) by integrating a real physical hardware system (ESP32/Arduino) with a simulated industrial environment (Siemens PLC S7-PLCSIM & Factory I/O). 

The system leverages Machine Learning (Random Forest) for real-time anomaly detection, estimating Remaining Useful Life (RUL), and preventing mechanical failures before they occur.

## ✨ Key Features
* **Unified Safety Bridge:** Coordinated emergency stops (E-Stop) across both real hardware (ESP32) and simulated environments (PLC/Factory I/O).
* **AI-Powered Predictive Maintenance:** Uses `scikit-learn` (Random Forest) to analyze telemetry data (PWM, RPM, Vibration, Temperature, Current) and predict faults.
* **Real-Time Digital Twin:** Live WebSocket broadcasting for instant dashboard updates with high-frequency data plotting (latency < 500ms).
* **Industrial Protocol Integration:** Communicates with Siemens PLCs via the `Snap7` library for seamless memory reading/writing (Inputs, Outputs, Markers).
* **Automated Alerting System:** Automated Gmail SMTP notifications triggered by critical PLC events or AI anomaly detection.
* **Robust Data Logging:** Employs SQLite with WAL mode for high-concurrency logging of sensor telemetry and fault events.

## 🛠️ Tech Stack & Technologies
* **Backend:** Python, Flask, WebSockets, SQLite, Threading/Multiprocessing.
* **Machine Learning:** Scikit-learn, Pandas, NumPy, Joblib.
* **Industrial Comm:** Python-Snap7 (S7 Communication Protocol), Factory I/O.
* **Hardware/IoT:** ESP32/Arduino, Serial Communication (`pyserial`).

## ⚙️ Hardware & Prerequisites
* **Controller:** ESP32 / Arduino (Connected via Serial, e.g., COM5)
* **Sensors:** Vibration (MPU6050), Temperature, Current sensors.
* **Software:** TIA Portal (v16 or higher), S7-PLCSIM, Factory I/O.
* **Environment:** Python 3.9+

## 🚀 Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Idriss099/digital-twin-predictive-maintenance.git
   cd digital-twin-predictive-maintenance
