"""
Pipeline Module - Orquestador principal del sistema de monitoreo de señal.
Coordina la captura de logs, procesamiento de datos y notificación a observadores.

Arquitectura:
    ADBInput → SignalLogic → Observers (AlarmSystem, DatabaseModule, TUI, etc.)
    
Características:
- Patrón Observer con prioridades (CRITICAL, HIGH, NORMAL, LOW, DEBUG)
- Inicialización lazy de componentes
- Procesamiento asíncrono con locks thread-safe
- Estadísticas detalladas de rendimiento
- Observadores integrados para alarmas, persistencia y monitoreo
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
import logging
import threading
import time
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from collections import deque

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# ============================================================================
# CLASES BASE
# ============================================================================

class ObserverPriority(Enum):
    """
    Prioridades para los observadores.
    Los observadores con prioridad más alta (CRITICAL) se notifican primero.
    """
    CRITICAL = 0   # Alarmas de seguridad, eventos críticos
    HIGH = 1       # Persistencia crítica, logging importante
    NORMAL = 2     # Procesamiento estándar
    LOW = 3        # UI, métricas, monitoreo
    DEBUG = 4      # Debugging, desarrollo


@dataclass
class PipelineStats:
    """
    Estadísticas de rendimiento del pipeline.
    Utilizado para monitoreo y debugging.
    """
    total_batches: int = 0
    total_events: int = 0
    processing_times: deque = field(default_factory=lambda: deque(maxlen=100))
    errors_count: int = 0
    last_error: Optional[str] = None
    last_processing_time: float = 0.0
    
    @property
    def avg_processing_time(self) -> float:
        """Tiempo promedio de procesamiento en segundos."""
        if not self.processing_times:
            return 0.0
        return sum(self.processing_times) / len(self.processing_times)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario para logging."""
        return {
            "total_batches": self.total_batches,
            "total_events": self.total_events,
            "avg_processing_ms": self.avg_processing_time * 1000,
            "errors_count": self.errors_count,
            "last_error": self.last_error
        }


