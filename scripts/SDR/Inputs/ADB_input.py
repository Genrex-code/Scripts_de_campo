"""
ADB Input Module - Captura logs de radio desde dispositivo Android
Módulo principal de entrada de datos para el sistema de monitoreo de señal.

Características:
- Batching inteligente con timeout
- Session ID único por ejecución
- Deque con autolimpieza para gestión de memoria
- Procesamiento asíncrono thread-safe
- Estadísticas detalladas de rendimiento
- Manejo graceful de señales
"""
import select
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

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class ADBInput:
    """
    Capturador de logs ADB con procesamiento por lotes.
    
    Flujo de datos:
    ADB Logcat → Buffer (deque) → Queue → Process Batch → Pipeline
                ↓
            Flush por tamaño o timeout
    """
    
    def __init__(self, 
                 batch_size: int = 30,
                 max_buffer_size: int = 1000,
                 batch_timeout: float = 1.0,
                 enable_file_logging: bool = True):
        """
        Inicializa el capturador de ADB.
        
        Args:
            batch_size: Número de líneas para formar un lote (menor = más reactivo)
            max_buffer_size: Tamaño máximo del buffer interno (deque)
            batch_timeout: Segundos máximos antes de forzar flush
            enable_file_logging: Guardar logs en archivo automáticamente
        """
        # Configuración
        self.batch_size = batch_size
        self.max_buffer_size = max_buffer_size
        self.batch_timeout = batch_timeout
        self.enable_file_logging = enable_file_logging
        
        # Identificación única de sesión
        self.session_id = str(uuid.uuid4())
        self.session_start_time = datetime.now()
        
        # Estado del sistema (thread-safe)
        self._is_running = False
        self._lock = threading.RLock()  # RLock permite reentrada
        
        # Buffer circular con autolimpieza
        self._data_buffer: Deque[Dict[str, Any]] = deque(maxlen=max_buffer_size)
        
        # Cola para procesamiento asíncrono
        self._process_queue = queue.Queue(maxsize=100)
        
        # Procesos e hilos
        self._adb_process: Optional[subprocess.Popen] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._processor_thread: Optional[threading.Thread] = None
        self._batch_timer: Optional[threading.Timer] = None
        
        # Estadísticas detalladas
        self._stats = {
            "lines_captured": 0,
            "batches_processed": 0,
            "batches_dropped": 0,
            "buffer_overflows": 0,
            "errors": 0,
            "last_error": None
        }
        
        # Logger
        self.logger = logging.getLogger(f"ADBInput.{self.session_id[:8]}")
        self.logger.info(f"Sesión iniciada: {self.session_id}")
        self.logger.info(f"Config: batch_size={batch_size}, timeout={batch_timeout}s, buffer={max_buffer_size}")
    
    # ========================================================================
    # Métodos Públicos
    # ========================================================================
    
    def start(self) -> bool:
        """
        Inicia la captura de logs.
        
        Returns:
            bool: True si se inició correctamente
        """
        with self._lock:
            if self._is_running:
                self.logger.warning("Ya está en ejecución")
                return False
            
            self._is_running = True
        
        # Iniciar hilo de captura
        self._capture_thread = threading.Thread(
            target=self._run_logcat_capture,
            name=f"ADBCapture-{self.session_id[:8]}",
            daemon=True
        )
        self._capture_thread.start()
        
        # Iniciar hilo de procesamiento
        self._processor_thread = threading.Thread(
            target=self._run_batch_processor,
            name=f"BatchProcessor-{self.session_id[:8]}",
            daemon=True
        )
        self._processor_thread.start()
        
        self.logger.info("✅ Captura iniciada")
        return True
    
    def stop(self, timeout: int = 5) -> None:
        """
        Detiene la captura de manera graceful.
        
        Args:
            timeout: Segundos máximos para esperar procesos
        """
        self.logger.info("Deteniendo captura...")
        
        # Detener flag de ejecución
        with self._lock:
            self._is_running = False
        
        # Cancelar timer pendiente
        self._cancel_batch_timer()
        
        # Detener proceso ADB
        self._stop_adb_process(timeout)
        
        # Flush final de datos pendientes
        self._flush_buffer_final()
        
        # Esperar a que termine el procesador
        self._wait_for_processor(timeout)
        
        # Mostrar estadísticas finales
        self._log_final_stats()
    
    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas actuales del sistema."""
        with self._lock:
            queue_size = self._process_queue.qsize()
            buffer_usage = len(self._data_buffer)
            
            return {
                "session_id": self.session_id,
                "session_duration": (datetime.now() - self.session_start_time).total_seconds(),
                "is_running": self._is_running,
                "buffer_usage": buffer_usage,
                "buffer_max_size": self.max_buffer_size,
                "buffer_usage_percent": (buffer_usage / self.max_buffer_size * 100) if self.max_buffer_size > 0 else 0,
                "queue_size": queue_size,
                "queue_max_size": 100,
                "batch_size": self.batch_size,
                "batch_timeout": self.batch_timeout,
                "lines_captured": self._stats["lines_captured"],
                "batches_processed": self._stats["batches_processed"],
                "batches_dropped": self._stats["batches_dropped"],
                "buffer_overflows": self._stats["buffer_overflows"],
                "errors": self._stats["errors"],
                "last_error": self._stats["last_error"],
                "efficiency": self._calculate_efficiency()
            }
    
    def set_batch_callback(self, callback):
        """
        Establece un callback para procesar lotes (usado por el pipeline).
        
        Args:
            callback: Función que recibe (batch) y procesa los datos
        """
        self._process_batch_callback = callback
        self.logger.debug("Callback de procesamiento establecido")
    
    # ========================================================================
    # Métodos Internos - Captura
    # ========================================================================
    
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
            
            # Leer líneas en tiempo real
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
        except subprocess.TimeoutExpired:
            self.logger.warning("Timeout limpiando buffer de logcat")
        except Exception as e:
            self.logger.warning(f"Error limpiando buffer: {e}")
    
    def _start_adb_process(self) -> None:
        """Inicia el proceso ADB para capturar logs de radio."""
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
        """
        Procesa una línea cruda del logcat.
        
        Args:
            line: Línea de log sin procesar
        """
        entry = self._create_log_entry(line)
        
        with self._lock:
            # Añadir al buffer
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
        """
        Crea una entrada estructurada a partir de una línea de log.
        
        Args:
            line: Línea cruda del log
            
        Returns:
            Diccionario con datos estructurados
        """
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
        """
        Extrae el timestamp del log de ADB.
        Formato típico: "01-15 10:30:45.123"
        """
        try:
            parts = line.split()
            if len(parts) >= 2:
                timestamp = f"{parts[0]} {parts[1]}"
                if "-" in timestamp and ":" in timestamp:
                    return timestamp
        except Exception:
            pass
        return None
    
    # ========================================================================
    # Métodos Internos - Batching y Flush
    # ========================================================================
    
    def _reset_batch_timer(self) -> None:
        """Reinicia el timer para forzar flush automático."""
        self._cancel_batch_timer()
        
        if self._is_running:
            self._batch_timer = threading.Timer(
                self.batch_timeout, 
                self._force_batch_flush
            )
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
        Este método debe llamarse con el lock adquirido.
        """
        if not self._data_buffer:
            return
        
        # Tomar copia del buffer actual
        batch = list(self._data_buffer)
        
        # Limpiar buffer
        self._data_buffer.clear()
        
        # Intentar encolar
        try:
            self._process_queue.put(batch, timeout=2)
            self._stats["batches_processed"] += 1
            
            self.logger.debug(
                f"Lote #{self._stats['batches_processed']}: {len(batch)} eventos encolados"
            )
            
        except queue.Full:
            self._stats["batches_dropped"] += 1
            self.logger.warning(
                f"Cola llena! Lote #{self._stats['batches_dropped']} perdido"
            )
            self._save_emergency_batch(batch)
    
    def _flush_buffer_final(self) -> None:
        """Flush final para datos pendientes al detener."""
        with self._lock:
            if self._data_buffer:
                self.logger.info(f"Flush final: {len(self._data_buffer)} eventos pendientes")
                self._flush_buffer()
    
    # ========================================================================
    # Métodos Internos - Procesamiento
    # ========================================================================
    
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
        Este método puede ser sobrescrito por el pipeline.
        
        Args:
            batch: Lista de entradas a procesar
        """
        if not batch:
            return
        
        # Si hay un callback establecido (por el pipeline), usarlo
        if hasattr(self, '_process_batch_callback') and self._process_batch_callback:
            try:
                self._process_batch_callback(batch)
                return
            except Exception as e:
                self.logger.error(f"Error en callback: {e}")
        
        # Comportamiento por defecto: guardar en archivo
        if self.enable_file_logging:
            self._save_batch_to_file(batch)
    
    def _save_batch_to_file(self, batch: List[Dict]) -> None:
        """
        Guarda un lote en archivo (formato JSONL).
        
        Args:
            batch: Lista de entradas a guardar
        """
        try:
            filename = f"logs_{self.session_id}_{datetime.now().strftime('%Y%m%d')}.jsonl"
            
            with open(filename, 'a', encoding='utf-8') as f:
                for entry in batch:
                    entry['batch_number'] = self._stats["batches_processed"]
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    
        except Exception as e:
            self.logger.error(f"Error guardando lote: {e}")
    
    def _save_emergency_batch(self, batch: List[Dict]) -> None:
        """
        Guarda lote de emergencia cuando la cola está llena.
        
        Args:
            batch: Lote a guardar
        """
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
    
    # ========================================================================
    # Métodos Internos - Shutdown
    # ========================================================================
    
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
            # Esperar a que se vacíe la cola
            self._process_queue.join()
            # Esperar al hilo
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
        """
        Calcula eficiencia del sistema.
        
        Eficiencia = (eventos procesados / eventos capturados) * 100
        """
        lines = self._stats["lines_captured"]
        if lines == 0:
            return 100.0
        
        # Estimación: cada lote tiene en promedio batch_size eventos
        processed = self._stats["batches_processed"] * self.batch_size
        return min(100.0, (processed / lines) * 100)


# ========================================================================
# Manejador de Señales para Cierre Graceful
# ========================================================================

class GracefulKiller:
    """
    Manejador de señales para cierre graceful con reporte final.
    """
    
    def __init__(self, adb_input: ADBInput):
        self.adb_input = adb_input
        signal.signal(signal.SIGINT, self._exit_gracefully)
        signal.signal(signal.SIGTERM, self._exit_gracefully)
    
    def _exit_gracefully(self, signum, frame):
        print("\n" + "=" * 60)
        print("⚠️  Recibida señal de interrupción")
        print("=" * 60)
        
        stats = self.adb_input.get_stats()
        
        print("\n📊 REPORTE FINAL DE SESIÓN")
        print("-" * 40)
        print(f"  Sesión ID: {stats['session_id']}")
        print(f"  Duración: {stats['session_duration']:.1f} segundos")
        print(f"  📱 Líneas capturadas: {stats['lines_captured']}")
        print(f"  📦 Lotes procesados: {stats['batches_processed']}")
        print(f"  ❌ Lotes perdidos: {stats['batches_dropped']}")
        print(f"  💾 Buffer overflows: {stats['buffer_overflows']}")
        print(f"  📊 Eficiencia: {stats['efficiency']:.1f}%")
        print("-" * 40)
        
        self.adb_input.stop()
        sys.exit(0)


# ========================================================================
# Monitor en Tiempo Real
# ========================================================================

class ADBMonitor:
    """
    Monitor en tiempo real para visualizar estadísticas en consola.
    """
    
    def __init__(self, adb_input: ADBInput):
        self.adb = adb_input
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
    
    def start(self, interval: int = 3) -> None:
        """Inicia el monitoreo en un hilo separado."""
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
            name="ADBMonitor"
        )
        self._monitor_thread.start()
    
    def stop(self) -> None:
        """Detiene el monitoreo."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)
    
    def _monitor_loop(self, interval: int) -> None:
        """Bucle principal del monitor."""
        while self._running and self.adb._is_running:
            stats = self.adb.get_stats()
            self._display_stats(stats)
            time.sleep(interval)
    
    def _display_stats(self, stats: Dict[str, Any]) -> None:
        """Muestra estadísticas en consola."""
        # Barra de progreso del buffer
        buffer_percent = stats.get("buffer_usage_percent", 0)
        bar_length = 25
        filled = int(bar_length * buffer_percent / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        print(f"\r[{datetime.now().strftime('%H:%M:%S')}] "
              f"Buffer: [{bar}] {buffer_percent:.0f}% | "
              f"📱 {stats['lines_captured']} | "
              f"📦 {stats['batches_processed']} | "
              f"❌ {stats['batches_dropped']} | "
              f"📊 {stats['efficiency']:.0f}%", 
              end="", flush=True)


# ========================================================================
# Punto de Entrada (Pruebas)
# ========================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("   🔧 ADB INPUT - Módulo de Captura de Logs")
    print("=" * 60)
    
    # Configuración
    adb = ADBInput(
        batch_size=30,
        max_buffer_size=2000,
        batch_timeout=1.0,
        enable_file_logging=True
    )
    
    # Manejador de señales
    killer = GracefulKiller(adb)
    
    # Iniciar captura
    if adb.start():
        print(f"\n✅ CAPTURA INICIADA")
        print(f"   📱 Sesión: {adb.session_id[:8]}...")
        print(f"   📊 Batch: {adb.batch_size} líneas")
        print(f"   ⏱️  Timeout: {adb.batch_timeout}s")
        print(f"   💾 Buffer: {adb.max_buffer_size} líneas")
        print("\n📡 Esperando logs... Presiona Ctrl+C para detener\n")
        
        # Iniciar monitor
        monitor = ADBMonitor(adb)
        monitor.start(interval=2)
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            monitor.stop()
            adb.stop()
    else:
        print("❌ Error al iniciar captura")