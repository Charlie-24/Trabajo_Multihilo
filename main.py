import threading
import time
import socket
import os
import psutil
import requests

# Intentar importar pynvml para GPU NVIDIA
try:
    import pynvml
    pynvml.nvmlInit()
    HAS_GPU = True
except Exception:
    HAS_GPU = False

from influxdb_client_3 import InfluxDBClient3, Point
from datetime import datetime

# --- Configuración de InfluxDB ---
INFLUX_HOST = "http://localhost:8181"
DATABASE = "MonitorizacionPrueba2"
client = InfluxDBClient3(host=INFLUX_HOST, database=DATABASE, token=None)
print(f"Conectado a InfluxDB, usando la base de datos '{DATABASE}'")

# --- Configuración del log local ---
LOG_FILE = "monitor.log"
GPU_THRESHOLD = 20.0  # °C
RUN_EVENTS = 6
lock = threading.Lock()
entries = []
event_counter = 0
worker_threads = []
stop_event = threading.Event()

# --- crear carpeta si no existe ---
log_dir = os.path.dirname(LOG_FILE)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)

# --- obtener IP local ---
def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

IP_ADDR = get_ip_address()

# --- TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8540727798:AAHdcpMpyceL4z7Pcbd5XGIxguVWnZDO1g4"
TELEGRAM_CHAT_ID = "6340007840"

def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram no configurado (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID vacíos).")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        resp = requests.post(url, json=payload, timeout=6)
        return resp.ok
    except Exception as e:
        print("Error al enviar Telegram:", e)
        return False

# --- hilos que simulan tareas ---
def worker(name, interval):
    while not stop_event.is_set():
        time.sleep(interval)

# --- obtener datos reales ---
def get_gpu_temperature():
    if not HAS_GPU:
        return 0.0
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        return pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
    except:
        return 0.0

def get_memory_usage():
    return psutil.virtual_memory().percent

# --- crear entrada de log ---
def create_log_entry(gpu_temp, mem_percent, running_tasks):
    ts = datetime.now().strftime("%d %m %Y %H:%M:%S")
    lines = [
        f"{ts} + GPU: {gpu_temp:.1f} °C + IP: {IP_ADDR}",
        f"{ts} + Memoria: {mem_percent:.1f}% + IP: {IP_ADDR}",
        f"{ts} + Tareas: {', '.join(running_tasks)}"
    ]
    return "\n".join(lines)

def write_log_file():
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            for i, entry in enumerate(entries):
                f.write(entry)
                if i != len(entries) - 1:
                    f.write("\n\n---\n\n")
    except Exception as e:
        print("Error al escribir el log:", e)

# --- hilo monitor GPU ---
def gpu_monitor_loop():
    global event_counter
    while event_counter < RUN_EVENTS:
        gpu_temp = get_gpu_temperature()
        mem_percent = get_memory_usage()
        running_tasks = [t.name for t in threading.enumerate() if t.name.startswith("Worker-")]

        # --- Crear la entrada ---
        entry_text = create_log_entry(gpu_temp, mem_percent, running_tasks)

        # --- Guardar siempre en InfluxDB como texto completo ---
        point = Point("system_monitor") \
            .tag("host", IP_ADDR) \
            .field("entry", entry_text) \
            .time(datetime.utcnow())
        try:
            client.write(point)
        except Exception as e:
            print("Error al escribir en InfluxDB:", e)

        # --- Guardar en log solo si supera el umbral ---
        if gpu_temp > GPU_THRESHOLD:
            with lock:
                entries.append(entry_text)
                if len(entries) > 5:
                    entries[:] = entries[-5:]
                write_log_file()
            print(f"Alerta GPU {gpu_temp:.1f}°C registrada")

        event_counter += 1
        time.sleep(1.0)

# --- lanzar hilos workers ---
for i in range(1, 5):
    t = threading.Thread(target=worker, args=(f"task{i}", 0.6 + i*0.2), name=f"Worker-{i}", daemon=True)
    worker_threads.append(t)
    t.start()

# --- lanzar monitor ---
monitor_thread = threading.Thread(target=gpu_monitor_loop, name="GPU-Monitor", daemon=True)
monitor_thread.start()

# --- esperar a que termine demo ---
while event_counter < RUN_EVENTS:
    time.sleep(0.2)

stop_event.set()
time.sleep(0.2)

# --- enviar la última lectura por Telegram ---
if entries:
    ultima_entrada = entries[-1]
    sent = send_telegram(ultima_entrada)
    if sent:
        print("Última entrada enviada por Telegram.")
    else:
        print("No se pudo enviar la última entrada por Telegram.")

print(f"\nSe han registrado {len(entries)} alertas de GPU y las últimas 5 en '{LOG_FILE}'")

# --- limpiar pynvml al final ---
if HAS_GPU:
    try:
        pynvml.nvmlShutdown()
    except:
        pass

# --- CONSULTA DE VERIFICACIÓN: MOSTRAR TODAS LAS LECTURAS DE ESTA SESIÓN ---
try:
    table = client.query(
        "SELECT * FROM system_monitor ORDER BY time ASC",
        language="sql"
    )

    print("\nTodas las lecturas de esta sesión guardadas en InfluxDB:")
    try:
        import pandas as pd
        df = table.to_pandas()
        for idx, row in df.iterrows():
            print(row['entry'])
    except:
        for row in table:
            print(row['entry'])
except Exception as e:
    print("Error al consultar InfluxDB:", e)
