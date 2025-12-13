import os
import socket
import threading
import time
from datetime import datetime
from typing import List
from queue import Queue, Empty

import psutil
import requests
import warnings

warnings.filterwarnings("ignore")

try:
    import pynvml
    pynvml.nvmlInit()
    HAS_GPU = True
except Exception:
    HAS_GPU = False

from influxdb_client_3 import InfluxDBClient3, Point

# ------------------------------
# Configuración
# ------------------------------
INFLUX_HOST = "http://localhost:8181"
DATABASE = "MonitorizacionPrueba2"
LOG_FILE = "monitor.log"
GPU_THRESHOLD = 20.0
RUN_EVENTS = 7
MAX_LOG_ENTRIES = 5
TELEGRAM_BOT_TOKEN = "8540727798:AAHdcpMpyceL4z7Pcbd5XGIxguVWnZDO1g4"
TELEGRAM_CHAT_ID = "6340007840"

# ------------------------------
# Utilidades
# ------------------------------
def ensure_log_dir(path: str) -> None:
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)

def get_ip_address() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        return requests.post(url, json=payload, timeout=5).ok
    except Exception:
        return False

# ------------------------------
# Monitor Class
# ------------------------------
class SystemMonitor:

    def __init__(self, influx_host, database, log_file, gpu_threshold, run_events):
        self.client = InfluxDBClient3(host=influx_host, database=database, token=None)

        self.log_file = log_file
        self.gpu_threshold = gpu_threshold
        self.run_events = run_events

        self.lock = threading.Lock()
        self.entries: List[str] = []
        self.last_alert = None

        # Colas para consumidores
        self.log_queue = Queue()
        self.influx_queue = Queue()

        self.stop_event = threading.Event()
        self.event_counter = 0

        self.ip_addr = get_ip_address()
        ensure_log_dir(self.log_file)

    # ---- getters ----
    def get_gpu_temperature(self) -> float:
        if not HAS_GPU:
            return 0.0
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            return 0.0

    @staticmethod
    def get_memory_usage() -> float:
        return psutil.virtual_memory().percent

    # ---- logging ----
    def create_log_entry(self, gpu, mem, tasks) -> str:
        ts = datetime.now().strftime("%d %m %Y %H:%M:%S")
        return "\n".join([
            f"{ts} + GPU: {gpu:.1f} °C + IP: {self.ip_addr}",
            f"{ts} + Memoria: {mem:.1f}% + IP: {self.ip_addr}",
            f"{ts} + Tareas: {', '.join(tasks)}",
            " "
        ])

    def write_log_file(self):
        with open(self.log_file, "w", encoding="utf-8") as f:
            for i, e in enumerate(self.entries):
                f.write(e)
                if i != len(self.entries) - 1:
                    f.write("\n\n---\n\n")

    def write_influx(self, entry: str):
        p = Point("system_monitor").tag("host", self.ip_addr).field("entry", entry).time(datetime.utcnow())
        self.client.write(p)
        print(f"[InfluxDB] Entrada guardada:\n{entry}\n")  # debug por consola

    # ---- PRODUCTOR ----
    def gpu_monitor_loop(self):
        while self.event_counter < self.run_events:
            gpu = self.get_gpu_temperature()
            mem = self.get_memory_usage()
            tasks = [t.name for t in threading.enumerate() if t.name.startswith("Worker-")]

            if gpu > self.gpu_threshold:
                entry = self.create_log_entry(gpu, mem, tasks)
                # Enviar a los consumidores
                self.log_queue.put(entry)
                self.influx_queue.put(entry)
                self.last_alert = entry  # solo última entrada para Telegram

            self.event_counter += 1
            time.sleep(1)

    # ---- CONSUMIDORES ----
    def log_worker(self):
        while not self.stop_event.is_set() or not self.log_queue.empty():
            try:
                entry = self.log_queue.get(timeout=0.2)
            except Empty:
                continue
            with self.lock:
                self.entries.append(entry)
                self.entries = self.entries[-MAX_LOG_ENTRIES:]
                self.write_log_file()
            self.log_queue.task_done()

    def influx_worker(self):
        while not self.stop_event.is_set() or not self.influx_queue.empty():
            try:
                entry = self.influx_queue.get(timeout=0.2)
            except Empty:
                continue
            self.write_influx(entry)
            self.influx_queue.task_done()

    # ---- workers simulados ----
    @staticmethod
    def worker(interval, stop_event):
        while not stop_event.is_set():
            time.sleep(interval)

    def start_workers(self):
        for i in range(1, 5):
            threading.Thread(
                target=self.worker,
                args=(0.5 + i * 0.2, self.stop_event),
                name=f"Worker-{i}",
                daemon=True
            ).start()

    # ---- ejecución ----
    def run(self):
        self.start_workers()

        # Consumidores
        threading.Thread(target=self.log_worker, daemon=True).start()
        threading.Thread(target=self.influx_worker, daemon=True).start()

        # Productor
        threading.Thread(target=self.gpu_monitor_loop, daemon=True).start()

        # Esperar a terminar eventos
        while self.event_counter < self.run_events:
            time.sleep(0.2)

        # Marcar parada y esperar a que las colas terminen
        self.stop_event.set()
        self.log_queue.join()
        self.influx_queue.join()

        # Última entrada: escribir directamente y enviar por Telegram
        if self.last_alert:
            self.write_influx(self.last_alert)
            send_telegram(self.last_alert)
            print("[Telegram] Última entrada enviada y almacenada en InfluxDB.")

    # ---- consulta SQL final (una vez) ----
    def print_all_influx_entries(self):
        try:
            results = self.client.query(
                "SELECT entry FROM system_monitor ORDER BY time ASC",
                language="sql"
            )
            print("\nEntradas almacenadas en InfluxDB (consulta SQL):")
            for row in results:
                if isinstance(row, dict) and "entry" in row:
                    print(row["entry"])
                else:
                    print(row)
        except Exception as e:
            print("Error consultando InfluxDB:", e)

    def shutdown(self):
        if HAS_GPU:
            pynvml.nvmlShutdown()

# ------------------------------
# Main
# ------------------------------
def main():
    monitor = SystemMonitor(
        INFLUX_HOST,
        DATABASE,
        LOG_FILE,
        GPU_THRESHOLD,
        RUN_EVENTS
    )
    try:
        monitor.run()
        # Llamada a la consulta solo 1 vez
        monitor.print_all_influx_entries()
    finally:
        monitor.shutdown()

if __name__ == "__main__":
    main()
