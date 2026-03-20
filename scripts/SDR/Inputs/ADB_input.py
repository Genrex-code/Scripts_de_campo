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
    Clase mejorada para capturar logs de ADB con:
    - Batching inteligente para manejar ataques de ruido
    - Session ID único para trazar sesiones
    - Deque para autogestión de memoria
    - Procesamiento asíncrono y thread-safe
    """
    
    def __init__(self, 
                 batch_size: int = 50,  # Reducido a 50 para mejor respuesta
                 max_buffer_size: int = 1000,
                 batch_timeout: float = 2.0):  # Timeout para forzar batch aunque no esté lleno
        """
        Inicializa el capturador de ADB.
        
        Args:
            batch_size: Tamaño del lote para procesamiento (más pequeño = más reactivo)
            max_buffer_size: Tamaño máximo del buffer interno (deque)
            batch_timeout: Segundos máximos para acumular un batch antes de procesarlo
        """
        self.batch_size = batch_size
        self.max_buffer_size = max_buffer_size
        self.batch_timeout = batch_timeout
        
        # Session ID único para esta ejecución
        self.session_id = str(uuid.uuid4())
        self.session_start_time = datetime.now()
        
        self.is_running = False
        self._lock = threading.Lock()  # Para operaciones thread-safe
        
        # Usamos deque con maxlen para autolimpieza automática
        # Cuando llegue a maxlen, los más antiguos se eliminan solos
        self._data_buffer: Deque[Dict[str, Any]] = deque(maxlen=max_buffer_size)
        
        # Cola para procesamiento asíncrono
        self._process_queue = queue.Queue(maxsize=100)  # Límite para no acumular demasiado
        
        self.process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._processor_thread: Optional[threading.Thread] = None
        self._batch_timer: Optional[threading.Timer] = None
        
        # Estadísticas para monitoreo
        self.stats = {
            "total_lines_captured": 0,
            "total_batches_processed": 0,
            "dropped_batches": 0,
            "buffer_overflows": 0
        }
        
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Sesión iniciada con ID: {self.session_id}")
        
    def _start_logcat(self) -> None:
        """
        Lanza el proceso ADB y captura el flujo de logs con mejor manejo de errores.
        """
        try:
            # Limpiar buffer de logcat
            subprocess.run(
                ["adb", "logcat", "-c"],
                capture_output=True,
                timeout=5,
                check=False
            )
            
            # Comando para capturar logs de radio
            cmd = ["adb", "logcat", "-b", "radio", "-v", "time"]
            
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors='ignore',
                bufsize=1  # Line buffered
            )
            
            self.logger.info(f"Proceso ADB iniciado correctamente (Sesión: {self.session_id})")
            
            # Reiniciar el timer para el primer batch
            self._reset_batch_timer()
            #validacion extra
            if not self.process or not self.process.stdout:
                self.logger.error("No se pudo abrir stdout del proceso ADB")
                self.is_running = False
                return
            # Leer línea por línea con timeout
            for line in iter(self.process.stdout.readline, ''):
                if not self.is_running:
                    break
                    
                if line.strip():
                    self._prepare_dataset(line)
                    
        except subprocess.TimeoutExpired:
            self.logger.error("Timeout al limpiar buffer de logcat")
        except FileNotFoundError:
            self.logger.error("ADB no encontrado. ¿Está instalado y en el PATH?")
        except Exception as e:
            self.logger.error(f"Error en _start_logcat: {e}")
            self.is_running = False
    
    def _reset_batch_timer(self) -> None:
        """
        Reinicia el timer para forzar procesamiento de batch aunque no esté lleno.
        Esto evita que los logs se queden atrapados si el flujo es lento.
        """
        if self._batch_timer:
            self._batch_timer.cancel()
        
        if self.is_running:
            self._batch_timer = threading.Timer(self.batch_timeout, self._force_batch_flush)
            self._batch_timer.daemon = True
            self._batch_timer.start()
    
    def _force_batch_flush(self) -> None:
        """
        Fuerza el flush del buffer actual aunque no esté lleno.
        Esencial para no perder logs en flujos de baja intensidad.
        """
        with self._lock:
            if self._data_buffer and self.is_running:
                self.logger.debug(f"Forzando flush de batch con {len(self._data_buffer)} eventos")
                self._flush_to_queue()
        self._reset_batch_timer()
    
    def _prepare_dataset(self, line: str) -> None:
        """
        Prepara el dataset con timestamp local, session ID y manejo thread-safe.
        """
        entry = {
            "session_id": self.session_id,
            "session_start": self.session_start_time.isoformat(),
            "timestamp": datetime.now().isoformat(),
            "raw_payload": line.strip(),
            "source": "Snapdragon_Modem",
            "adb_timestamp": self._extract_log_timestamp(line),
            "sequence_number": self.stats["total_lines_captured"]  # Para trazabilidad
        }
        
        with self._lock:
            # El deque automáticamente elimina los más antiguos si supera maxlen
            self._data_buffer.append(entry)
            self.stats["total_lines_captured"] += 1
            
            # Verificar si hubo overflow (el deque eliminó datos viejos)
            if len(self._data_buffer) == self.max_buffer_size:
                self.stats["buffer_overflows"] += 1
                self.logger.warning(f"Buffer lleno! Se eliminaron logs antiguos. Total overflows: {self.stats['buffer_overflows']}")
            
            # Batching: procesar cuando alcanzamos el tamaño óptimo
            if len(self._data_buffer) >= self.batch_size:
                self._flush_to_queue()
    
    def _extract_log_timestamp(self, line: str) -> Optional[str]:
        """
        Extrae el timestamp original del log de ADB si está disponible.
        Formato típico: "01-15 10:30:45.123"
        """
        try:
            # El formato de ADB con -v time es: "MM-DD HH:MM:SS.mmm"
            parts = line.split()
            if len(parts) >= 2:
                timestamp_part = parts[0] + " " + parts[1]
                # Validar formato básico
                if "-" in timestamp_part and ":" in timestamp_part:
                    return timestamp_part
        except Exception:
            pass
        return None
    
    def _flush_to_queue(self) -> None:
        """
        Envía el bloque de datos a la cola de procesamiento de manera thread-safe.
        Ahora convierte el deque a lista para procesamiento.
        """
        if not self._data_buffer:
            return
            
        try:
            # Convertir deque a lista (los datos más recientes)
            # Si queremos procesar en orden FIFO, convertimos toda la lista
            batch = list(self._data_buffer)
            
            # Limpiar el buffer después de tomar la copia
            self._data_buffer.clear()
            
            # Intentar poner en cola con timeout
            self._process_queue.put(batch, timeout=2)
            self.stats["total_batches_processed"] += 1
            
            self.logger.debug(f"Lote de {len(batch)} eventos encolado (Batch #{self.stats['total_batches_processed']})")
            
        except queue.Full:
            self.stats["dropped_batches"] += 1
            self.logger.warning(f"Cola de procesamiento llena! Lote perdido. Total perdidos: {self.stats['dropped_batches']}")
            # Guardar emergencia
            self._save_emergency_batch(batch)
    
    def _save_emergency_batch(self, batch: List[Dict]) -> None:
        """
        Guarda lote de emergencia cuando la cola está llena.
        Incluye session_id en el nombre del archivo.
        """
        try:
            filename = f"emergency_log_{self.session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w') as f:
                json.dump({
                    "session_id": self.session_id,
                    "timestamp": datetime.now().isoformat(),
                    "batch_size": len(batch),
                    "data": batch
                }, f, indent=2)
            self.logger.info(f"Lote de emergencia guardado en {filename}")
        except Exception as e:
            self.logger.error(f"Error guardando lote de emergencia: {e}")
    
    def _process_batches(self) -> None:
        """
        Procesa los lotes de datos de manera asíncrona.
        """
        while self.is_running:
            try:
                # Esperar por lotes con timeout
                batch = self._process_queue.get(timeout=1)
                
                # Procesar el lote
                self._process_batch(batch)
                
                self._process_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"Error procesando lote: {e}")
    
    def _process_batch(self, batch: List[Dict]) -> None:
        """
        Procesa un lote de datos con información de sesión.
        Sobrescribe este método para tu lógica específica.
        """
        if not batch:
            return
            
        # Obtener información de la sesión del primer elemento (todos deberían tener la misma)
        session_info = {
            "session_id": batch[0]["session_id"],
            "session_start": batch[0]["session_start"],
            "batch_size": len(batch),
            "batch_timestamp": datetime.now().isoformat(),
            "batch_number": self.stats["total_batches_processed"]
        }
        
        self.logger.info(f"Procesando lote #{session_info['batch_number']} "
                        f"(Sesión: {session_info['session_id'][:8]}...) "
                        f"con {len(batch)} eventos")
        
        # Guardar en archivo con session_id y fecha
        try:
            filename = f"logs_{self.session_id}_{datetime.now().strftime('%Y%m%d')}.json"
            
            # Escribir cada entrada como línea JSON separada (formato JSONL para análisis posterior)
            with open(filename, 'a') as f:
                for entry in batch:
                    # Añadir metadata del batch al entry
                    entry['batch_number'] = session_info['batch_number']
                    f.write(json.dumps(entry) + '\n')
                    
        except Exception as e:
            self.logger.error(f"Error guardando lote: {e}")
    
    def start(self) -> bool:
        """
        Inicia la captura de logs.
        
        Returns:
            bool: True si se inició correctamente
        """
        if self.is_running:
            self.logger.warning("Ya está en ejecución")
            return False
            
        self.is_running = True
        
        # Hilo principal de captura
        self._thread = threading.Thread(
            target=self._start_logcat,
            name="ADBLogcatCapture",
            daemon=True
        )
        self._thread.start()
        
        # Hilo de procesamiento de lotes
        self._processor_thread = threading.Thread(
            target=self._process_batches,
            name="BatchProcessor",
            daemon=True
        )
        self._processor_thread.start()
        
        self.logger.info(f"Sistema de captura iniciado - Sesión: {self.session_id}")
        return True
    
    def stop(self, timeout: int = 5) -> None:
        """
        Detiene la captura de logs de manera limpia.
        """
        self.logger.info(f"Deteniendo captura (Sesión: {self.session_id})...")
        self.is_running = False
        
        # Cancelar timer pendiente
        if self._batch_timer:
            self._batch_timer.cancel()
        
        # Detener proceso ADB
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=timeout)
                self.logger.info("Proceso ADB terminado")
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.logger.warning("Proceso ADB forzado a terminar")
        
        # Forzar flush final de datos pendientes
        with self._lock:
            if self._data_buffer:
                self._flush_to_queue()
        
        # Esperar a que se procesen los últimos lotes
        if self._processor_thread and self._processor_thread.is_alive():
            try:
                self._process_queue.join()
                self._processor_thread.join(timeout=timeout)
            except Exception as e:
                self.logger.error(f"Error en shutdown: {e}")
        
        self.logger.info(f"Captura detenida - Estadísticas finales: {self.get_stats()}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Obtiene estadísticas detalladas del sistema.
        """
        with self._lock:
            return {
                "session_id": self.session_id,
                "session_duration": (datetime.now() - self.session_start_time).total_seconds(),
                "is_running": self.is_running,
                "buffer_usage": len(self._data_buffer),
                "buffer_max_size": self.max_buffer_size,
                "queue_size": self._process_queue.qsize(),
                "batch_size": self.batch_size,
                "batch_timeout": self.batch_timeout,
                "total_lines_captured": self.stats["total_lines_captured"],
                "total_batches_processed": self.stats["total_batches_processed"],
                "dropped_batches": self.stats["dropped_batches"],
                "buffer_overflows": self.stats["buffer_overflows"],
                "efficiency": self._calculate_efficiency()
            }
    
    def _calculate_efficiency(self) -> float:
        """
        Calcula eficiencia del sistema (líneas procesadas vs capturadas).
        """
        if self.stats["total_lines_captured"] == 0:
            return 100.0
        
        total_processed = self.stats["total_batches_processed"] * self.batch_size
        # Estimación aproximada
        return min(100.0, (total_processed / self.stats["total_lines_captured"]) * 100)

