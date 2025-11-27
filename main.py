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

LOG_FILE = "Trabajo_multihilo/monitor.log"
THRESHOLD = 1.0  # °C
lock = threading.Lock()
entries = []
RUN_EVENTS = 6  # número de eventos de umbral para demo

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
worker_threads = []
stop_event = threading.Event()

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
    ts = datetime.datetime.now().strftime("%d %m %Y %H:%M:%S")
    lines = [
        f"{ts} + Control de temperatura de la CPU: {cpu_temp:.1f} °C + IP: {IP_ADDR}",
        f"{ts} + Control de temperatura de la GPU: {gpu_temp:.1f} °C + IP: {IP_ADDR}",
        f"{ts} + Utilización de memoria: {mem_percent:.1f}% + IP: {IP_ADDR}",
        f"{ts} + Tareas en ejecución: {', '.join(running_tasks)}"
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
event_counter = 0
def cpu_monitor_loop():
    global event_counter
    while event_counter < RUN_EVENTS:
        cpu_temp = get_cpu_temperature()
        gpu_temp = get_gpu_temperature()
        mem_percent = get_memory_usage()

        if cpu_temp > THRESHOLD or gpu_temp > THRESHOLD:
            running_tasks = [t.name for t in threading.enumerate() if t.name.startswith("Worker-")]
            entry_text = create_log_entry(cpu_temp, gpu_temp, mem_percent, running_tasks)
            with lock:
                entries.append(entry_text)
                if len(entries) > 5:
                    entries[:] = entries[-5:]
                write_log_file()
                event_counter += 1

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

# --- mostrar log final ---
print("Contenido final de", LOG_FILE, ":\n")
with open(LOG_FILE, 'r', encoding='utf-8') as f:
    print(f.read())

print(f"\nFichero guardado en: {os.path.abspath(LOG_FILE)}")
print(f"\nNúmero de entradas guardadas: {len(entries)} (<= 5)")

# --- limpiar pynvml al final ---
if HAS_GPU:
    try:
        pynvml.nvmlShutdown()
    except:
        pass
