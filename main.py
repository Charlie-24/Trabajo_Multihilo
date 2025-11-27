import threading, time, socket, os, random, datetime, sys

LOG_FILE = "Trabajo_multihilo/monitor.log"
THRESHOLD = 45.0  # grados
lock = threading.Lock()
entries = []  # cada "entrada" es el grupo de líneas generado cuando se supera el umbral
RUN_EVENTS = 10  # número de eventos de umbral a simular para la demo

# --- asegurar que la carpeta existe ---
log_dir = os.path.dirname(LOG_FILE)
if log_dir:  # si LOG_FILE contiene un directorio (no está solo en la carpeta actual)
    os.makedirs(log_dir, exist_ok=True)

# Intenta obtener IP real (sin enviar datos)
def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return '127.0.0.1'

IP_ADDR = get_ip_address()

# Hilos "tareas" que simulan trabajo concurrente.
worker_threads = []
stop_event = threading.Event()

def worker(name, interval):
    while not stop_event.is_set():
        time.sleep(interval)

def create_log_entry(cpu_temp, gpu_temp, mem_percent, running_tasks):
    ts = datetime.datetime.now().strftime("%d %m %Y %H:%M:%S")
    lines = []
    lines.append(f"{ts} + Control de temperatura de la cpu: {cpu_temp:.1f} °C + IP: {IP_ADDR}")
    lines.append(f"{ts} + Control de temperatura de la gpu: {gpu_temp:.1f} °C + IP: {IP_ADDR}")
    lines.append(f"{ts} + Utilización de memoria: {mem_percent:.1f}% + IP: {IP_ADDR}")
    lines.append(f"{ts} + Tareas en ejecución: {', '.join(running_tasks)}")
    return "\n".join(lines)

def write_log_file():
    # Escribe el contenido actual de `entries` en el fichero, manteniendo orden cronológico y solo las últimas 5 entradas
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            for i, entry in enumerate(entries):
                f.write(entry)
                if i != len(entries) - 1:
                    f.write("\n\n---\n\n")
    except Exception as e:
        print("Error al escribir el log:", e)

# Monitor que comprueba la temperatura (aquí simulada si no hay sensores)
event_counter = 0

def cpu_monitor_loop():
    global event_counter
    while event_counter < RUN_EVENTS:
        base_cpu = 35 + random.random() * 15  # 35..50
        base_gpu = 30 + random.random() * 20  # 30..50
        cpu_temp = base_cpu
        gpu_temp = base_gpu
        mem_percent = 30 + random.random() * 60  # 30..90
        
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

# Lanzar algunos hilos workers para simular concurrencia/tareas en ejecución
for i in range(1, 5):
    t = threading.Thread(target=worker, args=(f"task{i}", 0.6 + i*0.2), name=f"Worker-{i}", daemon=True)
    worker_threads.append(t)
    t.start()

# Lanzar monitor en hilo aparte
monitor_thread = threading.Thread(target=cpu_monitor_loop, name="CPU-Monitor", daemon=True)
monitor_thread.start()

# Esperar a que el monitor simule el número de eventos deseado
while event_counter < RUN_EVENTS:
    time.sleep(0.2)

# Terminamos la demo
stop_event.set()
time.sleep(0.2)

# Mostrar el fichero log resultante
print("Contenido final de", LOG_FILE, ":\n")
with open(LOG_FILE, 'r', encoding='utf-8') as f:
    content = f.read()
print(content)

print(f"\nFichero guardado en: {os.path.abspath(LOG_FILE)}")
print(f"\nNúmero de entradas guardadas: {len(entries)} (debe ser <= 5)")
