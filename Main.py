import asyncio
import csv
import html
import io
import json
import logging
import math
import os
import queue
import sqlite3
import smtplib
import threading
import time
from collections import deque
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
import serial
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, Response, jsonify, request
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import websockets

from dotenv import load_dotenv

# ============================================================
#  Advanced Digital Twin - Predictive Maintenance Dashboard
#  WebSocket + SQLite + 3D Twin + AI + Gmail + WiFi + Serial
# ============================================================

# ---------------------------
# تحميل المتغيرات البيئية
# ---------------------------
load_dotenv()

GMAIL_SENDER   = os.getenv("GMAIL_SENDER")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD")
GMAIL_RECEIVER = os.getenv("GMAIL_RECEIVER")

if not all([GMAIL_SENDER, GMAIL_PASSWORD, GMAIL_RECEIVER]):
    print("[WARN] إعدادات الإيميل مفقودة! تأكد من إنشاء ملف .env ووضع البيانات فيه.")

# ---------------------------
# Config
# ---------------------------
SERIAL_PORT              = 'COM5'
BAUD_RATE                = 115200
MAX_DATA_POINTS          = 30
LOG_FILE_NAME            = 'telemetry_log_v3.csv'
DB_FILE                  = 'digital_twin_unified.db'

PLC_ENABLED               = True
PLC_IP                    = '127.0.0.1'
PLC_RACK                  = 0
PLC_SLOT                  = 2
PLC_SCAN_INTERVAL         = 0.10
PLC_OUTPUT_START          = 30
PLC_OUTPUT_SIZE           = 16
PLC_VISION_INPUT_START    = 30
PLC_VISION_INPUT_SIZE     = 2
PLC_VFD_RAW_MAX           = 27648
PLC_VFD_FREQ_MAX_HZ       = 50.0
PLC_MAX_CYCLES            = 150
PLC_DB_SAMPLE_INTERVAL    = 1.0
MODEL_FILE               = 'twin_anomaly_model.pkl'
TRAINING_DATASET         = 'fault_training_data.csv'
HEALTH_THRESHOLD         = 30.0
EMAIL_COOLDOWN           = 120
FAULT_LOG_COOLDOWN       = 60
PLOT_UPDATE_INTERVAL_MS  = 500
ENABLE_LOCAL_MATPLOTLIB  = False

HTTP_HOST        = '0.0.0.0'
HTTP_PORT        = 5000
WS_HOST          = '0.0.0.0'
WS_PORT          = 8765
MAX_CLIENT_POINTS = 120

# ---------------------------
# Logging
# ---------------------------
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

# ---------------------------
# Flask app
# ---------------------------
app = Flask(__name__)
app.logger.setLevel(logging.ERROR)

# ---------------------------
# Global state
# ---------------------------
# FIX: تعريف SERIAL_ENABLED مبكراً لتجنب NameError في snapshot_state
SERIAL_ENABLED      = False
ser                 = None

telemetry_queue      = queue.Queue(maxsize=400)
ws_broadcast_queue   = queue.Queue(maxsize=400)
state_lock           = threading.Lock()
db_lock              = threading.Lock()
serial_lock          = threading.Lock()
data_lock            = threading.Lock()
process_lock         = threading.Lock()

latest_ack           = '0'
ai_prediction_status = 'Analyzing...'
status_color         = 'gray'
current_health       = 100.0
rul_estimate         = 'Collecting...'
target_pwm_wifi      = 0
fault_count          = 0
latest_runtime       = 0.0
latest_rpm           = 0.0
latest_temp          = 0.0
latest_current       = 0.0
latest_source        = 'WiFi'
clf_model            = None

# Unified safety bridge
plc_manager_ref = None
integrated_safety_active = False
last_integrated_safety_reason = ''
last_integrated_safety_time = 0.0
last_valid_temp = 25.0
safety_condition_cleared = False
plc_state_lock = threading.Lock()
plc_state = {
    'connected': False, 'input_byte': 0, 'q0_0': False,
    'e_stop': False, 'auto_mode': False, 'start_pressed': False,
    'stop_pressed': False, 'reset_pressed': False, 'at_exit_sensor': False,
    'conveyor_off': False, 'conveyor_on': False, 'vfd_raw': 0,
    'vfd_percent': 0.0, 'vfd_frequency_hz': 0.0, 'vision_sensor': 0,
    'box_count_1': 0, 'box_count_2': 0, 'box_count_3': 0,
    'sorter1_health': 100.0, 'sorter2_health': 100.0, 'sorter3_health': 100.0,
    'active_events': []
}

# Plot buffers (Python side)
time_data    = deque(maxlen=MAX_DATA_POINTS)
pwm_data     = deque(maxlen=MAX_DATA_POINTS)
rpm_data     = deque(maxlen=MAX_DATA_POINTS)
vib_x        = deque(maxlen=MAX_DATA_POINTS)
vib_y        = deque(maxlen=MAX_DATA_POINTS)
vib_z        = deque(maxlen=MAX_DATA_POINTS)
temp_data    = deque(maxlen=MAX_DATA_POINTS)
current_data = deque(maxlen=MAX_DATA_POINTS)
health_data  = deque(maxlen=MAX_DATA_POINTS)

vib_x_buffer    = deque(maxlen=10)
current_buffer  = deque(maxlen=5)
rpm_window      = deque(maxlen=5)
health_history  = deque(maxlen=120)
timestamp_history = deque(maxlen=120)

last_alert_time = {}

# FIX: وقت البدء لحساب runtime
_process_start_time = time.time()


# ---------------------------
# Database
# ---------------------------
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                runtime_sec REAL,
                pwm REAL,
                rpm REAL,
                vib_x REAL,
                vib_y REAL,
                vib_z REAL,
                temp REAL,
                current REAL,
                health REAL,
                rul TEXT,
                ai_status TEXT,
                status_color TEXT,
                target_pwm REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS faults (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                health REAL,
                temp REAL,
                current REAL,
                pwm REAL,
                rpm REAL,
                action TEXT
            )
        ''')
        conn.execute("CREATE TABLE IF NOT EXISTS plc_telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, input_byte INTEGER, q0_0 INTEGER, e_stop INTEGER, auto_mode INTEGER, start_pressed INTEGER, stop_pressed INTEGER, reset_pressed INTEGER, at_exit_sensor INTEGER, conveyor_off INTEGER, conveyor_on INTEGER, vfd_raw INTEGER, vfd_percent REAL, vfd_frequency_hz REAL, vision_sensor INTEGER, box_count_sorter1 INTEGER, box_count_sorter2 INTEGER, box_count_sorter3 INTEGER, sorter1_health REAL, sorter2_health REAL, sorter3_health REAL)")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_plc_telemetry_ts ON plc_telemetry(ts DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_faults_ts ON faults(ts DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts DESC)')


def db_insert_telemetry(sample):
    with db_lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                '''
                INSERT INTO telemetry
                (ts, source, runtime_sec, pwm, rpm, vib_x, vib_y, vib_z,
                 temp, current, health, rul, ai_status, status_color, target_pwm)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    sample['ts'], sample['source'], sample['runtime_sec'],
                    sample['pwm'], sample['rpm'],
                    sample['vib_x'], sample['vib_y'], sample['vib_z'],
                    sample['temp'], sample['current'],
                    sample['health'], sample['rul'],
                    sample['ai'], sample['status_color'], sample['target_pwm']
                )
            )
            conn.commit()