# Manejador de señales mejorado
class GracefulKiller:
    """Manejador de señales para cierre graceful con estadísticas finales"""
    
    def __init__(self, adb_input: ADBInput):
        self.adb_input = adb_input
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)
    
    def exit_gracefully(self, signum, frame):
        print("\n" + "="*60)
        print("Recibida señal de interrupción. Generando reporte final...")
        
        # Mostrar estadísticas finales
        stats = self.adb_input.get_stats()
        print("\n--- ESTADÍSTICAS FINALES DE SESIÓN ---")
        print(f"Sesión ID: {stats['session_id']}")
        print(f"Duración: {stats['session_duration']:.2f} segundos")
        print(f"Líneas capturadas: {stats['total_lines_captured']}")
        print(f"Lotes procesados: {stats['total_batches_processed']}")
        print(f"Lotes perdidos: {stats['dropped_batches']}")
        print(f"Overflows de buffer: {stats['buffer_overflows']}")
        print(f"Eficiencia: {stats['efficiency']:.2f}%")
        print("="*60)
        
        self.adb_input.stop()
        sys.exit(0)

# Ejemplo de uso avanzado con monitoreo en tiempo real
class ADBMonitor:
    """
    Monitor en tiempo real para visualizar estadísticas
    """
    def __init__(self, adb_input: ADBInput):
        self.adb = adb_input
        self.running = False
    
    def start_monitoring(self, interval: int = 5):
        """
        Inicia monitoreo periódico
        """
        self.running = True
        while self.running and self.adb.is_running:
            stats = self.adb.get_stats()
            
            # Barra de progreso simple para uso de buffer
            buffer_percent = (stats['buffer_usage'] / stats['buffer_max_size']) * 100
            bar_length = 30
            filled = int(bar_length * buffer_percent / 100)
            bar = '█' * filled + '░' * (bar_length - filled)
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] "
                  f"Buffer: [{bar}] {buffer_percent:.1f}% | "
                  f"Capturados: {stats['total_lines_captured']} | "
                  f"Lotes: {stats['total_batches_processed']} | "
                  f"Drop: {stats['dropped_batches']}")
            
            time.sleep(interval)
    
    def stop_monitoring(self):
        self.running = False

