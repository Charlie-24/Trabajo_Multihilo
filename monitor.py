"""
Monitor de sistema (GPU, memoria, tareas) con:
 - Escritura en InfluxDB (InfluxDBClient3)
 - Registro local (monitor.log)
 - Envío de última entrada por Telegram
 - Hilos worker de ejemplo
"""
import os
import socket
import threading
import time
from datetime import datetime
from typing import List

import psutil
import requests

# Intentar importar pynvml para GPUs NVIDIA
try:
    import pynvml
    pynvml.nvmlInit()
    HAS_GPU = True
except Exception:
    HAS_GPU = False

# InfluxDB v3 client
from influxdb_client_3 import InfluxDBClient3, Point

# ------------------------------
# Configuración (constantes)
# ------------------------------
INFLUX_HOST = "http://localhost:8181"
DATABASE = "MonitorizacionPrueba2"
LOG_FILE = "monitor.log"
GPU_THRESHOLD = 20.0  # ºC (umbral para guardar en log local)
RUN_EVENTS = 7        # número de iteraciones del monitor
MAX_LOG_ENTRIES = 5   # máximo de entradas guardadas localmente
TELEGRAM_BOT_TOKEN = "8540727798:AAHdcpMpyceL4z7Pcbd5XGIxguVWnZDO1g4"
TELEGRAM_CHAT_ID = "6340007840"

# ------------------------------
# Utilidades
# ------------------------------
def ensure_log_dir(path: str) -> None:
    """Crear carpeta del log si no existe."""
    log_dir = os.path.dirname(path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)


