"""
ADB Input Module – Captura logs de radio desde dispositivos Android (Nothing Phone 3)
Soporta batching, session ID, buffer con deque, y callback para pipeline.
"""
from typing import Optional, List, Dict, Any, Deque, Callable
import subprocess
import threading
from datetime import datetime
from collections import deque
import queue
import logging
import signal
import sys
import uuid
import json
from typing import Optional, List, Dict, Any, Deque
import time

# Configuración de logging silenciosa para producción
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class ADBInput:
    """
    Capturador de logs ADB para radio móvil.
    
    Características:
    - Batching inteligente (por tamaño y timeout)
    - Session ID único por ejecución
    - Buffer circular con autolimpieza (deque)
    - Procesamiento asíncrono thread-safe
    - Callback para pipeline externo
    """

    def __init__(self,
                 batch_size: int = 30,
                 max_buffer_size: int = 1000,
                 batch_timeout: float = 1.0,
                 enable_file_logging: bool = True):
        """
        Args:
            batch_size: Número de líneas para formar un lote (menor = más reactivo)
            max_buffer_size: Tamaño máximo del buffer interno (deque)
            batch_timeout: Segundos máximos antes de forzar flush
            enable_file_logging: Guardar logs en archivo automáticamente (fallback)
        """
        self.batch_size = batch_size
        self.max_buffer_size = max_buffer_size
        self.batch_timeout = batch_timeout
        self.enable_file_logging = enable_file_logging

        # Identificación única de sesión
        self.session_id = str(uuid.uuid4())
        self.session_start_time = datetime.now()

        # Estado del sistema (thread-safe)
        self._is_running = False
        self._lock = threading.RLock()

        # Buffer circular con autolimpieza
        self._data_buffer: Deque[Dict[str, Any]] = deque(maxlen=max_buffer_size)

        # Cola para procesamiento asíncrono
        self._process_queue = queue.Queue(maxsize=100)

        # Procesos e hilos
        self._adb_process: Optional[subprocess.Popen] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._processor_thread: Optional[threading.Thread] = None
        self._batch_timer: Optional[threading.Timer] = None

        # Callback para pipeline (se establece después)
        self._batch_callback: Optional[Callable] = None

        # Estadísticas
        self._stats = {
            "lines_captured": 0,
            "batches_processed": 0,
            "batches_dropped": 0,
            "buffer_overflows": 0,
            "errors": 0,
            "last_error": None
        }

        self.logger = logging.getLogger(f"ADBInput.{self.session_id[:8]}")
        self.logger.info(f"Sesión iniciada: {self.session_id}")
        self.logger.info(f"Config: batch_size={batch_size}, timeout={batch_timeout}s, buffer={max_buffer_size}")

    # ------------------------------------------------------------------------
    # Métodos públicos
    # ------------------------------------------------------------------------

    def set_batch_callback(self, callback: Callable) -> None:
        """
        Establece el callback que se llamará con cada lote procesado.
        El callback debe aceptar un único argumento: batch (lista de diccionarios).
        """
        self._batch_callback = callback
        self.logger.debug("Callback de lote establecido")

    def start(self) -> bool:
        """Inicia la captura de logs."""
        with self._lock:
            if self._is_running:
                self.logger.warning("Ya está en ejecución")
                return False
            self._is_running = True

        # Hilo de captura
        self._capture_thread = threading.Thread(
            target=self._run_logcat_capture,
            name=f"ADBCapture-{self.session_id[:8]}",
            daemon=True
        )
        self._capture_thread.start()

        # Hilo de procesamiento de la cola
        self._processor_thread = threading.Thread(
            target=self._run_batch_processor,
            name=f"BatchProcessor-{self.session_id[:8]}",
            daemon=True
        )
        self._processor_thread.start()

        self.logger.info("✅ Captura iniciada")
        return True

    def stop(self, timeout: int = 5) -> None:
        """Detiene la captura de manera graceful
        Ahora sin perdida de datos.
        """
        self.logger.info("Deteniendo captura...")

        # 1. Cancelar timer pendiente
        self._cancel_batch_timer()

        # 2. Detener proceso ADB (Cerramos la entrada de datos nuevos)
        self._stop_adb_process(timeout)

        # 3. Flush final de datos pendientes (Ponemos las sobras en la cola)
        self._flush_buffer_final()

        # 4. Esperamos a que la cola se procese (¡El hilo sigue vivo aquí!)
        self.logger.info("Esperando a que la cola de procesamiento se vacíe...")
        try:
            self._process_queue.join()  # Esperar a que se procesen los lotes pendientes
        except Exception as e:
            self.logger.error(f"Error esperando vaciado de cola: {e}")
                
        # 5. AHORA SI, MATAMOS EL HILO AL SILICIO inteligente
        with self._lock:
            self._is_running = False

        # 6. Esperar a que termine el procesador (Usando tu nuevo nombre)
        self._wait_for_silicio(timeout)

        # Mostrar estadísticas finales
        self._log_final_stats()

    def _wait_for_silicio(self, timeout: int) -> None:
        """Espera a que el hilo del procesador termine por completo."""
        if not self._processor_thread or not self._processor_thread.is_alive():
            return
        try:
            self._processor_thread.join(timeout=timeout)
        except Exception as e:
            self.logger.error(f"Error cerrando el hilo al silicio: {e}")    
    #cgingadera hecha funciona        
    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas actuales."""
        with self._lock:
            queue_size = self._process_queue.qsize()
            buffer_usage = len(self._data_buffer)
            return {
                "session_id": self.session_id,
                "session_duration": (datetime.now() - self.session_start_time).total_seconds(),
                "is_running": self._is_running,
                "buffer_usage": buffer_usage,
                "buffer_max_size": self.max_buffer_size,
                "buffer_usage_percent": (buffer_usage / self.max_buffer_size * 100) if self.max_buffer_size else 0,
                "queue_size": queue_size,
                "lines_captured": self._stats["lines_captured"],
                "batches_processed": self._stats["batches_processed"],
                "batches_dropped": self._stats["batches_dropped"],
                "buffer_overflows": self._stats["buffer_overflows"],
                "errors": self._stats["errors"],
                "last_error": self._stats["last_error"],
                "efficiency": self._calculate_efficiency()
            }

    # ------------------------------------------------------------------------
    # Métodos internos de captura
    # ------------------------------------------------------------------------

    def _run_logcat_capture(self) -> None:
        """Ejecuta el proceso de captura de logcat."""
        try:
            # Limpiar buffer de logcat
            self._clear_logcat_buffer()

            # Iniciar proceso ADB
            self._start_adb_process()

            # Reiniciar timer para el primer batch
            self._reset_batch_timer()

            # Verificar proceso
            if not self._adb_process or not self._adb_process.stdout:
                self.logger.error("No se pudo abrir stdout del proceso ADB")
                self._stop_with_error("ADB process stdout not available")
                return

            self.logger.info("Comenzando lectura de logs...")

            for line in iter(self._adb_process.stdout.readline, ''):
                if not self._is_running:
                    break
                if line.strip():
                    self._process_raw_line(line)

        except Exception as e:
            self.logger.error(f"Error en captura: {e}")
            self._stop_with_error(str(e))

    def _clear_logcat_buffer(self) -> None:
        """Limpia el buffer de logcat antes de comenzar."""
        try:
            subprocess.run(
                ["adb", "logcat", "-c"],
                capture_output=True,
                timeout=5,
                check=False
            )
            self.logger.debug("Buffer de logcat limpiado")
        except Exception as e:
            self.logger.warning(f"Error limpiando buffer: {e}")

    def _start_adb_process(self) -> None:
        """Inicia el proceso ADB para capturar logs de radio."""
        # Comando: logcat -b radio -v time
        cmd = ["adb", "logcat", "-b", "radio", "-v", "time"]
        self._adb_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors='ignore',
            bufsize=1  # Line buffered
        )
        self.logger.info(f"Proceso ADB iniciado (PID: {self._adb_process.pid})")

    def _process_raw_line(self, line: str) -> None:
        """Procesa una línea cruda del logcat."""
        entry = self._create_log_entry(line)

        with self._lock:
            self._data_buffer.append(entry)
            self._stats["lines_captured"] += 1

            # Verificar overflow (deque eliminó datos viejos)
            if len(self._data_buffer) == self.max_buffer_size:
                self._stats["buffer_overflows"] += 1
                self.logger.warning(f"Buffer lleno! Overflow #{self._stats['buffer_overflows']}")

            # Flush si alcanzamos el tamaño de batch
            if len(self._data_buffer) >= self.batch_size:
                self._flush_buffer()

    def _create_log_entry(self, line: str) -> Dict[str, Any]:
        """Crea una entrada estructurada a partir de una línea de log."""
        return {
            "session_id": self.session_id,
            "session_start": self.session_start_time.isoformat(),
            "timestamp": datetime.now().isoformat(),
            "raw_payload": line.strip(),
            "source": "Snapdragon_Modem",
            "adb_timestamp": self._extract_timestamp(line),
            "sequence_number": self._stats["lines_captured"]
        }

    def _extract_timestamp(self, line: str) -> Optional[str]:
        """Extrae el timestamp original del log de ADB."""
        try:
            parts = line.split()
            if len(parts) >= 2:
                timestamp = f"{parts[0]} {parts[1]}"
                if "-" in timestamp and ":" in timestamp:
                    return timestamp
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------------
    # Métodos internos de batching
    # ------------------------------------------------------------------------

    def _reset_batch_timer(self) -> None:
        """Reinicia el timer para forzar flush automático."""
        self._cancel_batch_timer()
        if self._is_running:
            self._batch_timer = threading.Timer(self.batch_timeout, self._force_batch_flush)
            self._batch_timer.daemon = True
            self._batch_timer.start()

    def _cancel_batch_timer(self) -> None:
        """Cancela el timer de flush si existe."""
        if self._batch_timer:
            self._batch_timer.cancel()
            self._batch_timer = None

    def _force_batch_flush(self) -> None:
        """Fuerza el flush del buffer actual (llamado por timer)."""
        with self._lock:
            if self._data_buffer and self._is_running:
                self.logger.debug(f"Forzando flush: {len(self._data_buffer)} eventos")
                self._flush_buffer()
        self._reset_batch_timer()

    def _flush_buffer(self) -> None:
        """
        Envía el buffer actual a la cola de procesamiento.
        Debe llamarse con el lock adquirido.
        """
        if not self._data_buffer:
            return

        batch = list(self._data_buffer)
        self._data_buffer.clear()

        try:
            self._process_queue.put(batch, timeout=2)
            self._stats["batches_processed"] += 1
            self.logger.debug(f"Lote #{self._stats['batches_processed']}: {len(batch)} eventos encolados")
        except queue.Full:
            self._stats["batches_dropped"] += 1
            self.logger.warning(f"Cola llena! Lote #{self._stats['batches_dropped']} perdido")
            self._save_emergency_batch(batch)

    def _flush_buffer_final(self) -> None:
        """Flush final para datos pendientes al detener."""
        with self._lock:
            if self._data_buffer:
                self.logger.info(f"Flush final: {len(self._data_buffer)} eventos pendientes")
                self._flush_buffer()

    def _save_emergency_batch(self, batch: List[Dict]) -> None:
        """Guarda lote de emergencia cuando la cola está llena."""
        try:
            filename = f"emergency_{self.session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump({
                    "session_id": self.session_id,
                    "timestamp": datetime.now().isoformat(),
                    "batch_size": len(batch),
                    "reason": "queue_full",
                    "data": batch
                }, f, indent=2, ensure_ascii=False)
            self.logger.info(f"Lote de emergencia guardado: {filename}")
        except Exception as e:
            self.logger.error(f"Error guardando emergencia: {e}")

    # ------------------------------------------------------------------------
    # Métodos internos de procesamiento de cola
    # ------------------------------------------------------------------------

    def _run_batch_processor(self) -> None:
        """Hilo que procesa los lotes de la cola."""
        while self._is_running:
            try:
                batch = self._process_queue.get(timeout=1)
                self._process_batch(batch)
                self._process_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"Error en procesador: {e}")
                with self._lock:
                    self._stats["errors"] += 1
                    self._stats["last_error"] = str(e)

    def _process_batch(self, batch: List[Dict]) -> None:
        """
        Procesa un lote de datos.
        Si hay un callback configurado (por el pipeline), lo usa.
        Si no, y enable_file_logging está activo, guarda en archivo.
        """
        if not batch:
            return

        if self._batch_callback:
            try:
                self._batch_callback(batch)
                return
            except Exception as e:
                self.logger.error(f"Error en callback: {e}")

        # Fallback: guardar en archivo
        if self.enable_file_logging:
            self._save_batch_to_file(batch)

    def _save_batch_to_file(self, batch: List[Dict]) -> None:
        """Guarda un lote en archivo (formato JSONL)."""
        try:
            filename = f"logs_{self.session_id}_{datetime.now().strftime('%Y%m%d')}.jsonl"
            with open(filename, 'a', encoding='utf-8') as f:
                for entry in batch:
                    entry['batch_number'] = self._stats["batches_processed"]
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            self.logger.error(f"Error guardando lote: {e}")

    # ------------------------------------------------------------------------
    # Métodos internos de shutdown
    # ------------------------------------------------------------------------

    def _stop_adb_process(self, timeout: int) -> None:
        """Detiene el proceso ADB gracefulmente."""
        if not self._adb_process:
            return
        if self._adb_process.poll() is None:
            try:
                self._adb_process.terminate()
                self._adb_process.wait(timeout=timeout)
                self.logger.info("Proceso ADB terminado")
            except subprocess.TimeoutExpired:
                self._adb_process.kill()
                self.logger.warning("Proceso ADB forzado a terminar")

    def _wait_for_processor(self, timeout: int) -> None:
        """Espera a que termine el procesador."""
        if not self._processor_thread or not self._processor_thread.is_alive():
            return
        try:
            self._process_queue.join()
            self._processor_thread.join(timeout=timeout)
        except Exception as e:
            self.logger.error(f"Error esperando procesador: {e}")

    def _stop_with_error(self, error_msg: str) -> None:
        """Detiene el sistema con un error."""
        with self._lock:
            self._stats["errors"] += 1
            self._stats["last_error"] = error_msg
            self._is_running = False
        self.logger.error(f"Deteniendo por error: {error_msg}")

    def _log_final_stats(self) -> None:
        """Registra estadísticas finales."""
        stats = self.get_stats()
        self.logger.info("=" * 50)
        self.logger.info("ESTADÍSTICAS FINALES:")
        self.logger.info(f"  Sesión: {stats['session_id']}")
        self.logger.info(f"  Duración: {stats['session_duration']:.2f}s")
        self.logger.info(f"  Líneas capturadas: {stats['lines_captured']}")
        self.logger.info(f"  Lotes procesados: {stats['batches_processed']}")
        self.logger.info(f"  Lotes perdidos: {stats['batches_dropped']}")
        self.logger.info(f"  Buffer overflows: {stats['buffer_overflows']}")
        self.logger.info(f"  Errores: {stats['errors']}")
        self.logger.info(f"  Eficiencia: {stats['efficiency']:.1f}%")
        self.logger.info("=" * 50)

    def _calculate_efficiency(self) -> float:
        """Calcula eficiencia = (eventos procesados / eventos capturados) * 100."""
        lines = self._stats["lines_captured"]
        if lines == 0:
            return 100.0
        processed = self._stats["batches_processed"] * self.batch_size
        return min(100.0, (processed / lines) * 100)


# ------------------------------------------------------------------------
# Prueba rápida (ejecutar directamente)
# ------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    def dummy_callback(batch):
        print(f"Callback recibió lote de {len(batch)} eventos")
        if batch:
            print("  Último evento:", batch[-1]["raw_payload"][:80])

    adb = ADBInput(batch_size=5, batch_timeout=1.0, enable_file_logging=False)
    adb.set_batch_callback(dummy_callback)

    if adb.start():
        print("Capturando logs durante 10 segundos... (presiona Ctrl+C para detener antes)")
        try:
            time.sleep(10)
        except KeyboardInterrupt:
            pass
        finally:
            adb.stop()
    else:
        print("Error al iniciar")