if __name__ == "__main__":
    # Configuración optimizada para ataques de ruido
    # - batch_size más pequeño (20-30) para ser más reactivo
    # - buffer más grande (2000) para aguantar picos
    # - timeout más bajo (1s) para no dejar logs atrapados
    adb = ADBInput(
        batch_size=30,          # Más pequeño = más reactivo ante ruido
        max_buffer_size=2000,    # Buffer grande para aguantar picos
        batch_timeout=1.0        # Forzar flush cada segundo
    )
    
    # Configurar manejador de señales
    killer = GracefulKiller(adb)
    
    # Iniciar captura
    if adb.start():
        print(f"\n{'='*60}")
        print(f"✅ CAPTURA INICIADA")
        print(f"📱 Sesión ID: {adb.session_id}")
        print(f"📊 Batch size: {adb.batch_size} líneas")
        print(f"⏱️  Batch timeout: {adb.batch_timeout}s")
        print(f"💾 Buffer max: {adb.max_buffer_size} líneas")
        print(f"{'='*60}\n")
        
        # Iniciar monitor en hilo separado
        monitor = ADBMonitor(adb)
        monitor_thread = threading.Thread(
            target=monitor.start_monitoring,
            args=(3,),  # Actualizar cada 3 segundos
            daemon=True
        )
        monitor_thread.start()
        
        try:
            # Mantener el programa vivo
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            monitor.stop_monitoring()
            adb.stop()
    else:
        print("❌ Error al iniciar captura")