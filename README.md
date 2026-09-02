# 🏭 Integrated Smart Factory Cyber-Physical System

### Siemens TIA Portal + S7-PLCSIM + Factory I/O + ESP32 + AI + Digital Twin

> A cyber-physical industrial automation prototype integrating a Siemens PLC-based virtual factory with a real ESP32-driven motor, real-time telemetry, AI-based anomaly detection, predictive maintenance, and unified safety control.

**Author:** Bencheikh Mohamed Idris
**M.Sc. Automation & Industrial Computing — Université Blida 1**
**Research Interests:** Industrial Automation · Digital Twin · Predictive Maintenance · AI · Cyber-Physical Systems · Industry 4.0

---

## 🎥 Demonstration

**Full System Demonstration:**
*Add your YouTube/video link here.*

The demonstration shows the complete integration between the virtual factory, Siemens PLC control system, real motor prototype, Python middleware, AI monitoring, and unified safety mechanism.

---

## 🚀 Project Overview

This project implements an integrated **cyber-physical industrial automation system** combining a simulated production environment with a physical motor and sensor platform.

Unlike a conventional Factory I/O automation project, the system establishes a bidirectional bridge between:

* Siemens TIA Portal
* S7-PLCSIM
* Factory I/O
* Python industrial middleware
* ESP32
* Real motor and sensors
* AI-based anomaly detection
* Digital Twin visualization
* Predictive maintenance analytics
* SQLite historical data storage
* Automated safety response

The objective is to demonstrate how **virtual industrial processes and physical assets can be monitored and coordinated within a unified Industry 4.0 architecture.**

---

# 🧠 System Architecture

```text
                         ┌───────────────────────┐
                         │      Factory I/O      │
                         │  Virtual Production   │
                         └───────────┬───────────┘
                                     │
                              S7-PLCSIM
                                     │
                         ┌───────────▼───────────┐
                         │      TIA Portal       │
                         │ Siemens PLC Control    │
                         └───────────┬───────────┘
                                     │
                                  Snap7
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │    Python Middleware  │
                         │ Flask + WebSocket     │
                         └───────────┬───────────┘
                                     │
                 ┌───────────────────┼───────────────────┐
                 │                   │                   │
                 ▼                   ▼                   ▼
          ┌─────────────┐    ┌──────────────┐    ┌──────────────┐
          │   ESP32     │    │   AI Engine  │    │   SQLite DB  │
          │ Real Motor  │    │ Random Forest │    │ Telemetry    │
          │ + Sensors   │    │ Fault Detect. │    │ + Faults     │
          └──────┬──────┘    └──────────────┘    └──────────────┘
                 │
          WiFi / Serial
                 │
                 ▼
        ┌────────────────────┐
        │ Digital Twin       │
        │ Real-time Dashboard│
        │ Health + RUL + AI   │
        └────────────────────┘
```

---

# ⚙️ Main Components

## 1. Siemens Industrial Automation

The virtual production process is controlled using:

* Siemens TIA Portal
* S7-PLCSIM
* PLC control logic
* Digital inputs/outputs
* VFD command simulation
* Conveyor control
* Vision sensor signals
* Emergency-stop logic
* Operator START / STOP / RESET controls

---

## 2. Factory I/O

Factory I/O provides the virtual industrial environment used to reproduce a production process.

The system monitors:

* Conveyor state
* VFD command
* Vision sensor
* Production counters
* Sorter health indicators
* Emergency-stop state
* Operator commands

---

## 3. Physical ESP32 Motor Platform

A real motor prototype is connected to an ESP32 and monitored through multiple sensors.

### Measured variables

| Variable        | Source           |
| --------------- | ---------------- |
| RPM             | Encoder          |
| Temperature     | DS18B20          |
| Vibration X/Y/Z | MPU6050          |
| Current         | Current sensor   |
| PWM             | Motor controller |

Telemetry can be transferred through **WiFi or Serial communication**.

---

# 🤖 AI-Based Anomaly Detection

The Python backend uses a **Random Forest classifier** for real-time anomaly detection.

The model uses a combination of operational and sensor features including:

* PWM
* RPM
* Vibration X/Y/Z
* Temperature
* Current
* Vibration statistics
* Current statistics

The AI layer is designed to identify abnormal operating conditions and support predictive maintenance decisions.

---

# ❤️ Health Index

A multi-sensor Health Index is calculated from:

* Vibration
* Temperature
* Motor current

The resulting health score ranges from:

```text
100% ───────────── Healthy
       ↓
       ↓ degradation
       ↓
30%  ───────────── Maintenance threshold
       ↓
0%   ───────────── Critical
```

This provides a unified condition indicator for the monitored asset.

---

# ⏳ Remaining Useful Life Estimation

The prototype includes a **health-index-based RUL estimation method**.

The system tracks the evolution of the Health Index over time and estimates the time required to reach a predefined maintenance threshold.

> Note: The current RUL implementation is a prototype research baseline. More advanced degradation models and data-driven RUL methods are planned for future work.

---

# 🛡️ Unified Safety Architecture

One of the main contributions of this project is the integration of safety actions across the virtual and physical domains.

