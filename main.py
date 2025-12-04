import threading
import time
import socket
import os
import datetime
import psutil
import wmi

# Intentar importar pynvml para GPU NVIDIA
try:
    import pynvml
    pynvml.nvmlInit()
    HAS_GPU = True
except Exception:
    HAS_GPU = False

from influxdb_client_3 import InfluxDBClient3, Point
from datetime import datetime  # Para evitar conflictos con datetime.datetime

# --- Configuración de InfluxDB ---
INFLUX_HOST = "http://localhost:8181"
DATABASE = "Monitorizacion"
client = InfluxDBClient3(host=INFLUX_HOST, database=DATABASE, token=None)

# --- BORRAR Y RECREAR LA BASE DE DATOS ---
try:
    client.drop_database(DATABASE)
    print(f"Base de datos '{DATABASE}' eliminada")
except Exception as e:
    print("No se pudo eliminar la base de datos (quizá no existía):", e)

try:
    client.create_database(DATABASE)
    print(f"Base de datos '{DATABASE}' creada vacía")
except Exception as e:
    print("Error al crear la base de datos:", e)

# --- Configuración del log local ---
LOG_FILE = "Trabajo_multihilo/monitor.log"
THRESHOLD = 20.0  # °C
RUN_EVENTS = 6    # número de lecturas
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

# --- hilos que simulan tareas ---
def worker(name, interval):
    while not stop_event.is_set():
        time.sleep(interval)

# --- funciones para obtener datos reales ---
def get_cpu_temperature():
    try:
        w = wmi.WMI(namespace="root\\wmi")
        temps = w.MSAcpi_ThermalZoneTemperature()
        if temps:
            return temps[0].CurrentTemperature / 10 - 273.15
    except:
        pass
    return 0.0

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
def create_log_entry(cpu_temp, gpu_temp, mem_percent, running_tasks):
    ts = datetime.now().strftime("%d %m %Y %H:%M:%S")
    lines = [
        f"{ts} + CPU: {cpu_temp:.1f} °C + IP: {IP_ADDR}",
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

# --- hilo monitor ---
def cpu_monitor_loop():
    global event_counter
    while event_counter < RUN_EVENTS:
        cpu_temp = get_cpu_temperature()
        gpu_temp = get_gpu_temperature()
        mem_percent = get_memory_usage()
        running_tasks = [t.name for t in threading.enumerate() if t.name.startswith("Worker-")]

        # --- Escribir en monitor.log solo últimas 5 entradas ---
        entry_text = create_log_entry(cpu_temp, gpu_temp, mem_percent, running_tasks)
        with lock:
            entries.append(entry_text)
            if len(entries) > 5:
                entries[:] = entries[-5:]
            write_log_file()

        # --- Guardar todas las lecturas en InfluxDB ---
        point = Point("system_monitor") \
            .tag("host", IP_ADDR) \
            .field("cpu_temp", cpu_temp) \
            .field("gpu_temp", gpu_temp) \
            .field("mem_percent", mem_percent) \
            .time(datetime.utcnow())
        try:
            client.write(point)
        except Exception as e:
            print("Error al escribir en InfluxDB:", e)

        event_counter += 1
        print(f"Lectura {event_counter} guardada")

        time.sleep(1.0)

# --- lanzar hilos workers ---
for i in range(1, 5):
    t = threading.Thread(target=worker, args=(f"task{i}", 0.6 + i*0.2), name=f"Worker-{i}", daemon=True)
    worker_threads.append(t)
    t.start()

# --- lanzar monitor ---
monitor_thread = threading.Thread(target=cpu_monitor_loop, name="CPU-Monitor", daemon=True)
monitor_thread.start()

# --- esperar a que termine demo ---
while event_counter < RUN_EVENTS:
    time.sleep(0.2)

stop_event.set()
time.sleep(0.2)

print(f"\nSe han guardado {RUN_EVENTS} lecturas en InfluxDB y las últimas 5 en '{LOG_FILE}'")

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
            print(f"{row['time']} - CPU: {row['cpu_temp']:.1f}°C, GPU: {row['gpu_temp']:.1f}°C, Mem: {row['mem_percent']:.1f}%")
    except:
        for row in table:
            print(row)

except Exception as e:
    print("Error al consultar InfluxDB:", e)