def db_insert_fault(kind, message, health=None, temp=None,
                    current=None, pwm=None, rpm=None, action=None):
    global fault_count
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with db_lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                '''
                INSERT INTO faults
                (ts, kind, message, health, temp, current, pwm, rpm, action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (ts, kind, message, health, temp, current, pwm, rpm, action)
            )
            conn.commit()
    with state_lock:
        fault_count += 1


def fetch_recent_faults(limit=50):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute(
            '''SELECT ts, kind, message, health, temp, current, pwm, rpm, action
               FROM faults ORDER BY id DESC LIMIT ?''',
            (limit,)
        )
        rows = cur.fetchall()
    return [
        {
            'ts': r[0], 'kind': r[1], 'message': r[2],
            'health': r[3], 'temp': r[4], 'current': r[5],
            'pwm': r[6], 'rpm': r[7], 'action': r[8]
        }
        for r in rows
    ]


def fault_count_total():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute('SELECT COUNT(*) FROM faults')
        return int(cur.fetchone()[0])


# ---------------------------
# AI model
# ---------------------------
FEATURE_COLUMNS = [
    'PWM', 'RPM', 'Vib_X', 'Vib_Y', 'Vib_Z',
    'Temperature', 'Current', 'Vib_X_Std', 'Current_Mean'
]


def train_model_from_dataset(csv_path):
    df = pd.read_csv(csv_path)
    label_col = None
    for candidate in ['label', 'Class', 'Fault', 'fault', 'anomaly', 'Anomaly']:
        if candidate in df.columns:
            label_col = candidate
            break
    if label_col is None:
        raise ValueError('Dataset must contain a label column: label/Class/Fault/Anomaly')

    missing = [c for c in FEATURE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f'Missing feature columns: {missing}')

    X = df[FEATURE_COLUMNS].copy()
    y = df[label_col].copy()
    if y.dtype == object:
        y = (y.astype(str).str.lower()
               .isin(['1', 'true', 'fault', 'faulty', 'anomaly', 'bad', 'defect', 'yes'])
               .astype(int))

    if y.nunique() < 2:
        raise ValueError('Need at least two classes in the training dataset.')

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    model = RandomForestClassifier(
        n_estimators=250, random_state=42, class_weight='balanced_subsample'
    )
    model.fit(X_train, y_train)
    preds  = model.predict(X_test)
    acc    = accuracy_score(y_test, preds)
    report = classification_report(y_test, preds, digits=4)
    joblib.dump(model, MODEL_FILE)
    with open('model_training_report.txt', 'w', encoding='utf-8') as f:
        f.write(f'Accuracy: {acc:.4f}\n\n')
        f.write(report)
    return model, acc, report


def load_or_train_model():
    global clf_model
    if os.path.exists(MODEL_FILE):
        clf_model = joblib.load(MODEL_FILE)
        print(f"[OK] AI Brain '{MODEL_FILE}' loaded successfully!")
        return
    if os.path.exists(TRAINING_DATASET):
        try:
            clf_model, acc, _ = train_model_from_dataset(TRAINING_DATASET)
            print(f'[OK] AI model trained from {TRAINING_DATASET} | Accuracy={acc:.4f}')
            return
        except Exception as e:
            print(f'[WARN] Training from real dataset failed: {e}')
    print('[WARN] No trained model found. Creating a small fallback model.')
    X = pd.DataFrame([
        [0,   0,    0.0, 0.0, 9.81, 25,  0.0, 0.0, 0.0],
        [200, 1200, 0.2, 0.1, 9.6,  30,  1.0, 0.1, 1.0],
        [220, 1300, 1.2, 0.9, 10.5, 48,  6.5, 0.8, 4.0],
        [235, 900,  2.0, 1.5, 11.0, 52,  7.0, 1.2, 5.0],
    ], columns=FEATURE_COLUMNS)
    y = np.array([0, 0, 1, 1])
    clf_model = RandomForestClassifier(n_estimators=100, random_state=42)
    clf_model.fit(X, y)
    joblib.dump(clf_model, MODEL_FILE)


# ---------------------------
# PLC / Factory I/O integration
# ---------------------------
I_AT_EXIT = 0
I_START = 1
I_STOP = 2
I_RESET = 3
I_ESTOP = 4
I_AUTO = 5
I_CONVEYOR_OFF = 6
I_CONVEYOR_ON = 7

class PLCManager:
    """Persistent, thread-safe Snap7 client for S7-PLCSIM."""
    def __init__(self):
        import snap7
        import snap7.type as sevtypes
        from snap7.util import get_int
        self.sevtypes = sevtypes
        self.get_int = get_int
        self.plc = snap7.client.Client()
        self.lock = threading.RLock()

    def connect(self):
        with self.lock:
            try:
                if self.plc.get_connected():
                    return True
                print(f'[PLC] Connecting to {PLC_IP}:{PLC_RACK}:{PLC_SLOT} ...')
                self.plc.connect(PLC_IP, PLC_RACK, PLC_SLOT)
                ok = self.plc.get_connected()
                if ok:
                    print('[PLC] Connected to S7-PLCSIM ✓')
                return ok
            except Exception as e:
                print(f'[PLC] Connection failed: {e}')
                return False

    def disconnect(self):
        with self.lock:
            try:
                if self.plc.get_connected():
                    self.plc.disconnect()
            except Exception:
                pass

    def _write_marker_bit(self, byte_address, bit_mask, value):
        with self.lock:
            if not self.plc.get_connected() and not self.connect():
                return False
            try:
                current = self.plc.read_area(self.sevtypes.Areas.MK, 0, byte_address, 1)[0]
                new_value = (current | bit_mask) if value else (current & (~bit_mask & 0xFF))
                self.plc.write_area(self.sevtypes.Areas.MK, 0, byte_address, bytes([new_value]))
                return bool(self.plc.read_area(self.sevtypes.Areas.MK, 0, byte_address, 1)[0] & bit_mask) == bool(value)
            except Exception as e:
                print(f'[PLC] Marker write error: {e}')
                return False

    def write_safety_marker(self, active):
        # M10.0 = Python_SafetyStop (reserve this bit in TIA Portal)
        return self._write_marker_bit(10, 0x01, bool(active))

    def write_q0_0(self, value):
        """Best-effort direct write of process output Q0.0."""
        with self.lock:
            if not self.plc.get_connected() and not self.connect():
                return False
            try:
                current = self.plc.read_area(self.sevtypes.Areas.PA, 0, 0, 1)[0]
                new_value = (current | 0x01) if value else (current & 0xFE)
                self.plc.write_area(self.sevtypes.Areas.PA, 0, 0, bytes([new_value]))
                return bool(self.plc.read_area(self.sevtypes.Areas.PA, 0, 0, 1)[0] & 0x01) == bool(value)
            except Exception as e:
                print(f'[PLC] Q0.0 write error: {e}')
                return False

    def write_vfd_raw(self, raw_value):
        """Best-effort direct write of QW42."""
        with self.lock:
            if not self.plc.get_connected() and not self.connect():
                return False
            try:
                raw = max(0, min(PLC_VFD_RAW_MAX, int(raw_value)))
                payload = int(raw).to_bytes(2, byteorder='big', signed=True)
                self.plc.write_area(self.sevtypes.Areas.PA, 0, PLC_OUTPUT_START + 12, payload)
                return True
            except Exception as e:
                print(f'[PLC] QW42 write error: {e}')
                return False

    def read_data(self):
        with self.lock:
            if not self.plc.get_connected() and not self.connect():
                return None
            try:
                outputs = self.plc.read_area(self.sevtypes.Areas.PA, 0, PLC_OUTPUT_START, PLC_OUTPUT_SIZE)
                q0_byte = self.plc.read_area(self.sevtypes.Areas.PA, 0, 0, 1)
                q0_0 = bool(q0_byte[0] & 0x01)
                vision = self.plc.read_area(self.sevtypes.Areas.PE, 0, PLC_VISION_INPUT_START, PLC_VISION_INPUT_SIZE)
                input_byte = self.plc.read_area(self.sevtypes.Areas.PE, 0, 0, 1)[0]

                raw_at_exit = bool(input_byte & (1 << I_AT_EXIT))
                raw_start = bool(input_byte & (1 << I_START))
                raw_stop = bool(input_byte & (1 << I_STOP))
                raw_reset = bool(input_byte & (1 << I_RESET))
                raw_estop = bool(input_byte & (1 << I_ESTOP))
                raw_auto = bool(input_byte & (1 << I_AUTO))
                raw_conveyor_off = bool(input_byte & (1 << I_CONVEYOR_OFF))
                raw_conveyor_on = bool(input_byte & (1 << I_CONVEYOR_ON))

                estop_active = not raw_estop
                stop_pressed = not raw_stop

                box1 = self.get_int(outputs, 0)
                box2 = self.get_int(outputs, 4)
                box3 = self.get_int(outputs, 8)
                vfd_raw = self.get_int(outputs, 12)
                vision_value = self.get_int(vision, 0)

                raw = max(0, min(PLC_VFD_RAW_MAX, vfd_raw))
                vfd_percent = (raw / PLC_VFD_RAW_MAX) * 100.0
                vfd_hz = (vfd_percent / 100.0) * PLC_VFD_FREQ_MAX_HZ

                def health(boxes):
                    return round(max(0.0, min(100.0, 100.0 - (boxes / PLC_MAX_CYCLES) * 100.0)), 2)

                return {
                    'input_byte': input_byte,
                    'q0_0': q0_0,
                    'raw_at_exit': raw_at_exit,
                    'raw_start': raw_start,
                    'raw_stop': raw_stop,
                    'raw_reset': raw_reset,
                    'raw_estop': raw_estop,
                    'raw_auto': raw_auto,
                    'raw_conveyor_off': raw_conveyor_off,
                    'raw_conveyor_on': raw_conveyor_on,
                    'at_exit_sensor': raw_at_exit,
                    'start_pressed': raw_start,
                    'stop_pressed': stop_pressed,
                    'reset_pressed': raw_reset,
                    'estop_active': estop_active,
                    'auto_mode': raw_auto,
                    'conveyor_off': raw_conveyor_off,
                    'conveyor_on': q0_0,
                    'vfd_raw': vfd_raw,
                    'vfd_percent': round(vfd_percent, 2),
                    'vfd_frequency_hz': round(vfd_hz, 2),
                    'vision_sensor': vision_value,
                    'box_count_1': box1,
                    'box_count_2': box2,
                    'box_count_3': box3,
                    'sorter1_health': health(box1),
                    'sorter2_health': health(box2),
                    'sorter3_health': health(box3),
                }
            except Exception as e:
                print(f'[PLC] Read error: {e}')
                try:
                    self.plc.disconnect()
                except Exception:
                    pass
                return None


def get_plc_snapshot():
    with plc_state_lock:
        return dict(plc_state)


def plc_insert_telemetry(d):
    with db_lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("""
                INSERT INTO plc_telemetry (
                    ts,input_byte,q0_0,e_stop,auto_mode,start_pressed,stop_pressed,reset_pressed,
                    at_exit_sensor,conveyor_off,conveyor_on,vfd_raw,vfd_percent,vfd_frequency_hz,
                    vision_sensor,box_count_sorter1,box_count_sorter2,box_count_sorter3,
                    sorter1_health,sorter2_health,sorter3_health
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                d['ts'], d['input_byte'], int(d['q0_0']), int(d['estop_active']), int(d['auto_mode']),
                int(d['start_pressed']), int(d['stop_pressed']), int(d['reset_pressed']),
                int(d['at_exit_sensor']), int(d['conveyor_off']), int(d['conveyor_on']),
                d['vfd_raw'], d['vfd_percent'], d['vfd_frequency_hz'], d['vision_sensor'],
                d['box_count_1'], d['box_count_2'], d['box_count_3'],
                d['sorter1_health'], d['sorter2_health'], d['sorter3_health']
            ))
            conn.commit()


def plc_log_event(kind, message, severity='INFO', action=None):
    snap = get_plc_snapshot()
    db_insert_fault(kind, message, health=snap.get('sorter1_health'),
                    pwm=snap.get('vfd_percent'), action=action)
    if GMAIL_SENDER and GMAIL_PASSWORD and GMAIL_RECEIVER:
        trigger_alert(f'plc_{kind}', f'[PLC {severity}] {kind}', message)


class DebouncedSignal:
    """Debounce a boolean signal over a fixed number of scans."""

    def __init__(self, required_scans=2):
        self.required_scans = max(1, int(required_scans))
        self.last_raw = False
        self.stable_state = False
        self.counter = 0

    def update(self, raw_state):
        raw_state = bool(raw_state)
        if raw_state == self.last_raw:
            self.counter += 1
        else:
            self.counter = 1
            self.last_raw = raw_state

        if self.counter >= self.required_scans:
            changed = self.stable_state != raw_state
            self.stable_state = raw_state
            return self.stable_state, changed

        return self.stable_state, False


class EdgeDetector:
    """Detect rising and falling edges of a boolean signal."""

    def __init__(self):
        self.previous = False

    def update(self, current):
        current = bool(current)
        rising = current and not self.previous
        falling = not current and self.previous
        self.previous = current
        return rising, falling


def coordinated_safety_stop(reason, source='system'):
    """
    Stop the real ESP32 motor and the PLC/Factory I/O side together.
    Safety remains latched until the operator uses the PLC reset input.
    """
    global integrated_safety_active
    global last_integrated_safety_reason, last_integrated_safety_time

    integrated_safety_active = True
    last_integrated_safety_reason = str(reason)
    last_integrated_safety_time = time.time()

    print(f'[SAFETY] UNIFIED STOP: {reason} | source={source}')

    # 1) Real motor: existing ESP32 command path.
    try:
        apply_pwm_command(0, source=f'safety:{source}')
    except Exception as e:
        print(f'[SAFETY] ESP32 stop command failed: {e}')

    # 2) PLC / Factory I/O: marker + direct best-effort output writes.
    manager = plc_manager_ref
    if manager is not None:
        marker_ok = manager.write_safety_marker(True)
        q_ok = manager.write_q0_0(False)
        vfd_ok = manager.write_vfd_raw(0)
        print(f'[SAFETY] PLC: M10.0={marker_ok} | Q0.0=OFF:{q_ok} | QW42=0:{vfd_ok}')


def coordinated_safety_reset():
    """Clear the Python safety latch only after an operator reset."""
    global integrated_safety_active, last_integrated_safety_reason
    integrated_safety_active = False
    last_integrated_safety_reason = ''
    manager = plc_manager_ref
    if manager is not None:
        ok = manager.write_safety_marker(False)
        print(f'[SAFETY] Reset accepted. M10.0 cleared={ok}. Restart remains operator-controlled.')


def run_plc_monitor():
    if not PLC_ENABLED:
        print('[PLC] Integration disabled.')
        return
    manager = PLCManager()
    global plc_manager_ref
    plc_manager_ref = manager
    previous = {'e_stop': False, 'no_vision': False}
    last_db = 0.0
    start_db = DebouncedSignal(2)
    stop_db = DebouncedSignal(2)
    reset_db = DebouncedSignal(2)
    start_edge = EdgeDetector()
    stop_edge = EdgeDetector()
    reset_edge = EdgeDetector()

    while True:
        try:
            data = manager.read_data()
            now = time.time()
            if data is None:
                with plc_state_lock:
                    plc_state['connected'] = False
                time.sleep(1.0)
                continue

            start_state, _ = start_db.update(data['start_pressed'])
            stop_state, _ = stop_db.update(data['stop_pressed'])
            reset_state, _ = reset_db.update(data['reset_pressed'])
            start_rise, _ = start_edge.update(start_state)
            stop_rise, _ = stop_edge.update(stop_state)
            reset_rise, _ = reset_edge.update(reset_state)
            data['start_pressed'] = start_state
            data['stop_pressed'] = stop_state
            data['reset_pressed'] = reset_state

            with plc_state_lock:
                plc_state.update({
                    'connected': True,
                    'input_byte': data['input_byte'],
                    'q0_0': data['q0_0'],
                    'e_stop': data['estop_active'],
                    'auto_mode': data['auto_mode'],
                    'start_pressed': data['start_pressed'],
                    'stop_pressed': data['stop_pressed'],
                    'reset_pressed': data['reset_pressed'],
                    'at_exit_sensor': data['at_exit_sensor'],
                    'conveyor_off': data['conveyor_off'],
                    'conveyor_on': data['conveyor_on'],
                    'vfd_raw': data['vfd_raw'],
                    'vfd_percent': data['vfd_percent'],
                    'vfd_frequency_hz': data['vfd_frequency_hz'],
                    'vision_sensor': data['vision_sensor'],
                    'box_count_1': data['box_count_1'],
                    'box_count_2': data['box_count_2'],
                    'box_count_3': data['box_count_3'],
                    'sorter1_health': data['sorter1_health'],
                    'sorter2_health': data['sorter2_health'],
                    'sorter3_health': data['sorter3_health'],
                })

            if start_rise: print('[PLC] START pressed')
            if stop_rise: print('[PLC] STOP pressed')
            if reset_rise: print('[PLC] RESET pressed')

            if data['estop_active']:
                # Keep the unified safety path asserted while E-Stop is active.
                coordinated_safety_stop('PLC E-STOP active', source='PLC')
                if not previous['e_stop']:
                    plc_log_event('PLC_E_STOP', 'Emergency stop activated.', 'CRITICAL',
                                  'Stop conveyor / inspect safety circuit')
            elif previous['e_stop']:
                print('[PLC] E-STOP cleared; safety remains latched until RESET')
            previous['e_stop'] = data['estop_active']

            # Operator STOP / Conveyor OFF also stops the real motor and latches safety.
            if (stop_rise or data['conveyor_off']) and not data['estop_active']:
                coordinated_safety_stop('PLC STOP / Conveyor OFF command', source='PLC')

            # Reset is the only software path that clears the integrated safety latch.
            if reset_rise and not data['estop_active']:
                coordinated_safety_reset()

            no_vision = data['conveyor_on'] and data['vfd_percent'] > 10.0 and data['vision_sensor'] == 0
            if no_vision and not previous['no_vision']:
                plc_log_event('PLC_NO_VISION', 'Conveyor is running without vision detection.', 'WARNING',
                              'Inspect vision sensor / material flow')
            elif not no_vision and previous['no_vision']:
                print('[PLC] NO_VISION cleared')
            previous['no_vision'] = no_vision

            if now - last_db >= PLC_DB_SAMPLE_INTERVAL:
                last_db = now
                plc_insert_telemetry({'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), **data})
                enqueue_ws({'type': 'plc', 'plc': get_plc_snapshot(), 'source': 'plc'})

            time.sleep(PLC_SCAN_INTERVAL)
        except Exception as e:
            print(f'[PLC] Monitor error: {e}')
            with plc_state_lock:
                plc_state['connected'] = False
            time.sleep(1.0)

# ---------------------------
# Gmail alerts
# ---------------------------
def send_alert_email(subject, body):
    if not all([GMAIL_SENDER, GMAIL_PASSWORD, GMAIL_RECEIVER]):
        print('[EMAIL] SKIPPED: configure GMAIL_SENDER, GMAIL_PASSWORD and GMAIL_RECEIVER in .env')
        return
    try:
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From']    = GMAIL_SENDER
        msg['To']      = GMAIL_RECEIVER
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.send_message(msg)
        print(f'[EMAIL] Sent: {subject}')
    except smtplib.SMTPAuthenticationError:
        print('[EMAIL ERR] Authentication failed. Check App Password.')
    except Exception as e:
        print(f'[EMAIL ERR] {e}')


def trigger_alert(alert_type, subject, body):
    now = time.time()
    if now - last_alert_time.get(alert_type, 0) > EMAIL_COOLDOWN:
        last_alert_time[alert_type] = now
        threading.Thread(
            target=send_alert_email, args=(subject, body), daemon=True
        ).start()


# ---------------------------
# Utilities
# ---------------------------
def get_local_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            import socket
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return '0.0.0.0'


def should_fire(kind, cooldown=FAULT_LOG_COOLDOWN):
    now = time.time()
    if now - last_alert_time.get(f'fault_{kind}', 0) > cooldown:
        last_alert_time[f'fault_{kind}'] = now
        return True
    return False


def compute_health_index(vx, vy, vz_dynamic, temp, current):
    vib_mag    = math.sqrt(vx * vx + vy * vy + vz_dynamic * vz_dynamic)
    vib_score  = max(0.0, 100.0 - min(vib_mag * 3.0, 100.0))
    temp_excess = max(0.0, temp - 26.0)
    temp_score = max(0.0, 100.0 - temp_excess * (100.0 / 19.0))
    curr_score = max(0.0, 100.0 - abs(current - 1.0) * 30.0)
    health     = 0.50 * vib_score + 0.35 * temp_score + 0.15 * curr_score
    return round(max(0.0, min(100.0, health)), 1)


def estimate_rul(health_now, runtime_sec):
    health_history.append(health_now)
    timestamp_history.append(runtime_sec)
    n = len(health_history)
    if n < 15:
        return f'Collecting data... ({n}/15)'
    t_list  = list(timestamp_history)
    h_list  = list(health_history)
    t0      = t_list[0]
    t_norm  = [t - t0 for t in t_list]
    s_t     = sum(t_norm)
    s_h     = sum(h_list)
    s_tt    = sum(t * t for t in t_norm)
    s_th    = sum(t * h for t, h in zip(t_norm, h_list))
    denom   = n * s_tt - s_t ** 2
    if abs(denom) < 1e-9:
        return '[OK] Stable — no maintenance needed'
    slope   = (n * s_th - s_t * s_h) / denom
    if slope >= -0.001:
        return '[OK] Stable — no maintenance soon'
    secs_to_critical = (HEALTH_THRESHOLD - health_now) / slope
    if secs_to_critical <= 0:
        return '[!] MAINTENANCE REQUIRED NOW'
    hours   = int(secs_to_critical // 3600)
    minutes = int((secs_to_critical % 3600) // 60)
    if hours >= 24:
        days = hours // 24
        return f'RUL: ~{days}d {hours % 24}h'
    elif hours > 0:
        return f'RUL: ~{hours}h {minutes}m'
    else:
        return f'RUL: ~{minutes} min'


# ---------------------------
# WebSocket broadcasting
# ---------------------------
ws_clients = set()


def enqueue_ws(payload):
    msg = json.dumps(payload)
    try:
        ws_broadcast_queue.put_nowait(msg)
    except queue.Full:
        try:
            ws_broadcast_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            ws_broadcast_queue.put_nowait(msg)
        except queue.Full:
            pass


def snapshot_state():
    with state_lock:
        return {
            'type': 'snapshot',
            'state': {
                'ai':          ai_prediction_status,
                'health':      current_health,
                'rul':         rul_estimate,
                'ack':         latest_ack,
                'status_color': status_color,
                'mode':        'Serial+WiFi' if SERIAL_ENABLED else 'WiFi',
                'pwm_target':  target_pwm_wifi,
                'runtime_sec': latest_runtime,
                'rpm':         latest_rpm,
                'temp':        latest_temp,
                'current':     latest_current,
                'fault_count': fault_count,
                'source':      latest_source,
                'integrated_safety_active': integrated_safety_active,
                'last_integrated_safety_reason': last_integrated_safety_reason,
            },
            'plc': get_plc_snapshot(),
            'faults': fetch_recent_faults(10)
        }


async def ws_handler(websocket):
    ws_clients.add(websocket)
    try:
        await websocket.send(json.dumps(snapshot_state()))
        async for message in websocket:
            try:
                data = json.loads(message)
            except Exception:
                continue

            cmd = data.get('cmd')
            if cmd == 'set_pwm':
                value = int(max(0, min(235, int(data.get('value', 0)))))
                apply_pwm_command(value, source='websocket')
                await websocket.send(json.dumps({'type': 'ack', 'pwm': value}))
            elif cmd == 'train':
                dataset_path = data.get('path') or TRAINING_DATASET
                try:
                    model, acc, _ = train_model_from_dataset(dataset_path)
                    global clf_model
                    clf_model = model
                    await websocket.send(
                        json.dumps({'type': 'train_done', 'ok': True, 'accuracy': acc})
                    )
                except Exception as e:
                    await websocket.send(
                        json.dumps({'type': 'train_done', 'ok': False, 'error': str(e)})
                    )
    finally:
        ws_clients.discard(websocket)


async def ws_broadcaster():
    while True:
        msg   = await asyncio.to_thread(ws_broadcast_queue.get)
        stale = []
        for client in list(ws_clients):
            try:
                await client.send(msg)
            except Exception:
                stale.append(client)
        for client in stale:
            ws_clients.discard(client)


async def ws_main():
    async with websockets.serve(
        ws_handler, WS_HOST, WS_PORT, ping_interval=20, ping_timeout=20
    ):
        print(f'[WS] WebSocket server running on ws://{get_local_ip()}:{WS_PORT}')
        await ws_broadcaster()


def start_ws_server():
    asyncio.run(ws_main())


# ---------------------------
# HTTP routes
# ---------------------------
@app.route('/status', methods=['GET'])
def status_route():
    return 'Digital Twin WiFi Server Running', 200


@app.route('/api/state', methods=['GET'])
def api_state():
    return jsonify(snapshot_state()), 200


@app.route('/api/faults', methods=['GET'])
def api_faults():
    limit = int(request.args.get('limit', 50))
    return jsonify(fetch_recent_faults(limit)), 200


@app.route('/history', methods=['GET'])
def history_page():
    faults = fetch_recent_faults(100)
    rows = []
    for f in faults:
        rows.append(
            '<tr>'
            f'<td>{html.escape(str(f["ts"]))}</td>'
            f'<td>{html.escape(str(f["kind"]))}</td>'
            f'<td>{html.escape(str(f["message"]))}</td>'
            f'<td>{"" if f["health"]  is None else f["health"]}</td>'
            f'<td>{"" if f["temp"]    is None else f["temp"]}</td>'
            f'<td>{"" if f["current"] is None else f["current"]}</td>'
            f'<td>{"" if f["pwm"]     is None else f["pwm"]}</td>'
            f'<td>{"" if f["rpm"]     is None else f["rpm"]}</td>'
            f'<td>{html.escape(str(f["action"])) if f["action"] else ""}</td>'
            '</tr>'
        )
    table_rows = '\n'.join(rows) if rows else '<tr><td colspan="9">No fault history yet</td></tr>'
    html_page = f'''
    <!DOCTYPE html>
    <html><head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Fault History</title>
    <style>
      body {{ background:#111; color:#eee; font-family:Arial, sans-serif; padding:16px; }}
      table {{ width:100%; border-collapse:collapse; font-size:14px; }}
      th, td {{ border:1px solid #333; padding:8px; vertical-align:top; }}
      th {{ background:#1d1d1d; position:sticky; top:0; }}
      tr:nth-child(even) {{ background:#171717; }}
      a {{ color:#5CFF90; }}
    </style>
    </head><body>
      <h2>Fault History</h2>
      <p>Total faults: {fault_count_total()}</p>
      <p><a href="/">Back to dashboard</a></p>
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Type</th><th>Message</th><th>Health</th>
            <th>Temp</th><th>Current</th><th>PWM</th><th>RPM</th><th>Action</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </body></html>
    '''
    return html_page, 200


@app.route('/train', methods=['GET', 'POST'])
def train_route():
    if request.method == 'GET':
        return '''
        <!DOCTYPE html>
        <html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Train AI</title>
        <style>
          body{background:#111;color:#eee;font-family:Arial;padding:16px;}
          input,button{padding:10px;margin:8px 0;}
        </style>
        </head><body>
        <h2>Train AI on Real Fault Data</h2>
        <form method="post" enctype="multipart/form-data">
          <p>Upload CSV with columns: PWM, RPM, Vib_X, Vib_Y, Vib_Z, Temperature,
             Current, Vib_X_Std, Current_Mean, label</p>
          <input type="file" name="dataset" accept=".csv" required>
          <br><button type="submit">Train</button>
        </form>
        <p>Or place <code>fault_training_data.csv</code> beside the script
           and submit with no file.</p>
        <p><a href="/">Back</a></p>
        </body></html>
        ''', 200

    uploaded     = request.files.get('dataset')
    dataset_path = TRAINING_DATASET
    if uploaded and uploaded.filename:
        dataset_path = f'_uploaded_{int(time.time())}.csv'
        uploaded.save(dataset_path)
    try:
        model, acc, report = train_model_from_dataset(dataset_path)
        global clf_model
        clf_model = model
        return jsonify({'ok': True, 'accuracy': acc, 'report': report}), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/telemetry', methods=['POST'])
def receive_telemetry():
    data = request.get_json(force=True, silent=True)
    if data:
        try:
            telemetry_queue.put_nowait({'source': 'wifi', 'data': data})
        except queue.Full:
            try:
                telemetry_queue.get_nowait()
            except queue.Empty:
                pass
            telemetry_queue.put({'source': 'wifi', 'data': data})
    return f'PWM:{target_pwm_wifi}', 200


def set_wifi_pwm(value):
    global target_pwm_wifi
    target_pwm_wifi = int(max(0, min(235, value)))


@app.route('/control/pwm', methods=['POST'])
def control_pwm_route():
    payload = request.get_json(force=True, silent=True) or {}
    value   = int(payload.get('value', 0))
    apply_pwm_command(value, source='http')
    return jsonify({'ok': True, 'pwm': value}), 200


@app.route('/', methods=['GET'])
def dashboard_page():
    return r'''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Digital Twin Dashboard</title>
<style>
  :root {
    --bg:#0e0f10; --panel:#17181b; --panel2:#111214; --line:#2a2d33; --txt:#f0f3f5;
    --muted:#9aa3ad; --green:#5CFF90; --orange:#ffb347; --red:#ff6b6b; --magenta:#d96bff;
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font-family:system-ui, Arial, sans-serif; }
  .wrap { max-width:1400px; margin:0 auto; padding:14px; }
  .hero, .card {
    background:linear-gradient(180deg,rgba(255,255,255,0.02),rgba(255,255,255,0.01));
    border:1px solid var(--line); border-radius:18px;
  }
  .hero { padding:16px; margin-bottom:12px; }
  .hero h1 { margin:0; font-size:1.25rem; color:#1D9E75; }
  .hero p { margin:6px 0 0; color:var(--muted); }
  .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-bottom:12px; }
  .kpi { background:var(--panel2); border:1px solid var(--line); border-radius:16px; padding:12px; min-height:86px; }
  .label { color:var(--muted); font-size:0.8rem; margin-bottom:6px; }
  .value { font-size:1.05rem; font-weight:800; }
  .big { font-size:1.2rem; }
  .green { color:var(--green); }
  .orange { color:var(--orange); }
  .red { color:var(--red); }
  .magenta { color:var(--magenta); }
  .panel { display:grid; grid-template-columns:2fr 1fr; gap:12px; align-items:start; }
  .card { padding:12px; margin-bottom:12px; }
  .charts { display:grid; grid-template-columns:1fr; gap:10px; }
  /* FIX: استخدام height ثابت بدل CSS height الذي يتضارب مع canvas */
  canvas { display:block; width:100%; height:260px;
           background:#0b0c0d; border:1px solid #2a2d33; border-radius:14px; }
  #motor3d { height:320px; }
  .split { display:grid; grid-template-columns:1.2fr 0.8fr; gap:12px; }
  .faults { max-height:320px; overflow:auto; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { border-bottom:1px solid #222; padding:8px 6px; text-align:left; vertical-align:top; }
  th { position:sticky; top:0; background:#15161a; }
  tr:nth-child(even) { background:rgba(255,255,255,0.02); }
  .toolbar { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
  .btn { background:#1d9e75; color:white; border:none; padding:10px 12px;
         border-radius:12px; cursor:pointer; font-weight:700; }
  .btn.secondary { background:#2a2d33; }
  .range { width:220px; }
  .small { color:var(--muted); font-size:0.9rem; }
  @media (max-width:1100px) {
    .panel, .split, .grid { grid-template-columns:1fr; }
    canvas { height:230px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>&#9881; Digital Twin WiFi Dashboard</h1>
    <p>WebSocket live stream &bull; SQLite fault history &bull; 3D motor twin &bull; AI &bull; Health &bull; RUL &bull; Email alerts &bull; Remote PWM</p>
    <div class="toolbar">
      <input id="pwmRange" class="range" type="range" min="0" max="235" value="0"
             oninput="debouncedSendPwm()" />
      <span id="pwmVal" style="color:#5CFF90;font-weight:bold;font-size:1.1rem;">0</span>
      <button class="btn" onclick="sendPwm()">Send PWM</button>
      <button class="btn secondary" onclick="location.href='/history'">Fault History</button>
      <button class="btn secondary" onclick="location.href='/train'">Train AI</button>
    </div>
  </div>

  <div class="grid">
    <div class="kpi"><div class="label">AI Status</div><div id="ai" class="value big">Loading...</div></div>
    <div class="kpi"><div class="label">Health</div><div id="health" class="value big">--%</div></div>
    <div class="kpi"><div class="label">RUL</div><div id="rul" class="value big">--</div></div>
    <div class="kpi"><div class="label">PWM / ACK</div><div id="ack" class="value">--</div></div>
    <div class="kpi"><div class="label">RPM</div><div id="rpm" class="value">--</div></div>
    <div class="kpi"><div class="label">Temperature / Current</div><div id="tc" class="value">--</div></div>
  </div>

  <div class="grid">
    <div class="kpi"><div class="label">PLC / PLCSIM</div><div id="plcConn" class="value big">--</div></div>
    <div class="kpi"><div class="label">E-Stop / Conveyor</div><div id="plcSafety" class="value big">--</div></div>
    <div class="kpi"><div class="label">PLC VFD</div><div id="plcVfd" class="value big">--</div></div>
    <div class="kpi"><div class="label">Sorter 1 / 2 / 3</div><div id="plcSorters" class="value">--</div></div>
    <div class="kpi"><div class="label">Vision / Exit</div><div id="plcSensors" class="value">--</div></div>
    <div class="kpi"><div class="label">PLC Input Byte</div><div id="plcByte" class="value">--</div></div>
    <div class="kpi"><div class="label">Integrated Safety</div><div id="integratedSafety" class="value big">--</div></div>
  </div>

  <div class="split">
    <div class="card"><canvas id="rpmChart"></canvas></div>
    <div class="card"><canvas id="motor3d"></canvas></div>
  </div>

  <div class="charts">
    <div class="card"><canvas id="vibChart"></canvas></div>
    <div class="card"><canvas id="tcChart"></canvas></div>
    <div class="card"><canvas id="healthChart"></canvas></div>
  </div>

  <div class="split">
    <div class="card faults">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <h3 style="margin:0;">Recent Faults</h3>
        <span id="faultCount" class="small">0</span>
      </div>
      <table>
        <thead><tr><th>Time</th><th>Type</th><th>Message</th><th>Action</th></tr></thead>
        <tbody id="faultTable"><tr><td colspan="4">Loading...</td></tr></tbody>
      </table>
    </div>
    <div class="card">
      <h3 style="margin-top:0;">Live Notes</h3>
      <div class="small">
        Use the slider to send PWM through WebSocket. The ESP32 receives it back
        via the HTTP reply from <code>/telemetry</code>.
      </div>
      <div id="wsStatus" class="small" style="margin-top:10px;">WebSocket: connecting...</div>
      <div id="dataStats" class="small" style="margin-top:6px;">Points: 0</div>
    </div>
  </div>
</div>

<script>
// ============================================================
//  Dashboard JS — FIX: canvas sizing + chart engine rewrite
// ============================================================
const MAX_POINTS = 120;
let ws          = null;
let latestState = {};
let frameReq    = null;
let totalPoints = 0;

const data = {
  rpm:[], pwm:[], vibx:[], viby:[], vibz:[],
  temp:[], current:[], health:[], ts:[]
};

// ---- helpers -----------------------------------------------
function fmt(v, digits=1) {
  if (v === null || v === undefined || !isFinite(Number(v))) return '--';
  return Number(v).toFixed(digits);
}

function colorClassFromText(text) {
  const t = String(text || '').toLowerCase();
  if (t.includes('critical') || t.includes('shutdown') ||
      t.includes('alert')    || t.includes('anomaly'))   return 'red';
  if (t.includes('drop')    || t.includes('warning'))    return 'orange';
  if (t.includes('healthy') || t.includes('normal') ||
      t.includes('[ok]'))                                 return 'green';
  if (t.includes('magenta'))                              return 'magenta';
  return '';
}

function setText(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  if (cls !== undefined) el.className = cls;
}

function healthColor(h) {
  if (h >= 70) return '#5CFF90';
  if (h >= 40) return '#ffb347';
  return '#ff6b6b';
}

// ---- state --------------------------------------------------
function applyIntegratedSafety(s) {
  const el = document.getElementById('integratedSafety');
  if (!el) return;
  const active = !!(s && s.integrated_safety_active);
  el.textContent = active ? ('STOP LATCHED: ' + (s.safety_reason || 'Safety condition')) : 'NORMAL';
  el.className = 'value big ' + (active ? 'red' : 'green');
}

function applyPLC(p) {
  if (!p) return;
  const conn=document.getElementById('plcConn'), safety=document.getElementById('plcSafety');
  const vfd=document.getElementById('plcVfd'), sorters=document.getElementById('plcSorters');
  const sensors=document.getElementById('plcSensors'), byt=document.getElementById('plcByte');
  if(conn){conn.textContent=p.connected?'CONNECTED ✓':'OFFLINE'; conn.className='value big '+(p.connected?'green':'red');}
  if(safety){safety.textContent=(p.e_stop?'E-STOP ACTIVE':'E-STOP OK')+' / '+(p.conveyor_on?'CONVEYOR ON':'CONVEYOR OFF'); safety.className='value big '+(p.e_stop?'red':'green');}
  if(vfd)vfd.textContent=fmt(p.vfd_frequency_hz,2)+' Hz ('+fmt(p.vfd_percent,1)+'%)';
  if(sorters)sorters.textContent=fmt(p.sorter1_health,1)+'% / '+fmt(p.sorter2_health,1)+'% / '+fmt(p.sorter3_health,1)+'%';
  if(sensors)sensors.textContent='Vision '+(Number(p.vision_sensor)||0)+' / Exit '+(p.at_exit_sensor?'ON':'OFF');
  if(byt)byt.textContent=(Number(p.input_byte)||0)+' ('+(Number(p.input_byte)||0).toString(2).padStart(8,'0')+')';
}

function applyState(s) {
  if (!s) return;
  latestState = s;
  setText('ai',     s.ai || '---', 'value big ' + colorClassFromText(s.ai));
  const h = Number(s.health ?? 0);
  setText('health', fmt(h,1) + '%', 'value big ' + (h>=70?'green':(h>=40?'orange':'red')));
  setText('rul',    s.rul || '--', 'value big');
  setText('ack',    'PWM ' + (s.ack ?? '--') + ' / Target ' + (s.pwm_target ?? '--'), 'value');
  setText('rpm',    fmt(s.rpm ?? 0, 1) + ' RPM', 'value');
  setText('tc',     fmt(s.temp ?? 0,2) + ' C  /  ' + fmt(s.current ?? 0,3) + ' A', 'value');
  applyIntegratedSafety(s);
  document.getElementById('faultCount').textContent = 'Faults: ' + (s.fault_count ?? 0);
  document.getElementById('pwmRange').value = s.pwm_target ?? 0;
  document.getElementById('pwmVal').textContent = s.pwm_target ?? 0;
}

function pushPoint(key, value) {
  data[key].push(Number(value) || 0);
  if (data[key].length > MAX_POINTS) data[key].shift();
}

// FIX: الآن pushTelemetry يُشغَّل بكل رسالة بيانات وليس فقط snapshot
function pushTelemetry(t) {
  pushPoint('ts',      t.runtime_sec ?? (Date.now() / 1000));
  pushPoint('rpm',     t.rpm     ?? 0);
  pushPoint('pwm',     t.pwm     ?? 0);
  pushPoint('vibx',    t.vib_x   ?? 0);
  pushPoint('viby',    t.vib_y   ?? 0);
  pushPoint('vibz',    t.vib_z   ?? 0);
  pushPoint('temp',    t.temp    ?? 0);
  pushPoint('current', t.current ?? 0);
  pushPoint('health',  t.health  ?? 0);
  totalPoints++;
  document.getElementById('dataStats').textContent =
    'Points: ' + totalPoints + ' | Buffer: ' + data.rpm.length;
}

// ---- canvas resize ------------------------------------------
// FIX: هذه الدالة هي سبب الرسومات الفارغة — يجب استدعاؤها قبل الرسم
function resizeCanvas(canvas) {
  const dpr  = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const W    = Math.floor(rect.width  * dpr);
  const H    = Math.floor(rect.height * dpr);
  if (canvas.width !== W || canvas.height !== H) {
    canvas.width  = W;
    canvas.height = H;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

function logicalSize(canvas) {
  // الحجم المنطقي بعد DPR correction
  const dpr = window.devicePixelRatio || 1;
  return { w: canvas.width / dpr, h: canvas.height / dpr };
}

// ---- chart engine -------------------------------------------
function clampRange(arr) {
  if (!arr || !arr.length) return [0, 1];
  let mn = Infinity, mx = -Infinity;
  for (const v of arr) { if (v < mn) mn = v; if (v > mx) mx = v; }
  if (!isFinite(mn) || !isFinite(mx)) return [0, 1];
  if (mn === mx) { mn -= 1; mx += 1; }
  const pad = (mx - mn) * 0.12;
  return [mn - pad, mx + pad];
}

const PAD = { l: 52, r: 16, t: 36, b: 40 };

function drawBackground(ctx, w, h, title) {
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#0b0c0d';
  ctx.fillRect(0, 0, w, h);

  // plot area border
  const pw = w - PAD.l - PAD.r;
  const ph = h - PAD.t - PAD.b;
  ctx.strokeStyle = '#30343b';
  ctx.lineWidth   = 1;
  ctx.strokeRect(PAD.l, PAD.t, pw, ph);

  // title
  ctx.fillStyle = '#e8edf2';
  ctx.font      = 'bold 13px Arial';
  ctx.fillText(title, PAD.l, PAD.t - 10);
}

function drawGrid(ctx, w, h, yMin, yMax) {
  const pw = w - PAD.l - PAD.r;
  const ph = h - PAD.t - PAD.b;
  ctx.strokeStyle = '#1f2328';
  ctx.lineWidth   = 1;
  ctx.fillStyle   = '#7f8790';
  ctx.font        = '10px Arial';

  for (let i = 0; i <= 4; i++) {
    const yPx = PAD.t + ph * i / 4;
    ctx.beginPath(); ctx.moveTo(PAD.l, yPx); ctx.lineTo(PAD.l + pw, yPx); ctx.stroke();
    const val = yMax - (yMax - yMin) * (i / 4);
    ctx.fillText(val.toFixed(1), 2, yPx + 4);
  }
  for (let i = 0; i <= 4; i++) {
    const xPx = PAD.l + pw * i / 4;
    ctx.beginPath(); ctx.moveTo(xPx, PAD.t); ctx.lineTo(xPx, PAD.t + ph); ctx.stroke();
  }
}

function drawLine(ctx, w, h, series, yMin, yMax, color, dashed) {
  if (!series || series.length < 2) return;
  const pw = w - PAD.l - PAD.r;
  const ph = h - PAD.t - PAD.b;
  const n  = series.length;

  ctx.strokeStyle = color;
  ctx.lineWidth   = 2;
  ctx.setLineDash(dashed ? [6, 4] : []);
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const xPx = PAD.l + pw * i / Math.max(1, n - 1);
    const yPx = PAD.t + ph - ((series[i] - yMin) / (yMax - yMin)) * ph;
    if (i === 0) ctx.moveTo(xPx, yPx); else ctx.lineTo(xPx, yPx);
  }
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawLegend(ctx, entries) {
  let lx = PAD.l + 8;
  const ly = PAD.t + 16;
  ctx.font = '11px Arial';
  for (const e of entries) {
    ctx.fillStyle = e.color;
    ctx.fillRect(lx, ly - 9, 14, 10);
    ctx.fillStyle = '#d0d8df';
    ctx.fillText(e.label, lx + 17, ly);
    lx += e.label.length * 6 + 36;
  }
}

// FIX: دالة موحدة للرسم تعتمد على canvas.getBoundingClientRect()
function renderChart(canvasId, title, seriesGroups) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = resizeCanvas(canvas);
  const { w, h } = logicalSize(canvas);
  if (w < 10 || h < 10) return;

  drawBackground(ctx, w, h, title);

  // تجميع كل البيانات لحساب المجال
  const allValues = [];
  for (const g of seriesGroups) {
    if (g.data && g.data.length) allValues.push(...g.data);
  }
  if (!allValues.length) {
    ctx.fillStyle = '#555';
    ctx.font = '13px Arial';
    ctx.fillText('Waiting for data...', PAD.l + 8, PAD.t + 30);
    return;
  }

  const [yMin, yMax] = clampRange(allValues);
  drawGrid(ctx, w, h, yMin, yMax);

  for (const g of seriesGroups) {
    if (g.data && g.data.length >= 2) {
      // كل مجموعة يمكن أن تملك مجالها الخاص أو تشترك
      const [gMin, gMax] = g.ownRange ? clampRange(g.data) : [yMin, yMax];
      drawLine(ctx, w, h, g.data, gMin, gMax, g.color, g.dashed || false);
    }
  }

  drawLegend(ctx, seriesGroups.map(g => ({ color: g.color, label: g.label })));
}

// ---- 3D Motor Twin ------------------------------------------
function drawMotor3D() {
  const canvas = document.getElementById('motor3d');
  if (!canvas) return;
  const ctx    = resizeCanvas(canvas);
  const { w, h } = logicalSize(canvas);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#0b0c0d';
  ctx.fillRect(0, 0, w, h);

  const health  = Number(latestState.health  ?? 100);
  const rpm     = Number(latestState.rpm     ?? 0);
  const temp    = Number(latestState.temp    ?? 25);
  const current = Number(latestState.current ?? 0);
  const color   = healthColor(health);
  const angle   = (Date.now() / 1000) * (rpm / 200) % (Math.PI * 2);

  // floor grid
  ctx.strokeStyle = '#1d2127';
  ctx.lineWidth = 1;
  for (let i = 0; i < 12; i++) {
    const y = h * 0.55 + i * 10;
    ctx.beginPath(); ctx.moveTo(20, y); ctx.lineTo(w - 20, y); ctx.stroke();
  }
  for (let i = 0; i < 16; i++) {
    const x = 20 + i * ((w - 40) / 15);
    ctx.beginPath(); ctx.moveTo(x, h * 0.55);
    ctx.lineTo(w / 2 + (x - w / 2) * 0.15, h - 18); ctx.stroke();
  }

  // motor body
  const cx = w * 0.50, cy = h * 0.48;
  const bW = w * 0.36, bH = h * 0.22;
  const grad = ctx.createLinearGradient(cx - bW/2, 0, cx + bW/2, 0);
  grad.addColorStop(0,    '#222831');
  grad.addColorStop(0.35, color);
  grad.addColorStop(0.7,  '#3a4048');
  grad.addColorStop(1,    '#15191d');
  ctx.fillStyle   = grad;
  ctx.strokeStyle = '#4b5563';
  ctx.lineWidth   = 2;
  ctx.beginPath();
  ctx.ellipse(cx, cy, bW/2, bH/2, 0, 0, Math.PI * 2);
  ctx.fill(); ctx.stroke();

  // highlight
  ctx.fillStyle = 'rgba(255,255,255,0.10)';
  ctx.beginPath();
  ctx.ellipse(cx - bW*0.18, cy - 3, bW*0.18, bH*0.34, 0, 0, Math.PI * 2);
  ctx.fill();

  // end caps
  ctx.fillStyle = '#101318';
  ctx.beginPath(); ctx.ellipse(cx - bW/2, cy, bW*0.16, bH*0.52, 0, 0, Math.PI*2); ctx.fill();
  ctx.beginPath(); ctx.ellipse(cx + bW/2, cy, bW*0.16, bH*0.52, 0, 0, Math.PI*2); ctx.fill();

  // shaft
  ctx.strokeStyle = '#cfd8dc'; ctx.lineWidth = 6;
  ctx.beginPath(); ctx.moveTo(cx + bW/2, cy); ctx.lineTo(cx + bW/2 + 40, cy); ctx.stroke();

  // rotating fan
  ctx.save();
  ctx.translate(cx + bW/2 + 12, cy);
  ctx.rotate(angle);
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2;
  for (let i = 0; i < 6; i++) {
    ctx.rotate(Math.PI / 3);
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(28, 0); ctx.stroke();
  }
  ctx.restore();

  // sensor orbs
  const vib = Math.min(1.0,
    Math.abs(current) / 8 + Math.abs(temp - 25) / 60);
  ctx.fillStyle = `rgba(255,255,255,${0.18 + vib * 0.25})`;
  ctx.beginPath(); ctx.arc(cx - bW*0.3, cy - bH*0.1, 10 + vib*3, 0, Math.PI*2); ctx.fill();
  ctx.beginPath(); ctx.arc(cx + bW*0.18, cy + bH*0.1, 5  + vib*2, 0, Math.PI*2); ctx.fill();

  // labels
  ctx.fillStyle = '#e9eef2';
  ctx.font = 'bold 15px Arial';
  ctx.fillText('3D Motor Twin', 14, 22);
  ctx.font = '13px Arial';
  ctx.fillStyle = color;   ctx.fillText('Health: '  + fmt(health,  1) + '%',   14,  44);
  ctx.fillStyle = '#e9eef2';
  ctx.fillText('RPM: '    + fmt(rpm,     1),        14,  64);
  ctx.fillText('Temp: '   + fmt(temp,    2) + ' C', 14,  84);
  ctx.fillText('Current: '+ fmt(current, 3) + ' A', 14, 104);
  ctx.fillText('Source: ' + (latestState.source || '---'), 14, 124);
}

// ---- fault table --------------------------------------------
function updateFaultTable(faults) {
  if (!Array.isArray(faults)) return;
  const tbody = document.getElementById('faultTable');
  if (!faults.length) {
    tbody.innerHTML = '<tr><td colspan="4">No fault history yet</td></tr>';
    return;
  }
  tbody.innerHTML = faults.slice(0, 15).map(f => `
    <tr>
      <td>${f.ts     || ''}</td>
      <td>${f.kind   || ''}</td>
      <td>${f.message|| ''}</td>
      <td>${f.action || ''}</td>
    </tr>`).join('');
}

// ---- main render loop ---------------------------------------
function redraw() {
  renderChart('rpmChart', 'Motor Velocity Profile & PWM Actuation', [
    { data: data.rpm, label: 'RPM', color: '#5CFF90' },
    { data: data.pwm, label: 'PWM', color: '#ff6b6b', dashed: true, ownRange: true }
  ]);
  renderChart('vibChart', 'Real-time 3-Axis Dynamic Vibrations', [
    { data: data.vibx, label: 'Vib X', color: '#5CFF90' },
    { data: data.viby, label: 'Vib Y', color: '#ffb347' },
    { data: data.vibz, label: 'Vib Z', color: '#8bd17c', dashed: true }
  ]);
  renderChart('tcChart', 'Thermal Status & Electrical Load Telemetry', [
    { data: data.temp,    label: 'Temp (C)', color: '#ffb347' },
    { data: data.current, label: 'Current (A)', color: '#d96bff', dashed: true, ownRange: true }
  ]);
  renderChart('healthChart', 'Motor Health Index', [
    { data: data.health, label: 'Health %',
      color: healthColor(Number(latestState.health ?? 100)) }
  ]);
  drawMotor3D();
}

// ---- WebSocket ----------------------------------------------
function connectWs() {
  const proto = (location.protocol === 'https:') ? 'wss://' : 'ws://';
  const url   = proto + location.hostname + ':8765';
  ws = new WebSocket(url);

  ws.onopen = () => {
    document.getElementById('wsStatus').textContent = 'WebSocket: connected ✓';
  };
  ws.onclose = () => {
    document.getElementById('wsStatus').textContent = 'WebSocket: reconnecting...';
    setTimeout(connectWs, 1500);
  };
  ws.onerror = () => {
    document.getElementById('wsStatus').textContent = 'WebSocket: error';
  };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'snapshot') {
        applyState(msg.state || {});
        applyPLC(msg.plc || {});
        updateFaultTable(msg.faults || []);
        // FIX: رسم snapshot كنقطة بيانات أولى
        if (msg.state) {
          pushTelemetry({
            runtime_sec: msg.state.runtime_sec,
            rpm:     msg.state.rpm,
            pwm:     msg.state.pwm_target,
            vib_x:   0, vib_y: 0, vib_z: 9.81,
            temp:    msg.state.temp,
            current: msg.state.current,
            health:  msg.state.health
          });
        }
      } else if (msg.type === 'telemetry') {
        // FIX: applyState يأخذ msg.state وليس msg مباشرة
        applyState(msg.state || {});
        pushTelemetry(msg);
        if (msg.faults) updateFaultTable(msg.faults);
      } else if (msg.type === 'fault') {
        if (msg.state)  applyState(msg.state);
        if (msg.faults) updateFaultTable(msg.faults);
      } else if (msg.type === 'plc') {
        applyPLC(msg.plc || {});
      } else if (msg.type === 'ack') {
        document.getElementById('wsStatus').textContent =
          'WebSocket: PWM ack ' + msg.pwm + ' ✓';
      } else if (msg.type === 'train_done') {
        alert(msg.ok
          ? 'Training completed. Accuracy: ' + Number(msg.accuracy).toFixed(4)
          : 'Training failed: ' + msg.error
        );
      }
    } catch (e) {
      console.warn('WS parse error', e);
    }
  };
}

// ---- PWM control --------------------------------------------
let pwmTimer = null;
function debouncedSendPwm() {
  const v = document.getElementById('pwmRange').value;
  document.getElementById('pwmVal').textContent = v;
  clearTimeout(pwmTimer);
  pwmTimer = setTimeout(sendPwm, 200);
}

function sendPwm() {
  const value = Number(document.getElementById('pwmRange').value);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ cmd: 'set_pwm', value }));
  } else {
    fetch('/control/pwm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value })
    });
  }
}

// ---- fault load on startup ----------------------------------
async function loadFaults() {
  try {
    const r = await fetch('/api/faults?limit=15', { cache: 'no-store' });
    updateFaultTable(await r.json());
  } catch (e) {}
}

// ---- resize all canvases ------------------------------------
function resizeAll() {
  ['rpmChart','vibChart','tcChart','healthChart','motor3d'].forEach(id => {
    const c = document.getElementById(id);
    if (c) resizeCanvas(c);
  });
}

// ---- boot ---------------------------------------------------
window.addEventListener('resize', () => { resizeAll(); redraw(); });

// FIX: نضمن أن الـ canvas لها حجم صحيح قبل أي رسم
window.addEventListener('load', () => {
  resizeAll();
  loadFaults();
  connectWs();
  // loop رسم بـ requestAnimationFrame لأداء أفضل
  function loop() { redraw(); frameReq = requestAnimationFrame(loop); }
  loop();
});
</script>
</body>
</html>
'''


# ---------------------------
# Data processing pipeline
# ---------------------------
def apply_pwm_command(value, source='http'):
    global target_pwm_wifi
    value = int(max(0, min(235, value)))
    target_pwm_wifi = value
    if SERIAL_ENABLED and ser and ser.is_open:
        try:
            with serial_lock:
                ser.write(f'SET_PWM:{value}\n'.encode('utf-8'))
                ser.flush()
        except Exception as e:
            print(f'[ERR] PWM send error: {e}')
    return value


def process_sample(sample, source='wifi'):
    """
    FIX: المشاكل التي تم إصلاحها:
    1. indentation كانت تكسر process_lock في منتصف الدالة
    2. process_sample.start_time كان يُعاد ضبطه في نهاية كل استدعاء
    3. إرسال WS خارج state_lock لتجنب deadlock
    """
    global latest_ack, ai_prediction_status, status_color
    global current_health, rul_estimate
    global last_valid_temp
    global latest_runtime, latest_rpm, latest_temp, latest_current, latest_source

    with process_lock:
        pwm      = float(sample.get('Speed_PWM', 0.0))
        rpm_val  = float(sample.get('RPM',       0.0))
        vx       = float(sample.get('Vib_X',     0.0))
        vy       = float(sample.get('Vib_Y',     0.0))
        vz       = float(sample.get('Vib_Z',     9.81))
        raw_temp_val = float(sample.get('Temp', 25.0))
        temp_valid = (-55.0 <= raw_temp_val <= 125.0) and raw_temp_val != -127.0
        if temp_valid:
            temp_val = raw_temp_val
            last_valid_temp = temp_val
        else:
            temp_val = last_valid_temp
            if should_fire('temp_sensor_invalid', cooldown=30):
                db_insert_fault('temp_sensor_invalid',
                                f'Invalid DS18B20 temperature reading: {raw_temp_val:.2f} C',
                                temp_val, None, None, pwm, rpm_val,
                                'Check DS18B20 wiring / sensor')
                trigger_alert(
                    'temp_sensor_invalid',
                    '[WARNING] DS18B20 Sensor Invalid',
                    f'Invalid DS18B20 reading: {raw_temp_val:.2f} C.\n'
                    f'Last valid temperature used: {temp_val:.2f} C.\n'
                    f'Check sensor wiring.'
                )
        curr_val = float(sample.get('Current', 0.0))

        # RPM smoothing
        if 0 <= rpm_val < 2500:
            rpm_window.append(rpm_val)
            smoothed_rpm = sum(rpm_window) / len(rpm_window)
        else:
            smoothed_rpm = rpm_data[-1] if rpm_data else 0.0

        vib_x_buffer.append(vx)
        current_buffer.append(curr_val)
        live_vib_x_std    = float(np.std(vib_x_buffer))    if len(vib_x_buffer) > 1 else 0.0
        live_current_mean = float(np.mean(current_buffer))  if len(current_buffer) > 0 else curr_val

        # FIX: وقت التشغيل يُحسب من وقت البدء المحدد مرة واحدة
        current_runtime = time.time() - _process_start_time
        vz_dynamic      = vz - 9.81
        health          = compute_health_index(vx, vy, vz_dynamic, temp_val, curr_val)
        rul             = estimate_rul(health, current_runtime)

        current_health  = health
        rul_estimate    = rul
        latest_runtime  = current_runtime
        latest_rpm      = smoothed_rpm
        latest_temp     = temp_val
        latest_current  = curr_val
        latest_source   = source
        latest_ack      = str(int(pwm))

        # ---- AI inference & fault detection ----
        _local_ai_status = ai_prediction_status
        _local_color     = status_color

        if health < 40.0:
            msg = f'Motor health dropped to {health:.1f}%'
            db_insert_fault('health_low', msg, health, temp_val, curr_val, pwm, smoothed_rpm,
                            'Plan maintenance')
            trigger_alert('health_low',
                          f'[WARNING] Motor Health Critical: {health:.0f}%',
                          f'{msg}\nTime   : {time.strftime("%Y-%m-%d %H:%M:%S")}'
                          f'\nRUL    : {rul}\nTemp   : {temp_val:.2f} C'
                          f'\nCurrent: {curr_val:.3f} A\n\nPlan maintenance before health reaches 30%.')

        if pwm > 235 and live_current_mean < 0.05 and should_fire('voltage_drop'):
            _local_ai_status = '[!] WARNING: POWER SUPPLY VOLTAGE DROP DETECTED!'
            _local_color     = 'magenta'
            db_insert_fault('voltage_drop', 'Power supply voltage drop detected',
                            health, temp_val, curr_val, pwm, smoothed_rpm, 'Check power supply')
            trigger_alert('voltage_drop',
                          '[WARNING] Power Supply Voltage Drop Detected',
                          f'Voltage drop detected at {time.strftime("%H:%M:%S")}'
                          f'\nPWM: {pwm}  |  Current Mean: {live_current_mean:.3f} A'
                          f'\nCheck power supply connections.')
        else:
            live_features = pd.DataFrame(
                [[pwm, smoothed_rpm, vx, vy, vz, temp_val,
                  curr_val, live_vib_x_std, live_current_mean]],
                columns=FEATURE_COLUMNS
            )
            try:
                prediction = int(clf_model.predict(live_features)[0])
                prob = None
                if hasattr(clf_model, 'predict_proba'):
                    try:
                        prob = float(clf_model.predict_proba(live_features)[0][1])
                    except Exception:
                        prob = None

                if prediction == 1:
                    _local_ai_status = '[ALERT] AI: ACTIVE ANOMALY DETECTED!'
                    if prob is not None:
                        _local_ai_status += f' | P={prob:.2f}'
                    _local_color = 'red'
                    if should_fire('anomaly'):
                        db_insert_fault('anomaly', 'AI detected anomaly',
                                        health, temp_val, curr_val, pwm, smoothed_rpm,
                                        'Inspect motor immediately')
                        trigger_alert('anomaly',
                                      '[ALERT] Motor Anomaly Detected — AI Alert',
                                      f'Random Forest detected an anomaly!\n\n'
                                      f'Time   : {time.strftime("%Y-%m-%d %H:%M:%S")}\n'
                                      f'Health : {health:.1f}%\nRUL    : {rul}\n'
                                      f'Temp   : {temp_val:.2f} C\nVib Y  : {vy:.3f} m/s2\n'
                                      f'Current: {curr_val:.3f} A\nPWM    : {pwm}\n\n'
                                      f'Action : Inspect motor immediately!')
                else:
                    _local_ai_status = '[OK] SYSTEM HEALTHY (Normal)'
                    if prob is not None:
                        _local_ai_status += f' | P={prob:.2f}'
                    _local_color = 'green'
            except Exception as e:
                _local_ai_status = f'[ERR] AI inference failed: {e}'
                _local_color     = 'orange'

        # Predictive warning before shutdown.
        if health <= 40.0 and should_fire('health_warning', cooldown=60):
            db_insert_fault(
                'health_warning',
                f'Motor health approaching critical level: {health:.1f}%',
                health, temp_val, curr_val, pwm, smoothed_rpm,
                'Prepare controlled stop / inspect motor'
            )
            trigger_alert(
                'health_warning',
                f'[WARNING] Motor Health Approaching Critical: {health:.0f}%',
                f'Health: {health:.1f}%\nRUL: {rul}\n'
                f'Temp: {temp_val:.2f} C\nCurrent: {curr_val:.3f} A\n'
                f'PWM: {pwm}\nPrepare a controlled stop and inspect the motor.'
            )

        # Hard safety thresholds. These stop BOTH systems.
        emergency_reason = None
        if curr_val > 6.5:
            emergency_reason = f'Over-current: {curr_val:.3f} A > 6.5 A'
        elif temp_val > 45.0:
            emergency_reason = f'Over-temperature: {temp_val:.2f} C > 45 C'
        elif health <= 30.0:
            emergency_reason = f'Critical health: {health:.1f}% <= 30%'

        if emergency_reason:
              global safety_condition_cleared
        if integrated_safety_active and not emergency_reason:
            safety_condition_cleared = True
        elif emergency_reason:
            safety_condition_cleared = False
            coordinated_safety_stop(emergency_reason, source='ESP32/AI')
            if should_fire('emergency', cooldown=10):
                db_insert_fault(
                    'emergency',
                    'Unified emergency shutdown triggered',
                    health, temp_val, curr_val, pwm, smoothed_rpm,
                    'ESP32 stopped + PLC/Factory I/O stop requested'
                )
                trigger_alert(
                    'emergency',
                    '[EMERGENCY] Unified Digital Twin Shutdown',
                    f'EMERGENCY SHUTDOWN at {time.strftime("%Y-%m-%d %H:%M:%S")}\n\n'
                    f'Reason: {emergency_reason}\n'
                    f'Current: {curr_val:.3f} A\n'
                    f'Temp: {temp_val:.2f} C\n'
                    f'Health: {health:.1f}%\n'
                    f'PWM: {pwm}\n\n'
                    f'Action: ESP32 motor stopped + PLC/Factory I/O stop requested.\n'
                    f'Inspect hardware before reset/restart.'
                )

        # Update plot buffers
        with data_lock:
            time_data.append(current_runtime)
            pwm_data.append(pwm)
            rpm_data.append(smoothed_rpm)
            vib_x.append(vx)
            vib_y.append(vy)
            vib_z.append(vz)
            temp_data.append(temp_val)
            current_data.append(curr_val)
            health_data.append(health)

        # Persist to DB (blocking OK — runs in worker thread)
        db_insert_telemetry({
            'ts':          time.strftime('%Y-%m-%d %H:%M:%S'),
            'source':      source,
            'runtime_sec': round(current_runtime, 2),
            'pwm':         pwm,
            'rpm':         round(smoothed_rpm, 2),
            'vib_x':       round(vx, 3),
            'vib_y':       round(vy, 3),
            'vib_z':       round(vz, 3),
            'temp':        round(temp_val, 2),
            'current':     round(curr_val, 3),
            'health':      round(health, 1),
            'rul':         rul,
            'ai':          _local_ai_status,
            'status_color': _local_color,
            'target_pwm':  target_pwm_wifi,
        })

        # Update globals under lock
        with state_lock:
            ai_prediction_status = _local_ai_status
            status_color         = _local_color

        # FIX: إرسال WS خارج state_lock تماماً لتجنب deadlock
        telemetry_payload = {
            'type':        'telemetry',
            'runtime_sec': round(current_runtime, 2),
            'pwm':         pwm,
            'rpm':         round(smoothed_rpm, 2),
            'vib_x':       round(vx, 3),
            'vib_y':       round(vy, 3),
            'vib_z':       round(vz, 3),
            'temp':        round(temp_val, 2),
            'current':     round(curr_val, 3),
            'health':      round(health, 1),
            'rul':         rul,
            'ai':          _local_ai_status,
            'status_color': _local_color,
            'ack':         str(int(pwm)),
            'pwm_target':  target_pwm_wifi,
            'source':      source,
            'fault_count': fault_count,
            'state': {
                'ai':          _local_ai_status,
                'health':      round(health, 1),
                'rul':         rul,
                'ack':         str(int(pwm)),
                'status_color': _local_color,
                'pwm_target':  target_pwm_wifi,
                'rpm':         round(smoothed_rpm, 2),
                'temp':        round(temp_val, 2),
                'current':     round(curr_val, 3),
                'fault_count': fault_count,
                'source':      source,
                'integrated_safety_active': integrated_safety_active,
                'last_integrated_safety_reason': last_integrated_safety_reason,
            }
        }

    # FIX: enqueue_ws بعد الخروج من process_lock تماماً
    enqueue_ws(telemetry_payload)


# ---------------------------
# Serial / WiFi ingestion threads
# ---------------------------
def read_serial_thread():
    while True:
        try:
            if ser and ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='replace').strip()
                if not line:
                    continue
                if line.startswith('ACK_PWM:'):
                    with state_lock:
                        globals()['latest_ack'] = line.split(':', 1)[1]
                    continue
                if ('Speed_PWM:' in line and 'RPM:' in line and
                        'Vib_X:' in line and 'Temp:' in line):
                    d = {}
                    for part in line.split('|'):
                        if ':' in part:
                            k, v = part.split(':', 1)
                            try:
                                d[k.strip()] = float(v.strip())
                            except ValueError:
                                pass
                    if d:
                        process_sample(d, source='serial')
        except Exception as e:
            print(f'[ERR] Serial thread: {e}')
            time.sleep(0.1)


def process_wifi_data():
    while True:
        try:
            item = telemetry_queue.get(timeout=1.0)
            process_sample(item['data'], source='wifi')
        except queue.Empty:
            continue
        except Exception as e:
            print(f'[ERR] WiFi processing: {e}')


# ---------------------------
# Server runners
# ---------------------------
def run_flask_server():
    ip = get_local_ip()
    print(f"\n{'='*60}")
    print(f'  [HTTP] Dashboard : http://{ip}:{HTTP_PORT}/')
    print(f'  [HTTP] Fault log : http://{ip}:{HTTP_PORT}/history')
    print(f'  [HTTP] Train AI  : http://{ip}:{HTTP_PORT}/train')
    print(f'  [HTTP] ESP32 IP  : PYTHON_IP = "{ip}"')
    print(f'  [PLC]  S7-PLCSIM : {PLC_IP} / Rack {PLC_RACK} / Slot {PLC_SLOT}')
    print(f'  [WS]   WebSocket : ws://{ip}:{WS_PORT}')
    print(f"{'='*60}\n")
    app.run(host=HTTP_HOST, port=HTTP_PORT,
            debug=False, use_reloader=False, threaded=True)


# ---------------------------
# Optional local Matplotlib monitor (disabled by default)
# ---------------------------
def start_local_monitor():
    if not ENABLE_LOCAL_MATPLOTLIB:
        return
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from matplotlib.widgets import Slider

    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(11, 12))
    ax1_twin = ax1.twinx()
    ax3_twin = ax3.twinx()
    plt.subplots_adjust(bottom=0.10, hspace=0.45)

    ax_slider = plt.axes([0.2, 0.03, 0.6, 0.025], facecolor='lightgray')
    pwm_slider = Slider(ax_slider, 'Adjust PWM (0-235)', 0, 235,
                        valinit=0, valfmt='%d', color='tab:red')
    pwm_slider.on_changed(lambda val: apply_pwm_command(int(val), source='local'))

    def update_plot(_):
        if not time_data:
            return
        t_list    = list(time_data)
        rpm_list  = list(rpm_data)
        pwm_list  = list(pwm_data)
        vibx_list = list(vib_x)
        viby_list = list(vib_y)
        vibz_list = list(vib_z)
        temp_list = list(temp_data)
        curr_list = list(current_data)
        h_list    = list(health_data)

        ax1.clear(); ax1_twin.clear()
        ax2.clear(); ax3.clear(); ax3_twin.clear(); ax4.clear()

        ax1.plot(t_list, rpm_list,  color='tab:blue',   label='Actual RPM')
        ax1_twin.plot(t_list, pwm_list, color='tab:red', label='Target PWM', linestyle='--')
        ax2.plot(t_list, vibx_list, label='Vibration X')
        ax2.plot(t_list, viby_list, label='Vibration Y')
        ax2.plot(t_list, [z - 9.81 for z in vibz_list], label='Vibration Z (Dynamic)')
        ax3.plot(t_list, temp_list, color='tab:orange',  label='Motor Temperature')
        ax3_twin.plot(t_list, curr_list, color='tab:purple',
                      label='Current Consumption', linestyle='-.')
        if h_list:
            ax4.plot(t_list[-len(h_list):], h_list, label='Health Index')

    FuncAnimation(fig, update_plot, interval=PLOT_UPDATE_INTERVAL_MS, cache_frame_data=False)
    plt.show()


# ---------------------------
# Main
# ---------------------------
def main():
    global SERIAL_ENABLED, ser, fault_count, clf_model, _process_start_time

    init_db()
    fault_count = fault_count_total()
    load_or_train_model()

    # FIX: تحديد وقت البداية مرة واحدة فقط
    _process_start_time = time.time()

    # Serial (optional)
    SERIAL_ENABLED = False
    ser = None
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        time.sleep(2)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        SERIAL_ENABLED = True
        print(f'[OK] Serial connected → {SERIAL_PORT}')
    except Exception as e:
        ser = None
        print(f'[INFO] No cable ({e}) → WiFi-only mode')

    if SERIAL_ENABLED:
        threading.Thread(target=read_serial_thread, daemon=True).start()
        print('[OK] Serial thread started')

    if PLC_ENABLED:
        threading.Thread(target=run_plc_monitor, name='PLC-Monitor', daemon=True).start()
        print('[OK] PLC/Factory I/O monitor thread started')

    # Start WiFi ingestion after PLC manager exists so unified safety is ready immediately.
    threading.Thread(target=process_wifi_data,  daemon=True).start()
    threading.Thread(target=run_flask_server,    daemon=True).start()
    threading.Thread(target=start_ws_server,     daemon=True).start()
    threading.Thread(target=start_local_monitor, daemon=True).start()

    # Keep alive
    while True:
        time.sleep(1)


if __name__ == '__main__':
    main()