When a critical condition is detected, the Python middleware can coordinate:

```text
PLC E-STOP / STOP
        │
        ▼
Python Safety Bridge
        │
        ├──────────────► Real ESP32 Motor → STOP
        │
        ├──────────────► PLC Safety Marker
        │
        ├──────────────► Factory I/O Process → STOP
        │
        └──────────────► Fault Database → LOG
```

The safety state remains latched until an operator performs the appropriate reset procedure.

This architecture demonstrates coordination between **physical equipment, PLC control, simulation, and software-based monitoring**.

---

# 📊 Real-Time Monitoring

The web dashboard provides:

* Real-time RPM
* Temperature
* Motor current
* 3-axis vibration
* PWM/VFD command
* AI status
* Health Index
* RUL estimation
* PLC status
* Conveyor state
* Production counters
* Fault events
* Safety status

Communication is implemented using **WebSocket** for real-time updates.

---

# 🗄️ Data Acquisition and Historical Logging

SQLite is used to store:

### Motor telemetry

* Timestamp
* RPM
* PWM
* Vibration
* Temperature
* Current
* Health
* RUL
* AI status

### PLC telemetry

* PLC inputs
* Conveyor state
* VFD command
* Vision sensor
* Production counters
* Equipment health

### Fault history

* Fault type
* Timestamp
* Sensor conditions
* Health
* Motor state
* Corrective action

This historical layer enables future development of more advanced predictive maintenance models.

---

# 🔬 Research Relevance

This prototype explores the integration of several Industry 4.0 concepts:

* **Digital Twins**
* **Cyber-Physical Systems**
* **Industrial IoT**
* **Predictive Maintenance**
* **Machine Learning**
* **Condition Monitoring**
* **PLC-based Automation**
* **Real-time Data Acquisition**
* **Human-Machine Interaction**
* **Industrial Safety**

The project is particularly relevant to research in:

> **AI-enabled industrial automation and predictive maintenance using cyber-physical systems and digital twins.**

---

# 🧪 Experimental Platform

The system combines two complementary environments:

### Virtual environment

**TIA Portal + S7-PLCSIM + Factory I/O**

Used for:

* PLC control
* production simulation
* industrial sequence validation
* virtual sensors and actuators

### Physical environment

**ESP32 + Motor + Sensors**

Used for:

* real sensor acquisition
* motor condition monitoring
* physical anomaly experiments
* real-time safety response

This allows the project to investigate the interaction between **virtual industrial processes and physical assets**.

---

# 🛠️ Technologies

| Category           | Technology                   |
| ------------------ | ---------------------------- |
| PLC                | Siemens S7                   |
| PLC Engineering    | TIA Portal                   |
| PLC Simulation     | S7-PLCSIM                    |
| Factory Simulation | Factory I/O                  |
| PLC Communication  | Snap7                        |
| Backend            | Python / Flask               |
| Real-time          | WebSocket                    |
| AI                 | Scikit-learn / Random Forest |
| Data               | Pandas / NumPy               |
| Database           | SQLite                       |
| Hardware           | ESP32                        |
| Vibration          | MPU6050                      |
| Temperature        | DS18B20                      |
| Motor Control      | PWM / Motor Driver           |
| Frontend           | HTML / JavaScript            |
| Visualization      | Three.js / Chart.js          |
| Alerts             | Gmail SMTP                   |

---

# 📁 Suggested Repository Structure

```text
Integrated-Smart-Factory-Cyber-Physical-System/
│
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
│
├── python/
│   ├── main.py
│   ├── requirements.txt
│   └── ...
│
├── esp32/
│   └── firmware/
│
├── tia_portal/
│   ├── PLC_program/
│   └── documentation/
│
├── factory_io/
│   └── scenes/
│
├── docs/
│   ├── architecture.png
│   ├── wiring_diagram.png
│   └── system_overview.png
│
├── screenshots/
│
└── results/
```

---

# 🚀 Future Research Directions

The current prototype provides a foundation for further research in:

1. Deep-learning-based fault diagnosis
2. Advanced RUL prediction
3. Physics-informed Digital Twins
4. Online model adaptation
5. Edge AI deployment
6. Multi-asset predictive maintenance
7. Digital Twin synchronization
8. Anomaly detection under changing operating conditions
9. Predictive control
10. Cloud/edge industrial architectures

---

# 👨‍🔬 Author

**Bencheikh Mohamed Idris**

M.Sc. Automation & Industrial Computing
Université Blida 1

Research interests:

**Industrial Automation · AI · Digital Twins · Predictive Maintenance · Cyber-Physical Systems · Industry 4.0**

📧 [bencheikhmohamed800@gmail.com](mailto:bencheikhmohamed800@gmail.com)

🔗 GitHub: https://github.com/Idriss099

---

## 🎯 PhD Research Direction

This project represents the practical foundation of my research interest in:

> **AI-Driven Digital Twins for Predictive Maintenance and Intelligent Industrial Automation.**

I am interested in developing this prototype further toward advanced **AI-based fault diagnosis, predictive control, RUL prediction, and intelligent cyber-physical industrial systems**.