class SignalObserver(ABC):
    """
    Clase base para observadores de señal.
    Todos los observadores deben implementar update().
    """
    
    def __init__(self, name: Optional[str] = None, priority: ObserverPriority = ObserverPriority.NORMAL):
        """
        Args:
            name: Nombre identificador del observador
            priority: Prioridad de ejecución
        """
        self.name = name or self.__class__.__name__
        self.priority = priority
        self.logger = logging.getLogger(f"Observer.{self.name}")
        self.is_active = True
        self.error_count = 0
    
    @abstractmethod
    def update(self, refined_data: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
        """
        Método llamado cuando hay nuevos datos procesados.
        
        Args:
            refined_data: Lista de entradas procesadas con métricas
            summary: Resumen estadístico del lote
        """
        pass
    
    def on_error(self, error: Exception) -> None:
        """
        Callback cuando ocurre un error en el observador.
        Por defecto desactiva después de 10 errores consecutivos.
        """
        self.error_count += 1
        self.logger.error(f"Error en observador: {error}")
        if self.error_count > 10:
            self.logger.critical(f"Demasiados errores ({self.error_count}) en {self.name}, desactivando...")
            self.is_active = False
    
    def on_attach(self, pipeline: 'SignalPipeline') -> None:
        """Callback cuando el observador es añadido al pipeline."""
        self.logger.info(f"Observador {self.name} conectado al pipeline")
    
    def on_detach(self) -> None:
        """Callback cuando el observador es removido del pipeline."""
        self.logger.info(f"Observador {self.name} desconectado")


# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

class SignalPipeline:
    """
    Pipeline principal de procesamiento de señales.
    Orquesta la captura, procesamiento y distribución de datos.
    
    Flujo:
        1. ADBInput captura logs → batch
        2. SignalLogic procesa → métricas
        3. Notifica a observadores por prioridad
        4. Estadísticas y monitoreo
    """
    
    def __init__(self, 
                 batch_size: int = 30,
                 batch_timeout: float = 1.0,
                 max_queue_size: int = 100,
                 enable_stats: bool = True,
                 auto_subscribe_defaults: bool = True):
        """
        Inicializa el pipeline.
        
        Args:
            batch_size: Tamaño de lote para ADBInput
            batch_timeout: Timeout para forzar flush
            max_queue_size: Tamaño máximo de cola interna
            enable_stats: Habilitar estadísticas
            auto_subscribe_defaults: Suscribir observadores por defecto
        """
        self.logger = logging.getLogger("SignalPipeline")
        
        # Configuración
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.enable_stats = enable_stats
        self.max_queue_size = max_queue_size
        
        # Observadores organizados por prioridad
        self._observers = {
            ObserverPriority.CRITICAL: [],
            ObserverPriority.HIGH: [],
            ObserverPriority.NORMAL: [],
            ObserverPriority.LOW: [],
            ObserverPriority.DEBUG: []
        }
        self._observers_lock = threading.RLock()
        
        # Componentes principales (inicialización lazy)
        self.input_node = None
        self.logic_engine = None
        self._components_initialized = False
        
        # Estado y estadísticas
        self._is_running = False
        self.stats = PipelineStats() if enable_stats else None
        self._processing_lock = threading.Lock()
        self._monitor_thread = None
        
        # Callback personalizado para procesamiento (opcional)
        self._custom_process_callback: Optional[Callable] = None
        
        # Suscribir observadores por defecto
        if auto_subscribe_defaults:
            self._subscribe_default_observers()
        
        self.logger.info("SignalPipeline inicializado")
    
    def _subscribe_default_observers(self) -> None:
        """Suscribe los observadores por defecto."""
        # Estos observadores se crean solo cuando se necesitan
        self._default_observers = {
            "alarm": None,
            "database": None,
            "monitor": None
        }
    
    def _initialize_components(self) -> bool:
        """
        Inicialización lazy de componentes.
        Los componentes se cargan solo cuando se inicia el pipeline.
        
        Returns:
            bool: True si se inicializaron correctamente
        """
        if self._components_initialized:
            return True
        
        # Inicializar input_node
        if self.input_node is None:
            try:
                # Ajustar ruta de importación según estructura
                sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from Inputs.ADB_input import ADBInput
                
                self.input_node = ADBInput(
                    batch_size=self.batch_size,
                    batch_timeout=self.batch_timeout
                )
                
                if self.input_node is None:
                    raise RuntimeError("ADBInput devolvió None")
                
                # Usar callback en lugar de sobrescribir método
                self.input_node.set_batch_callback(self._orchestrate_processing)
                
                self.logger.info("ADBInput inicializado correctamente")
                
            except ImportError as e:
                self.logger.error(f"No se pudo importar ADBInput: {e}")
                self.logger.error("Verifica que Inputs/ADB_input.py existe y es accesible")
                self.input_node = None
                return False
            except Exception as e:
                self.logger.error(f"Error creando ADBInput: {e}")
                self.input_node = None
                return False
        
        # Inicializar logic_engine
        if self.logic_engine is None:
            try:
                from SRC.Logic import SignalLogic
                
                self.logic_engine = SignalLogic(enable_stats=self.enable_stats)
                
                if self.logic_engine is None:
                    raise RuntimeError("SignalLogic devolvió None")
                
                self.logger.info("SignalLogic inicializado correctamente")
                
            except ImportError as e:
                self.logger.error(f"No se pudo importar SignalLogic: {e}")
                self.logger.error("Verifica que SRC/Logic.py existe y es accesible")
                self.logic_engine = None
                return False
            except Exception as e:
                self.logger.error(f"Error creando SignalLogic: {e}")
                self.logic_engine = None
                return False
        
        self._components_initialized = True
        return True
    
    # ========================================================================
    # Métodos Públicos - Control
    # ========================================================================
    
    def start(self) -> bool:
        """
        Inicia el pipeline completo.
        
        Returns:
            bool: True si se inició correctamente
        """
        if self._is_running:
            self.logger.warning("Pipeline ya está en ejecución")
            return False
        
        try:
            # Inicializar componentes
            if not self._initialize_components():
                self.logger.error("Fallo en inicialización de componentes")
                return False
            
            # Verificar input_node
            if self.input_node is None:
                self.logger.error("input_node es None después de inicialización")
                return False
            
            if not hasattr(self.input_node, 'start'):
                self.logger.error(f"input_node ({type(self.input_node)}) no tiene método start()")
                return False
            
            # Iniciar nodo de entrada
            self.logger.info("Intentando iniciar ADBInput...")
            start_result = self.input_node.start()
            
            if not start_result:
                self.logger.error("ADBInput.start() devolvió False")
                return False
            
            self._is_running = True
            
            # Iniciar thread de monitoreo
            if self.enable_stats:
                self._start_monitor_thread()
            
            self.logger.info("✅ Pipeline iniciado correctamente")
            return True
            
        except Exception as e:
            self.logger.error(f"Error iniciando pipeline: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return False
    
    def stop(self) -> None:
        """
        Detiene el pipeline de manera graceful.
        """
        self.logger.info("Deteniendo pipeline...")
        self._is_running = False
        
        # Detener monitor
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2)
        
        # Detener componentes
        if self.input_node and hasattr(self.input_node, 'stop'):
            try:
                self.input_node.stop()
                self.logger.info("ADBInput detenido")
            except Exception as e:
                self.logger.error(f"Error deteniendo ADBInput: {e}")
        
        # Mostrar estadísticas finales
        if self.enable_stats and self.stats:
            self.logger.info(f"Pipeline stats finales: {self.stats.to_dict()}")
        
        self.logger.info("✅ Pipeline detenido")
    
    # ========================================================================
    # Métodos Públicos - Gestión de Observadores
    # ========================================================================
    
    def subscribe(self, observer: SignalObserver) -> bool:
        """
        Suscribe un observador al pipeline.
        
        Args:
            observer: Observador a suscribir
            
        Returns:
            bool: True si se suscribió correctamente
        """
        with self._observers_lock:
            priority = observer.priority
            if observer not in self._observers[priority]:
                self._observers[priority].append(observer)
                observer.on_attach(self)
                self.logger.info(f"📌 Observador suscrito: {observer.name} (Prioridad: {priority.name})")
                return True
            else:
                self.logger.warning(f"Observador ya suscrito: {observer.name}")
                return False
    
    def unsubscribe(self, observer: SignalObserver) -> bool:
        """
        Desuscribe un observador del pipeline.
        
        Args:
            observer: Observador a desuscribir
            
        Returns:
            bool: True si se desuscribió correctamente
        """
        with self._observers_lock:
            priority = observer.priority
            if observer in self._observers[priority]:
                self._observers[priority].remove(observer)
                observer.on_detach()
                self.logger.info(f"📌 Observador desuscrito: {observer.name}")
                return True
            return False
    
    def set_custom_processor(self, callback: Callable[[List[Dict]], List[Dict]]) -> None:
        """
        Establece un procesador personalizado (reemplaza SignalLogic).
        
        Args:
            callback: Función que recibe batch y devuelve datos procesados
        """
        self._custom_process_callback = callback
        self.logger.info("Procesador personalizado establecido")
    
    # ========================================================================
    # Métodos Públicos - Información
    # ========================================================================
    
    def get_stats(self) -> Optional[Dict[str, Any]]:
        """Obtiene estadísticas actuales del pipeline."""
        if self.stats:
            return self.stats.to_dict()
        return None
    
    def get_observers_info(self) -> List[Dict[str, Any]]:
        """Obtiene información de los observadores suscritos."""
        with self._observers_lock:
            observers_info = []
            for priority, observers in self._observers.items():
                for observer in observers:
                    observers_info.append({
                        "name": observer.name,
                        "priority": priority.name,
                        "active": observer.is_active,
                        "errors": observer.error_count
                    })
            return observers_info
    
    def is_running(self) -> bool:
        """Verifica si el pipeline está en ejecución."""
        return self._is_running
    
    # ========================================================================
    # Métodos Internos - Procesamiento
    # ========================================================================
    
    def _orchestrate_processing(self, batch: List[Dict]) -> None:
        """
        Corazón del sistema: procesa el lote y notifica a observadores.
        
        Args:
            batch: Lote crudo del ADB input
        """
        if not batch:
            return
        
        # Evitar procesamiento concurrente
        if not self._processing_lock.acquire(blocking=False):
            self.logger.warning("Procesamiento anterior aún en curso, saltando lote...")
            return
        
        try:
            start_time = time.time()
            
            # Procesar batch (usar custom o logic_engine)
            if self._custom_process_callback:
                refined_data = self._custom_process_callback(batch)
            elif self.logic_engine:
                refined_data = self.logic_engine.process_batch(batch)
            else:
                refined_data = []
            
            if refined_data:
                # Generar resumen
                if self.logic_engine:
                    summary = self.logic_engine.get_summary(refined_data)
                else:
                    summary = self._generate_basic_summary(refined_data)
                
                # Notificar a observadores
                self._notify_all(refined_data, summary)
                
                # Actualizar estadísticas
                if self.enable_stats and self.stats:
                    processing_time = time.time() - start_time
                    self.stats.total_batches += 1
                    self.stats.total_events += len(refined_data)
                    self.stats.processing_times.append(processing_time)
                    self.stats.last_processing_time = processing_time
                    
                    if self.stats.total_batches % 10 == 0:
                        self.logger.info(f"Pipeline stats: {self.stats.to_dict()}")
            
        except Exception as e:
            self.logger.error(f"Error en procesamiento: {e}", exc_info=True)
            if self.enable_stats and self.stats:
                self.stats.errors_count += 1
                self.stats.last_error = str(e)
        
        finally:
            self._processing_lock.release()
    
    def _generate_basic_summary(self, refined_data: List[Dict]) -> Dict[str, Any]:
        """
        Genera un resumen básico cuando no hay logic_engine.
        
        Args:
            refined_data: Datos procesados
            
        Returns:
            Resumen básico
        """
        if not refined_data:
            return {"status": "empty", "total_events": 0}
        
        return {
            "status": "success",
            "total_events": len(refined_data),
            "timestamp": refined_data[-1].get("timestamp"),
            "avg_quality": 0,
            "common_rat": "Unknown"
        }
    
    def _notify_all(self, data: List[Dict], summary: Dict) -> None:
        """
        Notifica a todos los observadores en orden de prioridad.
        Los errores en un observador no afectan a los demás.
        
        Args:
            data: Datos procesados
            summary: Resumen del lote
        """
        with self._observers_lock:
            # Notificar en orden de prioridad
            for priority in [ObserverPriority.CRITICAL, ObserverPriority.HIGH,
                            ObserverPriority.NORMAL, ObserverPriority.LOW,
                            ObserverPriority.DEBUG]:
                for observer in self._observers[priority]:
                    if not observer.is_active:
                        continue
                    
                    try:
                        start_time = time.time()
                        observer.update(data, summary)
                        elapsed = time.time() - start_time
                        
                        if elapsed > 1.0:
                            self.logger.warning(f"Observador lento {observer.name}: {elapsed:.2f}s")
                            
                    except Exception as e:
                        self.logger.error(f"Error notificando a {observer.name}: {e}")
                        observer.on_error(e)
    
    def _start_monitor_thread(self) -> None:
        """Inicia thread de monitoreo para estadísticas."""
        def monitor():
            while self._is_running:
                time.sleep(30)
                if self.stats and self.stats.total_batches > 0:
                    self.logger.debug(f"Monitoreo: {self.stats.to_dict()}")
        
        self._monitor_thread = threading.Thread(target=monitor, daemon=True, name="PipelineMonitor")
        self._monitor_thread.start()


# ============================================================================
# OBSERVADORES INTEGRADOS
# ============================================================================

class AlarmSystem(SignalObserver):
    """
    Sistema de alarmas para eventos críticos.
    Detecta problemas de seguridad, señal débil y calidad pobre.
    """
    
    def __init__(self, name: str = "AlarmSystem", cooldown: int = 30, **kwargs):
        """
        Args:
            name: Nombre del observador
            cooldown: Segundos entre alertas del mismo tipo
        """
        super().__init__(name=name, priority=ObserverPriority.CRITICAL, **kwargs)
        self.last_alert_time = {}
        self.alert_cooldown = cooldown
        self._last_rat = None
    
    def _should_alert(self, alert_type: str) -> bool:
        """Control de cooldown para evitar spam."""
        now = time.time()
        if alert_type in self.last_alert_time:
            if now - self.last_alert_time[alert_type] < self.alert_cooldown:
                return False
        self.last_alert_time[alert_type] = now
        return True
    
    def update(self, refined_data: List[Dict], summary: Dict):
        """Procesa alertas según el resumen."""
        
        # Alerta de seguridad (cifrado desactivado)
        security_issues = summary.get("security_issues", 0)
        if security_issues > 0:
            if self._should_alert("security"):
                self.logger.warning(f"🚨 ALERTA DE SEGURIDAD: {security_issues} eventos sin cifrado")
                print("\n" + "="*60)
                print("🚨 [ALERTA CRÍTICA] ¡Posible ataque de celda falsa detectado!")
                print(f"   Eventos sin cifrado: {security_issues}")
                print("="*60)
        
        # Alerta de calidad de señal
        avg_quality = summary.get("avg_quality", 100)
        if avg_quality < 30:
            if self._should_alert("critical_instability"):
                self.logger.critical(f"⚠️ SEÑAL CRÍTICA: Calidad {avg_quality:.1f}%")
                print(f"\n🔴 [ALERTA CRÍTICA] Señal extremadamente débil: {avg_quality:.1f}%")
        elif avg_quality < 40:
            if self._should_alert("instability"):
                self.logger.warning(f"⚠️ SEÑAL INESTABLE: Calidad {avg_quality:.1f}%")
                print(f"\n🟡 [ADVERTENCIA] Señal inestable: {avg_quality:.1f}%")
        
        # Alerta de cambio de tecnología
        common_rat = summary.get("common_rat")
        if common_rat and self._last_rat is not None and self._last_rat != common_rat:
            self.logger.info(f"🔄 Cambio de tecnología: {self._last_rat} → {common_rat}")
            print(f"\n🔄 Handover detectado: {self._last_rat} → {common_rat}")
        self._last_rat = common_rat


class DatabaseModule(SignalObserver):
    """
    Módulo de persistencia con buffer y escritura asíncrona.
    Guarda los datos procesados en archivo JSONL.
    """
    
    def __init__(self, db_path: str = "signal_data.jsonl", flush_size: int = 50, 
                 flush_timeout: float = 5.0, **kwargs):
        """
        Args:
            db_path: Ruta del archivo de salida
            flush_size: Tamaño de buffer para escritura
            flush_timeout: Timeout para flush automático
        """
        super().__init__(name="DatabaseModule", priority=ObserverPriority.HIGH, **kwargs)
        self.db_path = db_path
        self.flush_size = flush_size
        self.flush_timeout = flush_timeout
        
        self.write_buffer = []
        self.buffer_lock = threading.Lock()
        self.last_write = time.time()
        
        self.logger.info(f"DatabaseModule: guardando en {db_path}")
    
    def update(self, refined_data: List[Dict], summary: Dict):
        """Acumula datos en buffer y escribe periódicamente."""
        if not refined_data:
            return
        
        with self.buffer_lock:
            self.write_buffer.extend(refined_data)
            
            # Escribir si buffer está lleno o ha pasado el timeout
            if len(self.write_buffer) >= self.flush_size or \
               (time.time() - self.last_write) >= self.flush_timeout:
                self._flush_buffer()
    
    def _flush_buffer(self):
        """Escribe el buffer al archivo."""
        if not self.write_buffer:
            return
        
        try:
            import json
            
            with open(self.db_path, 'a', encoding='utf-8') as f:
                for entry in self.write_buffer:
                    # Añadir timestamp de escritura
                    entry['write_timestamp'] = datetime.now().isoformat()
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            
            self.logger.debug(f"Escritos {len(self.write_buffer)} eventos a {self.db_path}")
            self.write_buffer.clear()
            self.last_write = time.time()
            
        except Exception as e:
            self.logger.error(f"Error escribiendo a DB: {e}")
            raise
    
    def on_detach(self):
        """Flush final al desconectar."""
        self._flush_buffer()
        super().on_detach()


class SignalMonitor(SignalObserver):
    """
    Monitor simple en consola para visualización en tiempo real.
    Muestra barra de calidad, señal y red.
    """
    
    def __init__(self, use_print: bool = True, **kwargs):
        """
        Args:
            use_print: Usar print() en lugar de logger (para TUI)
        """
        super().__init__(name="SignalMonitor", priority=ObserverPriority.LOW, **kwargs)
        self.last_quality = 0
        self.use_print = use_print
        self._last_display_time = 0
    
    def update(self, refined_data: List[Dict], summary: Dict):
        """Actualiza display en consola."""
        quality = summary.get("avg_quality", 0)
        
        # Solo mostrar cambios significativos o cada cierto tiempo
        if abs(quality - self.last_quality) > 5:
            self._update_display(quality, summary)
            self.last_quality = quality
    
    def _update_display(self, quality: float, summary: Dict):
        """Actualiza el display."""
        # Barra de calidad
        bar_length = 30
        filled = int(bar_length * quality / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        # Color según calidad
        if quality >= 70:
            color = "🟢"
        elif quality >= 40:
            color = "🟡"
        else:
            color = "🔴"
        
        signal = summary.get('avg_signal', 'N/A')
        rat = summary.get('common_rat', 'N/A')
        
        if self.use_print:
            print(f"\r{color} Calidad: [{bar}] {quality:.1f}% | "
                  f"Señal: {signal} dBm | "
                  f"Red: {rat}", end="", flush=True)
        else:
            self.logger.info(f"Monitor: {quality:.1f}% | {signal} dBm | {rat}")


# ============================================================================
# PUNTO DE ENTRADA (PRUEBAS)
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("   🔧 SIGNAL PIPELINE - Prueba de Integración")
    print("="*60)
    
    # Crear pipeline
    pipeline = SignalPipeline(
        batch_size=30,
        batch_timeout=1.0,
        enable_stats=True,
        auto_subscribe_defaults=False
    )
    
    # Suscribir observadores
    pipeline.subscribe(AlarmSystem(cooldown=10))
    pipeline.subscribe(DatabaseModule(db_path="test_signal.jsonl"))
    pipeline.subscribe(SignalMonitor())
    
    print("\n📌 Observadores suscritos:")
    for obs in pipeline.get_observers_info():
        print(f"   - {obs['name']} (Prioridad: {obs['priority']})")
    
    try:
        if pipeline.start():
            print("\n✅ Pipeline iniciado. Presiona Ctrl+C para detener...\n")
            while True:
                time.sleep(1)
        else:
            print("❌ Error al iniciar pipeline")
            
    except KeyboardInterrupt:
        print("\n\n🛑 Deteniendo sistema...")
    finally:
        pipeline.stop()
        print("\n✅ Sistema detenido correctamente")