def get_ip_address(timeout: float = 0.5) -> str:
    """Obtener IP local (intenta conexión UDP a 8.8.8.8)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def send_telegram(message: str) -> bool:
    """Enviar mensaje por Telegram. Devuelve True si OK."""
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

# ------------------------------
# Monitor Class
# ------------------------------
class SystemMonitor:
    """Clase que encapsula la lógica del monitor."""

    def __init__(
        self,
        influx_host: str,
        database: str,
        log_file: str,
        gpu_threshold: float,
        run_events: int,
    ):
        self.client = InfluxDBClient3(host=influx_host, database=database, token=None)
        print(f"Conectado a InfluxDB, usando la base de datos '{database}'")

        self.log_file = log_file
        self.gpu_threshold = gpu_threshold
        self.run_events = run_events

        self.lock = threading.Lock()
        self.entries: List[str] = []
        self.event_counter = 0
        self.stop_event = threading.Event()
        self.worker_threads: List[threading.Thread] = []

        self.ip_addr = get_ip_address()
        ensure_log_dir(self.log_file)

    # ---- data getters ----
    def get_gpu_temperature(self) -> float:
        """Obtener la temperatura GPU (0.0 si no hay GPU o error)."""
        if not HAS_GPU:
            return 0.0
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            return 0.0

    @staticmethod
    def get_memory_usage() -> float:
        """Porcentaje de uso de memoria."""
        return float(psutil.virtual_memory().percent)

    # ---- logging ----
    def create_log_entry(self, gpu_temp: float, mem_percent: float, running_tasks: List[str]) -> str:
        """Crear texto de entrada (multilínea)."""
        ts = datetime.now().strftime("%d %m %Y %H:%M:%S")
        lines = [
            f"{ts} + GPU: {gpu_temp:.1f} °C + IP: {self.ip_addr}",
            f"{ts} + Memoria: {mem_percent:.1f}% + IP: {self.ip_addr}",
            f"{ts} + Tareas: {', '.join(running_tasks)}",
            f" "
        ]
        return "\n".join(lines)

    def write_log_file(self) -> None:
        """Escribir las entradas actuales en el fichero de log (reemplaza contenido)."""
        try:
            with open(self.log_file, "w", encoding="utf-8") as f:
                for i, entry in enumerate(self.entries):
                    f.write(entry)
                    if i != len(self.entries) - 1:
                        f.write("\n\n---\n\n")
        except Exception as e:
            print("Error al escribir el log:", e)

    # ---- InfluxDB ----
    def write_influx(self, entry_text: str) -> None:
        """Escribe la entrada completa como campo en InfluxDB (medida system_monitor)."""
        point = Point("system_monitor").tag("host", self.ip_addr).field("entry", entry_text).time(datetime.utcnow())
        try:
            self.client.write(point)
        except Exception as e:
            print("Error al escribir en InfluxDB:", e)

    # ---- monitor loop ----
    def gpu_monitor_loop(self) -> None:
        """Bucle del monitor: recolecta, guarda en InfluxDB y en log local si supera umbral."""
        while self.event_counter < self.run_events and not self.stop_event.is_set():
            gpu_temp = self.get_gpu_temperature()
            mem_percent = self.get_memory_usage()
            running_tasks = [t.name for t in threading.enumerate() if t.name.startswith("Worker-")]

            entry_text = self.create_log_entry(gpu_temp, mem_percent, running_tasks)

            # Guardar siempre en InfluxDB
            self.write_influx(entry_text)

            # Guardar en log local sólo si supera umbral
            if gpu_temp > self.gpu_threshold:
                with self.lock:
                    self.entries.append(entry_text)
                    # Mantener sólo las últimas MAX_LOG_ENTRIES
                    if len(self.entries) > MAX_LOG_ENTRIES:
                        self.entries = self.entries[-MAX_LOG_ENTRIES :]
                    self.write_log_file()
                print(f"Alerta GPU {gpu_temp:.1f}°C registrada")

            self.event_counter += 1
            time.sleep(1.0)

    # ---- workers de ejemplo ----
    @staticmethod
    def worker(name: str, interval: float, stop_event: threading.Event) -> None:
        """Worker que simula trabajo; duerme hasta que stop_event esté seteado."""
        t_name = threading.current_thread().name
        while not stop_event.is_set():
            time.sleep(interval)

    def start_workers(self, count: int = 4) -> None:
        """Lanzar varios hilos worker de ejemplo (daemon)."""
        for i in range(1, count + 1):
            interval = 0.6 + i * 0.2
            t = threading.Thread(
                target=self.worker,
                args=(f"task{i}", interval, self.stop_event),
                name=f"Worker-{i}",
                daemon=True,
            )
            self.worker_threads.append(t)
            t.start()

    # ---- lifecycle ----
    def run(self) -> None:
        """Arranca workers, monitor y gestiona parada y envío final por Telegram."""
        # Lanzar workers
        self.start_workers(count=4)

        # Lanzar monitor en hilo
        monitor_thread = threading.Thread(target=self.gpu_monitor_loop, name="GPU-Monitor", daemon=True)
        monitor_thread.start()

        # Esperar a completar la demo
        while self.event_counter < self.run_events and not self.stop_event.is_set():
            time.sleep(0.2)

        # Señal de parada y pequeña espera para terminar hilos
        self.stop_event.set()
        time.sleep(0.2)

        # Enviar última entrada por Telegram si existe
        if self.entries:
            ultima_entrada = self.entries[-1]
            sent = send_telegram(ultima_entrada)
            if sent:
                print("Última entrada enviada por Telegram.")
            else:
                print("No se pudo enviar la última entrada por Telegram.")

        print(f"\nSe han registrado las últimas {len(self.entries)} entradas en '{self.log_file}'")

    def shutdown(self) -> None:
        """Limpieza final (p. ej. pynvml)."""
        if HAS_GPU:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass

    # ---- verificación: consulta InfluxDB (opcional) ----
    def print_all_influx_entries(self) -> None:
        """Consulta y muestra las lecturas guardadas en la sesión (si la API lo permite)."""
        try:
            table = self.client.query("SELECT * FROM system_monitor ORDER BY time ASC", language="sql")
            print("\nTodas las lecturas de esta sesión guardadas en InfluxDB:")
            try:
                import pandas as pd  # local, opcional
                df = table.to_pandas()
                for idx, row in df.iterrows():
                    print(row["entry"])
            except Exception:
                for row in table:
                    print(row["entry"])
        except Exception as e:
            print("Error al consultar InfluxDB:", e)


# ------------------------------
# Ejecución principal
# ------------------------------
def main() -> None:
    monitor = SystemMonitor(
        influx_host=INFLUX_HOST,
        database=DATABASE,
        log_file=LOG_FILE,
        gpu_threshold=GPU_THRESHOLD,
        run_events=RUN_EVENTS,
    )
    try:
        monitor.run()
        monitor.print_all_influx_entries()
    finally:
        monitor.shutdown()


if __name__ == "__main__":
    main()
