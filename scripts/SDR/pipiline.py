from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from collections import deque

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class ObserverPriority(Enum):
    """Prioridades para los observadores"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    DEBUG = 4

@dataclass
class PipelineStats:
    """Estadísticas del pipeline"""
    total_batches: int = 0
    total_events: int = 0
    processing_times: deque = field(default_factory=lambda: deque(maxlen=100))
    errors_count: int = 0
    last_error: Optional[str] = None
    last_processing_time: float = 0.0
    
    @property
    def avg_processing_time(self) -> float:
        if not self.processing_times:
            return 0.0
        return sum(self.processing_times) / len(self.processing_times)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_batches": self.total_batches,
            "total_events": self.total_events,
            "avg_processing_ms": self.avg_processing_time * 1000,
            "errors_count": self.errors_count,
            "last_error": self.last_error
        }


class SignalObserver(ABC):
    """Clase base para observadores de señal"""
    
    def __init__(self, name: Optional[str] = None, priority: ObserverPriority = ObserverPriority.NORMAL):
        self.name = name or self.__class__.__name__
        self.priority = priority
        self.logger = logging.getLogger(f"Observer.{self.name}")
        self.is_active = True
        self.error_count = 0
    
    @abstractmethod
    def update(self, refined_data: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
        pass
    
    def on_error(self, error: Exception) -> None:
        self.error_count += 1
        self.logger.error(f"Error en observador: {error}")
        if self.error_count > 10:
            self.logger.critical(f"Demasiados errores ({self.error_count}) en {self.name}, desactivando...")
            self.is_active = False
    
    def on_attach(self, pipeline: 'SignalPipeline') -> None:
        self.logger.info(f"Observador {self.name} conectado al pipeline")
    
    def on_detach(self) -> None:
        self.logger.info(f"Observador {self.name} desconectado")

class SignalPipeline:
    """Pipeline principal de procesamiento de señales"""
    
    def __init__(self, 
                 batch_size: int = 30,
                 batch_timeout: float = 1.0,
                 max_queue_size: int = 100,
                 enable_stats: bool = True):
        
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
        
        # Componentes principales - INICIALIZAR COMO None
        self.input_node = None
        self.logic_engine = None
        
        # Estado y estadísticas
        self._is_running = False
        self.stats = PipelineStats() if enable_stats else None
        self._processing_lock = threading.Lock()
        self._monitor_thread = None
        
        self.logger.info("SignalPipeline inicializado")
    
    def _initialize_components(self):
        """Inicialización lazy de componentes con mejor manejo de errores"""
        
        # Inicializar input_node
        if self.input_node is None:
            try:
                from Inputs.ADB_input import ADBInput
                
                self.input_node = ADBInput(
                    batch_size=self.batch_size,
                    batch_timeout=self.batch_timeout
                )
                
                # Verificar que se creó correctamente
                if self.input_node is None:
                    raise RuntimeError("ADBInput devolvió None")
                
                # Inyectar nuestro método de procesamiento
                self.input_node._process_batch = lambda batch: self._orchestrate_processing(batch)
                
                self.logger.info("ADBInput inicializado correctamente")
                
            except ImportError as e:
                self.logger.error(f"No se pudo importar ADBInput: {e}")
                self.input_node = None
                raise
            except Exception as e:
                self.logger.error(f"Error creando ADBInput: {e}")
                self.input_node = None
                raise
        
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
                self.logic_engine = None
                raise
            except Exception as e:
                self.logger.error(f"Error creando SignalLogic: {e}")
                self.logic_engine = None
                raise
    
    def start_pipeline(self) -> bool:
        """
        Inicia el pipeline completo.
        
        Returns:
            bool: True si se inició correctamente
        """
        if self._is_running:
            self.logger.warning("Pipeline ya está en ejecución")
            return False
        
        try:
            # Asegurarnos de que los componentes estén inicializados
            self._initialize_components()
            
            # CORRECCIÓN: Verificar que input_node NO sea None
            if self.input_node is None:
                self.logger.error("input_node es None después de inicialización")
                return False
            
            # CORRECCIÓN: Verificar que input_node tenga el método start
            if not hasattr(self.input_node, 'start'):
                self.logger.error(f"input_node ({type(self.input_node)}) no tiene método start()")
                return False
            
            # Intentar iniciar el nodo de entrada
            self.logger.info("Intentando iniciar ADBInput...")
            start_result = self.input_node.start()
            
            if not start_result:
                self.logger.error("ADBInput.start() devolvió False")
                return False
            
            self._is_running = True
            
            # Iniciar thread de monitoreo si hay estadísticas
            if self.enable_stats:
                self._start_monitor_thread()
            
            self.logger.info("✅ Pipeline iniciado correctamente")
            return True
            
        except ImportError as e:
            self.logger.error(f"Error de importación: {e}")
            self.logger.error("Verifica que los módulos existen en las rutas correctas:")
            self.logger.error("  - Inputs/ADB_input.py")
            self.logger.error("  - SRC/Logic.py")
            return False
        except Exception as e:
            self.logger.error(f"Error iniciando pipeline: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
            return False
    
    def stop_pipeline(self) -> None:
        """Detiene el pipeline de manera graceful."""
        self.logger.info("Deteniendo pipeline...")
        self._is_running = False
        
        # Detener monitor
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2)
        
        # Detener componentes (solo si existen)
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
    
    def start(self) -> bool:
        """Alias para start_pipeline() - mantiene compatibilidad"""
        return self.start_pipeline()
    
    def stop(self) -> None:
        """Alias para stop_pipeline() - mantiene compatibilidad"""
        self.stop_pipeline()
    
    def _orchestrate_processing(self, batch: List[Dict]) -> None:
        """Procesa el lote y notifica a observadores."""
        if not batch:
            return
        
        if not self._processing_lock.acquire(blocking=False):
            self.logger.warning("Procesamiento anterior aún en curso, saltando lote...")
            return
        
        try:
            start_time = time.time()
            
            if self.logic_engine:
                refined_data = self.logic_engine.process_batch(batch)
            else:
                refined_data = []
            
            if refined_data:
                summary = self.logic_engine.get_summary(refined_data) if self.logic_engine else {}
                self._notify_all(refined_data, summary)
                
                if self.enable_stats and self.stats:
                    processing_time = time.time() - start_time
                    self.stats.total_batches += 1
                    self.stats.total_events += len(refined_data)
                    self.stats.processing_times.append(processing_time)
                    self.stats.last_processing_time = processing_time
                    
                    if self.stats.total_batches % 10 == 0:
                        self.logger.info(f"Pipeline stats: {self.stats.to_dict()}")
            
        except Exception as e:
            self.logger.error(f"Error en procesamiento: {e}")
            if self.enable_stats and self.stats:
                self.stats.errors_count += 1
                self.stats.last_error = str(e)
        
        finally:
            self._processing_lock.release()
    
    def _notify_all(self, data: List[Dict], summary: Dict) -> None:
        """Notifica a todos los observadores."""
        with self._observers_lock:
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
        """Inicia thread de monitoreo"""
        def monitor():
            while self._is_running:
                time.sleep(30)
                if self.stats and self.stats.total_batches > 0:
                    self.logger.debug(f"Monitoreo: {self.stats.to_dict()}")
        
        self._monitor_thread = threading.Thread(target=monitor, daemon=True, name="PipelineMonitor")
        self._monitor_thread.start()
    
    def subscribe(self, observer: SignalObserver) -> bool:
        """Suscribe un observador al pipeline"""
        with self._observers_lock:
            priority = observer.priority
            if observer not in self._observers[priority]:
                self._observers[priority].append(observer)
                observer.on_attach(self)
                self.logger.info(f"Observador suscrito: {observer.name} (Prioridad: {priority.name})")
                return True
            return False
    
    def get_stats(self) -> Optional[Dict[str, Any]]:
        """Obtiene estadísticas actuales"""
        if self.stats:
            return self.stats.to_dict()
        return None
class AlarmSystem(SignalObserver):
    def __init__(self, name: str = "AlarmSystem", **kwargs):
        super().__init__(name=name, priority=ObserverPriority.CRITICAL, **kwargs)
        self.last_alert_time = {}
        self.alert_cooldown = 30
        self._last_rat = None
    
    def _should_alert(self, alert_type: str) -> bool:
        now = time.time()
        if alert_type in self.last_alert_time:
            if now - self.last_alert_time[alert_type] < self.alert_cooldown:
                return False
        self.last_alert_time[alert_type] = now
        return True
    
    def update(self, refined_data: List[Dict], summary: Dict):
        if summary.get("security_issues", 0) > 0:
            if self._should_alert("security"):
                self.logger.warning("🚨 ALERTA DE SEGURIDAD")
                print("\n" + "="*60)
                print("🚨 [ALERTA CRÍTICA] ¡Posible ataque de celda falsa detectado!")
                print("="*60)
        
        avg_quality = summary.get("avg_quality", 100)
        if avg_quality < 30:
            if self._should_alert("critical_instability"):
                self.logger.critical(f"⚠️ SEÑAL CRÍTICA: {avg_quality:.1f}%")
                print(f"\n🔴 [ALERTA] Señal extremadamente débil: {avg_quality:.1f}%")
        elif avg_quality < 40:
            if self._should_alert("instability"):
                self.logger.warning(f"⚠️ SEÑAL INESTABLE: {avg_quality:.1f}%")
                print(f"\n🟡 [ADVERTENCIA] Señal inestable: {avg_quality:.1f}%")


class DatabaseModule(SignalObserver):
    def __init__(self, db_path: str = "signal_data.db", **kwargs):
        super().__init__(name="DatabaseModule", priority=ObserverPriority.HIGH, **kwargs)
        self.db_path = db_path
        self.write_buffer = []
        self.buffer_lock = threading.Lock()
        self.last_write = time.time()
    
    def update(self, refined_data: List[Dict], summary: Dict):
        with self.buffer_lock:
            self.write_buffer.extend(refined_data)
            if len(self.write_buffer) >= 50 or (time.time() - self.last_write) > 5:
                self._flush_buffer()
    
    def _flush_buffer(self):
        if not self.write_buffer:
            return
        try:
            self.logger.debug(f"Escribiendo {len(self.write_buffer)} eventos a {self.db_path}")
            self.write_buffer.clear()
            self.last_write = time.time()
        except Exception as e:
            self.logger.error(f"Error escribiendo a DB: {e}")
            raise
    
    def on_detach(self):
        self._flush_buffer()
        super().on_detach()


class SignalMonitor(SignalObserver):
    def __init__(self, **kwargs):
        super().__init__(name="SignalMonitor", priority=ObserverPriority.LOW, **kwargs)
        self.last_quality = 0
    
    def update(self, refined_data: List[Dict], summary: Dict):
        quality = summary.get("avg_quality", 0)
        if abs(quality - self.last_quality) > 5:
            self._update_display(quality, summary)
            self.last_quality = quality
    
    def _update_display(self, quality: float, summary: Dict):
        bar_length = 30
        filled = int(bar_length * quality / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        if quality >= 70:
            color = "🟢"
        elif quality >= 40:
            color = "🟡"
        else:
            color = "🔴"
        
        print(f"\r{color} Calidad: [{bar}] {quality:.1f}% | "
              f"Señal: {summary.get('avg_signal', 'N/A')} dBm | "
              f"Red: {summary.get('common_rat', 'N/A')}", end="", flush=True)


# --- Ejemplo de uso ---
if __name__ == "__main__":
    import sys
    
    print("="*60)
    print("🔧 SIGNAL PIPELINE - Sistema de Monitoreo de Señal")
    print("="*60)
    
    pipeline = SignalPipeline(batch_size=30, batch_timeout=1.0, enable_stats=True)
    
    # Suscribir observadores
    pipeline.subscribe(AlarmSystem())
    pipeline.subscribe(DatabaseModule(db_path="signals.db"))
    pipeline.subscribe(SignalMonitor())
    
    try:
        # CORRECCIÓN: Usar start_pipeline() o start() (ahora funciona)
        if not pipeline.start():  # También funciona con start()
            print("❌ Error al iniciar el pipeline")
            sys.exit(1)
        
        print("\n✅ Sistema operativo. Presiona Ctrl+C para detener...\n")
        
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Deteniendo sistema...")
    finally:
        pipeline.stop()
        print("\n✅ Sistema detenido correctamente")