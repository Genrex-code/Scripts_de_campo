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
    """Prioridades para los observadores (control de orden de ejecución)"""
    CRITICAL = 0   # Alarmas, seguridad
    HIGH = 1       # Persistencia crítica
    NORMAL = 2     # Database, logging
    LOW = 3        # UI, métricas
    DEBUG = 4      # Debugging

@dataclass
class PipelineStats:
    """Estadísticas del pipeline para monitoreo"""
    total_batches: int = 0
    total_events: int = 0
    processing_times: deque = field(default_factory=lambda: deque(maxlen=100))
    errors_count: int = 0
    last_error: Optional[str] = None
    last_processing_time: float = 0.0
    
    @property
    def avg_processing_time(self) -> float:
        """Tiempo promedio de procesamiento"""
        if not self.processing_times:
            return 0.0
        return sum(self.processing_times) / len(self.processing_times)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario para logging"""
        return {
            "total_batches": self.total_batches,
            "total_events": self.total_events,
            "avg_processing_ms": self.avg_processing_time * 1000,
            "errors_count": self.errors_count,
            "last_error": self.last_error
        }


# 1. Interfaz del Observador Mejorada
class SignalObserver(ABC):
    """Clase base mejorada para observadores de señal"""
    
    def __init__(self, name: Optional[str] = None, priority: ObserverPriority = ObserverPriority.NORMAL):
        """
        Args:
            name: Nombre del observador (para logging)
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
            refined_data: Datos procesados del lote
            summary: Resumen estadístico del lote
        """
        pass
    
    def on_error(self, error: Exception) -> None:
        """
        Callback cuando ocurre un error en el observador.
        Puede ser sobrescrito para manejo personalizado.
        """
        self.error_count += 1
        self.logger.error(f"Error en observador: {error}")
        if self.error_count > 10:
            self.logger.critical(f"Demasiados errores ({self.error_count}) en {self.name}, desactivando...")
            self.is_active = False
    
    def on_attach(self, pipeline: 'SignalPipeline') -> None:
        """Callback cuando el observador es añadido al pipeline"""
        self.logger.info(f"Observador {self.name} conectado al pipeline")
    
    def on_detach(self) -> None:
        """Callback cuando el observador es removido del pipeline"""
        self.logger.info(f"Observador {self.name} desconectado")


# 2. El Pipeline Mejorado
class SignalPipeline:
    """
    Pipeline principal de procesamiento de señales.
    Implementa el patrón Observer con manejo de errores y estadísticas.
    """
    
    def __init__(self, 
                 batch_size: int = 30,
                 batch_timeout: float = 1.0,
                 max_queue_size: int = 100,
                 enable_stats: bool = True):
        """
        Args:
            batch_size: Tamaño de lote para ADB input
            batch_timeout: Timeout para forzar flush
            max_queue_size: Tamaño máximo de cola interna
            enable_stats: Habilitar estadísticas del pipeline
        """
        self.logger = logging.getLogger("SignalPipeline")
        
        # Configuración
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.enable_stats = enable_stats
        
        # Observadores organizados por prioridad
        self._observers: Dict[ObserverPriority, List[SignalObserver]] = {
            priority: [] for priority in ObserverPriority
        }
        self._observers_lock = threading.RLock()
        
        # Componentes principales
        self.input_node = None
        self.logic_engine = None
        
        # Estado y estadísticas
        self.is_running = False
        self.stats = PipelineStats() if enable_stats else None
        self._processing_lock = threading.Lock()
        
        # Thread de monitoreo (opcional)
        self._monitor_thread = None
        
        self.logger.info("SignalPipeline inicializado")
    
    def _initialize_components(self):
        """Inicialización lazy de componentes para evitar importaciones circulares"""
        if self.input_node is None:
            from Inputs.ADB_input import ADBInput
            self.input_node = ADBInput(
                batch_size=self.batch_size,
                batch_timeout=self.batch_timeout
            )
            # Inyectamos nuestra lógica de orquestación
            self.logic_engine = self._orchestrate_processing
        
        if self.logic_engine is None:
            from SRC.Logic import SignalLogic
            self.logic_engine = SignalLogic(enable_stats=self.enable_stats)
    
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
                self.logger.info(f"Observador suscrito: {observer.name} (Prioridad: {priority.name})")
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
                self.logger.info(f"Observador desuscrito: {observer.name}")
                return True
            return False
    
    def _notify_all(self, data: List[Dict], summary: Dict) -> None:
        """
        Notifica a todos los observadores en orden de prioridad.
        Los errores no interrumpen la notificación a otros observadores.
        """
        with self._observers_lock:
            # Notificar en orden de prioridad (menor número = mayor prioridad)
            for priority in sorted(ObserverPriority, key=lambda p: p.value):
                for observer in self._observers[priority]:
                    if not observer.is_active:
                        continue
                    
                    try:
                        start_time = time.time()
                        observer.update(data, summary)
                        elapsed = time.time() - start_time
                        
                        if elapsed > 1.0:  # Alerta si tarda más de 1 segundo
                            self.logger.warning(
                                f"Observador lento {observer.name}: {elapsed:.2f}s"
                            )
                            
                    except Exception as e:
                        self.logger.error(f"Error notificando a {observer.name}: {e}")
                        observer.on_error(e)
    
    def _orchestrate_processing(self, raw_batch: List[Dict]) -> None:
        """
        Corazón del sistema: procesa el lote y notifica a observadores.
        
        Args:
            raw_batch: Lote crudo del ADB input
        """
        if not raw_batch:
            return
        
        # Evitar procesamiento concurrente
        if not self._processing_lock.acquire(blocking=False):
            self.logger.warning("Procesamiento anterior aún en curso, saltando lote...")
            return
        
        try:
            start_time = time.time()
            
            # 1. Filtrado y extracción con Logic
            refined_data = self.logic_engine.process_batch(raw_batch)
            
            if refined_data:
                # 2. Generación de estadísticas rápidas
                summary = self.logic_engine.get_summary(refined_data)
                
                # 3. Distribución a suscriptores
                self._notify_all(refined_data, summary)
                
                # 4. Actualizar estadísticas
                if self.enable_stats and self.stats:
                    processing_time = time.time() - start_time
                    self.stats.total_batches += 1
                    self.stats.total_events += len(refined_data)
                    self.stats.processing_times.append(processing_time)
                    self.stats.last_processing_time = processing_time
                    
                    # Logging periódico de estadísticas
                    if self.stats.total_batches % 10 == 0:
                        self.logger.info(f"Pipeline stats: {self.stats.to_dict()}")
            
        except Exception as e:
            self.logger.error(f"Error en procesamiento: {e}", exc_info=True)
            if self.enable_stats and self.stats:
                self.stats.errors_count += 1
                self.stats.last_error = str(e)
        
        finally:
            self._processing_lock.release()
    
    def start(self) -> bool:
        """
        Inicia el pipeline completo.
        
        Returns:
            bool: True si se inició correctamente
        """
        if self.is_running:
            self.logger.warning("Pipeline ya está en ejecución")
            return False
        
        try:
            self._initialize_components()
            
            if not self.input_node.start():
                self.logger.error("No se pudo iniciar el nodo de entrada")
                return False
            
            self.is_running = True
            
            # Iniciar thread de monitoreo si hay estadísticas
            if self.enable_stats:
                self._start_monitor_thread()
            
            self.logger.info("✅ Pipeline iniciado correctamente")
            return True
            
        except Exception as e:
            self.logger.error(f"Error iniciando pipeline: {e}")
            return False
    
    def stop(self) -> None:
        """
        Detiene el pipeline de manera graceful.
        """
        self.logger.info("Deteniendo pipeline...")
        self.is_running = False
        
        # Detener monitor
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2)
        
        # Detener componentes
        if self.input_node:
            self.input_node.stop()
        
        # Mostrar estadísticas finales
        if self.enable_stats and self.stats:
            self.logger.info(f"Pipeline stats finales: {self.stats.to_dict()}")
        
        self.logger.info("✅ Pipeline detenido")
    
    def _start_monitor_thread(self) -> None:
        """Inicia thread de monitoreo para estadísticas en tiempo real"""
        def monitor():
            while self.is_running:
                time.sleep(30)  # Cada 30 segundos
                if self.stats and self.stats.total_batches > 0:
                    self.logger.debug(f"Monitoreo: {self.stats.to_dict()}")
        
        self._monitor_thread = threading.Thread(target=monitor, daemon=True, name="PipelineMonitor")
        self._monitor_thread.start()
    
    def get_stats(self) -> Optional[Dict[str, Any]]:
        """Obtiene estadísticas actuales del pipeline"""
        if self.stats:
            return self.stats.to_dict()
        return None
    
    def get_observers_info(self) -> List[Dict[str, Any]]:
        """Obtiene información de los observadores suscritos"""
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


# 3. Módulos Especializados Mejorados

class AlarmSystem(SignalObserver):
    """Módulo de alarmas mejorado con diferentes niveles de alerta"""
    
    def __init__(self, name: str = "AlarmSystem", **kwargs):
        super().__init__(name=name, priority=ObserverPriority.CRITICAL, **kwargs)
        self.last_alert_time = {}
        self.alert_cooldown = 30  # Segundos entre alertas del mismo tipo
    
    def _should_alert(self, alert_type: str) -> bool:
        """Control de cooldown para evitar spam de alertas"""
        now = time.time()
        if alert_type in self.last_alert_time:
            if now - self.last_alert_time[alert_type] < self.alert_cooldown:
                return False
        self.last_alert_time[alert_type] = now
        return True
    
    def update(self, refined_data: List[Dict], summary: Dict):
        """Procesa y genera alertas según los datos recibidos"""
        
        # Alerta de seguridad (cifrado)
        if summary.get("security_issues", 0) > 0:
            if self._should_alert("security"):
                self.logger.warning("🚨 ALERTA DE SEGURIDAD: Posible celda falsa o tráfico sin cifrado")
                print("\n" + "="*60)
                print("🚨 [ALERTA CRÍTICA] ¡Posible ataque de celda falsa detectado!")
                print(f"   Eventos sin cifrado: {summary.get('security_issues')}")
                print("="*60)
        
        # Alarma de inestabilidad
        avg_quality = summary.get("avg_quality", 100)
        if avg_quality < 30:
            if self._should_alert("critical_instability"):
                self.logger.critical(f"⚠️ SEÑAL CRÍTICAMENTE INESTABLE: Calidad {avg_quality:.1f}%")
                print(f"\n🔴 [ALERTA CRÍTICA] Señal extremadamente débil: {avg_quality:.1f}%")
        elif avg_quality < 40:
            if self._should_alert("instability"):
                self.logger.warning(f"⚠️ SEÑAL INESTABLE: Calidad {avg_quality:.1f}%")
                print(f"\n🟡 [ADVERTENCIA] Señal inestable: {avg_quality:.1f}%")
        
        # Alerta de cambio de tecnología (handover)
        common_rat = summary.get("common_rat")
        if common_rat and hasattr(self, '_last_rat'):
            if self._last_rat != common_rat:
                self.logger.info(f"🔄 Cambio de tecnología: {self._last_rat} → {common_rat}")
                print(f"\n🔄 Handover detectado: {self._last_rat} → {common_rat}")
        self._last_rat = common_rat


class DatabaseModule(SignalObserver):
    """Módulo de persistencia mejorado con buffer y escritura asíncrona"""
    
    def __init__(self, 
                 db_path: str = "signal_data.db",
                 batch_write: bool = True,
                 **kwargs):
        super().__init__(name="DatabaseModule", priority=ObserverPriority.HIGH, **kwargs)
        self.db_path = db_path
        self.batch_write = batch_write
        self.write_buffer = []
        self.buffer_lock = threading.Lock()
        self.last_write = time.time()
        
    def update(self, refined_data: List[Dict], summary: Dict):
        """Guarda los datos en la base de datos"""
        with self.buffer_lock:
            self.write_buffer.extend(refined_data)
            
            # Escribir si el buffer está lleno o ha pasado suficiente tiempo
            if len(self.write_buffer) >= 50 or (time.time() - self.last_write) > 5:
                self._flush_buffer()
    
    def _flush_buffer(self):
        """Escribe el buffer a la base de datos"""
        if not self.write_buffer:
            return
        
        try:
            # Aquí implementar la lógica real de DB
            # Por ahora solo simulamos
            self.logger.debug(f"Escribiendo {len(self.write_buffer)} eventos a {self.db_path}")
            self.write_buffer.clear()
            self.last_write = time.time()
            
        except Exception as e:
            self.logger.error(f"Error escribiendo a DB: {e}")
            raise
    
    def on_detach(self):
        """Al desconectar, asegurar que se escriben datos pendientes"""
        self._flush_buffer()
        super().on_detach()


class MetricsExporter(SignalObserver):
    """Módulo para exportar métricas a sistemas externos (Prometheus, etc.)"""
    
    def __init__(self, export_endpoint: Optional[str] = None, **kwargs):
        super().__init__(name="MetricsExporter", priority=ObserverPriority.LOW, **kwargs)
        self.export_endpoint = export_endpoint
        self.metrics_buffer = deque(maxlen=1000)
    
    def update(self, refined_data: List[Dict], summary: Dict):
        """Acumula métricas y exporta periódicamente"""
        # Acumular métricas importantes
        if "avg_signal" in summary:
            self.metrics_buffer.append({
                "timestamp": datetime.now().isoformat(),
                "avg_signal": summary["avg_signal"],
                "avg_quality": summary["avg_quality"],
                "common_rat": summary.get("common_rat")
            })
        
        # Exportar cada 10 batches
        if len(self.metrics_buffer) % 10 == 0:
            self._export_metrics()
    
    def _export_metrics(self):
        """Exporta métricas a sistema externo"""
        if not self.metrics_buffer:
            return
        
        # Aquí implementar exportación real (HTTP, Prometheus, etc.)
        self.logger.debug(f"Exportando {len(self.metrics_buffer)} métricas")


class SignalMonitor(SignalObserver):
    """Módulo de monitoreo en tiempo real para visualización"""
    
    def __init__(self, **kwargs):
        super().__init__(name="SignalMonitor", priority=ObserverPriority.LOW, **kwargs)
        self.last_quality = 0
    
    def update(self, refined_data: List[Dict], summary: Dict):
        """Actualiza visualización en tiempo real"""
        quality = summary.get("avg_quality", 0)
        
        # Solo mostrar cambios significativos
        if abs(quality - self.last_quality) > 5:
            self._update_display(quality, summary)
            self.last_quality = quality
    
    def _update_display(self, quality: float, summary: Dict):
        """Actualiza display en consola"""
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
        
        print(f"\r{color} Calidad: [{bar}] {quality:.1f}% | "
              f"Señal: {summary.get('avg_signal', 'N/A')} dBm | "
              f"Red: {summary.get('common_rat', 'N/A')}", end="", flush=True)


# --- Ejemplo de Ejecución Mejorado ---
if __name__ == "__main__":
    import time
    import sys
    
    print("="*60)
    print("🔧 SIGNAL PIPELINE - Sistema de Monitoreo de Señal")
    print("="*60)
    
    # Crear pipeline con configuración personalizada
    pipeline = SignalPipeline(
        batch_size=30,
        batch_timeout=1.0,
        enable_stats=True
    )
    
    # Configurar observadores
    pipeline.subscribe(AlarmSystem())
    pipeline.subscribe(DatabaseModule(db_path="signals.db"))
    pipeline.subscribe(MetricsExporter())
    pipeline.subscribe(SignalMonitor())  # Monitor en tiempo real
    
    try:
        # Iniciar pipeline
        if not pipeline.start():
            print("❌ Error al iniciar el pipeline")
            sys.exit(1)
        
        print("\n✅ Sistema operativo. Presiona Ctrl+C para detener...\n")
        
        # Bucle principal
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Deteniendo sistema...")
    finally:
        pipeline.stop()
        print("\n✅ Sistema detenido correctamente")