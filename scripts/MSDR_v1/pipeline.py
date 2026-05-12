"""
Pipeline Module – Orquestador principal del sistema de monitoreo de señal.
Conecta ADBInput con SignalLogic y notifica a los observadores.
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Dict, Any, Optional

# Importaciones locales (asumiendo estructura de carpetas adecuada)
from Inputs.ADB_input import ADBInput
from SRC.Logic import SignalLogic

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


# ============================================================================
# CLASES BASE PARA OBSERVADORES
# ============================================================================

class ObserverPriority(Enum):
    """Prioridades para los observadores (menor número = mayor prioridad)"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    DEBUG = 4


class SignalObserver(ABC):
    """Clase base para cualquier módulo que quiera recibir datos procesados."""

    def __init__(self, name: Optional[str] = None, priority: ObserverPriority = ObserverPriority.NORMAL):
        self.name = name or self.__class__.__name__
        self.priority = priority
        self.logger = logging.getLogger(f"Observer.{self.name}")
        self.is_active = True
        self.error_count = 0

    @abstractmethod
    def update(self, refined_data: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
        """Método llamado cuando hay nuevos datos procesados."""
        pass

    def on_error(self, error: Exception) -> None:
        """Manejo de errores internos."""
        self.error_count += 1
        self.logger.error(f"Error en observador {self.name}: {error}")
        if self.error_count > 10:
            self.logger.critical(f"Demasiados errores en {self.name}, desactivando...")
            self.is_active = False

    def on_attach(self, pipeline: 'SignalPipeline') -> None:
        """Callback cuando se suscribe al pipeline."""
        self.logger.info(f"Observador {self.name} conectado al pipeline")

    def on_detach(self) -> None:
        """Callback cuando se desuscribe."""
        self.logger.info(f"Observador {self.name} desconectado")


# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

class SignalPipeline:
    """
    Orquestador principal.
    - Inicia ADBInput.
    - Procesa lotes con SignalLogic.
    - Notifica a observadores suscritos.
    """
# nota para saber que aca voy a hacer una estupides volvio al original de 30 si mal no recuerdo
    def __init__(self,
                 #SEPARADOR PARA LA CARGA DE NUEVO ENTRADAS DE MODEM 
                 batch_size: int =  30,
                 batch_timeout: float = 1.0,
                 enable_stats: bool = True):
        """
        Args:
            batch_size: Tamaño de lote para ADBInput.
            batch_timeout: Timeout para forzar flush de lote.
            enable_stats: Habilita estadísticas internas.
        """
        self.logger = logging.getLogger("SignalPipeline")

        # Componentes principales
        self.input_node = ADBInput(
            batch_size=batch_size,
            batch_timeout=batch_timeout,
            enable_file_logging=False      # Delegamos el logging al pipeline
        )
        self.logic_engine = SignalLogic(enable_stats=enable_stats)

        # Conectar callback de ADBInput al pipeline
        self.input_node.set_batch_callback(self._orchestrate_processing)

        # Observadores organizados por prioridad
        self._observers = {
            ObserverPriority.CRITICAL: [],
            ObserverPriority.HIGH: [],
            ObserverPriority.NORMAL: [],
            ObserverPriority.LOW: [],
            ObserverPriority.DEBUG: []
        }
        self._observers_lock = threading.RLock()

        # Estado del pipeline
        self._is_running = False

        # Estadísticas propias del pipeline (opcional)
        self.stats = {
            "batches_received": 0,
            "batches_processed": 0,
            "errors": 0
        } if enable_stats else None

        self.logger.info("SignalPipeline inicializado")

    # ------------------------------------------------------------------------
    # Métodos públicos de control
    # ------------------------------------------------------------------------

    def start(self) -> bool:
        """Inicia la captura y el procesamiento."""
        if self._is_running:
            self.logger.warning("Pipeline ya está en ejecución")
            return False

        try:
            if not self.input_node.start():
                self.logger.error("ADBInput no pudo iniciar")
                return False

            self._is_running = True
            self.logger.info("✅ Pipeline iniciado correctamente")
            return True

        except Exception as e:
            self.logger.error(f"Error iniciando pipeline: {e}")
            return False

#bugfix de fuga de memoria en la base de datos: 
    def stop(self, timeout: int = 5) -> None:
        """Detiene la captura y el pipeline de forma segura.
           Ahora 100% a prueba de fallos catastróficos."""
        self.logger.info("Deteniendo pipeline...")
        # Intentamos detener el ADBInput, pero si explota, lo anotamos y seguimos adelante con el proceso de apagado.
        
        try:
            self.input_node.stop(timeout=timeout)  # Detener ADBInput primero
        except Exception as e:
            # Si el ADB explota al cerrar, lo anotamos pero NO detenemos el proceso de apagado
            self.logger.error(f"Error menor al detener ADBInput: {e}")
        finally:
            # Todo lo que esté en este bloque se ejecutará SÍ O SÍ, explote o no el ADB.
            self._is_running = False
            #chingue su madre el america 
            self.logger.info("Desconectando observadores y forzando guardado final...")
            with self._observers_lock:
                for priority in self._observers:
                    for observer in list(self._observers[priority]):
                        self.unsubscribe(observer)

            self.logger.info("✅ Pipeline detenido")

    # ------------------------------------------------------------------------
    # Gestión de observadores
    # ------------------------------------------------------------------------

    def subscribe(self, observer: SignalObserver) -> bool:
        """Suscribe un observador al pipeline."""
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
        """Desuscribe un observador."""
        with self._observers_lock:
            priority = observer.priority
            if observer in self._observers[priority]:
                self._observers[priority].remove(observer)
                observer.on_detach()
                self.logger.info(f"📌 Observador desuscrito: {observer.name}")
                return True
            return False

    # ------------------------------------------------------------------------
    # Procesamiento interno
    # ------------------------------------------------------------------------

    def _orchestrate_processing(self, batch: List[Dict]) -> None:
        """
        Callback invocado por ADBInput cuando hay un lote listo.
        Procesa el lote con SignalLogic y notifica a los observadores.
        """
        if not self._is_running:
            return

        if self.stats:
            self.stats["batches_received"] += 1

        try:
            # 1. Procesar lote crudo con SignalLogic
            refined_data = self.logic_engine.process_batch(batch)

            if not refined_data:
                # No hay datos útiles en este lote
                return

            # 2. Generar resumen estadístico
            summary = self.logic_engine.get_summary(refined_data)

            # 3. Notificar a todos los observadores
            self._notify_all(refined_data, summary)

            if self.stats:
                self.stats["batches_processed"] += 1

        except Exception as e:
            self.logger.error(f"Error procesando lote: {e}")
            if self.stats:
                self.stats["errors"] += 1

    def _notify_all(self, data: List[Dict], summary: Dict) -> None:
        """Notifica a todos los observadores en orden de prioridad."""
        with self._observers_lock:
            # Iterar en orden de prioridad ascendente (menor número = mayor prioridad)
            for priority in sorted(self._observers.keys(), key=lambda p: p.value):
                for observer in self._observers[priority]:
                    if not observer.is_active:
                        continue
                    try:
                        observer.update(data, summary)
                    except Exception as e:
                        self.logger.error(f"Error notificando a {observer.name}: {e}")
                        observer.on_error(e)

    # ------------------------------------------------------------------------
    # Información de estado
    # ------------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Devuelve estadísticas actuales del pipeline."""
        stats = {
            "is_running": self._is_running,
            "adb_stats": self.input_node.get_stats(),
            "logic_stats": self.logic_engine.get_pattern_stats() if self.logic_engine.enable_stats else {}
        }
        if self.stats:
            stats.update(self.stats)
        return stats


# ============================================================================
# OBSERVADORES INTEGRADOS (ejemplos básicos)
# ============================================================================

class AlarmSystem(SignalObserver):
    """Sistema de alarmas para eventos críticos (seguridad, señal muy débil)."""
    def __init__(self, cooldown: int = 30):
        super().__init__(name="AlarmSystem", priority=ObserverPriority.CRITICAL)
        self.cooldown = cooldown
        self._last_alert = {}

    def _should_alert(self, alert_type: str) -> bool:
        now = time.time()
        if alert_type in self._last_alert:
            if now - self._last_alert[alert_type] < self.cooldown:
                return False
        self._last_alert[alert_type] = now
        return True

    def update(self, refined_data: List[Dict], summary: Dict) -> None:
        # Seguridad
        if summary.get("security_issues", 0) > 0 and self._should_alert("security"):
            self.logger.warning("🚨 ALERTA DE SEGURIDAD: Posible celda falsa o tráfico sin cifrado")
            print("\n🚨 [ALERTA CRÍTICA] ¡Posible ataque de celda falsa detectado!")

        # Calidad crítica
        quality = summary.get("avg_quality", 100)
        if quality < 30 and self._should_alert("critical_quality"):
            self.logger.critical(f"⚠️ SEÑAL CRÍTICA: Calidad {quality:.1f}%")
            print(f"\n🔴 [ALERTA] Señal extremadamente débil: {quality:.1f}%")
        elif quality < 40 and self._should_alert("poor_quality"):
            self.logger.warning(f"⚠️ SEÑAL INESTABLE: Calidad {quality:.1f}%")
            print(f"\n🟡 [ADVERTENCIA] Señal inestable: {quality:.1f}%")


class DatabaseModule(SignalObserver):
    """Módulo de persistencia simple (guarda en archivo JSONL)."""
    def __init__(self, db_path: str = "signal_data.jsonl", flush_size: int = 50, flush_timeout: float = 5.0):
        super().__init__(name="DatabaseModule", priority=ObserverPriority.HIGH)
        self.db_path = db_path
        self.flush_size = flush_size
        self.flush_timeout = flush_timeout
        self._buffer = []
        self._lock = threading.Lock()
        self._last_write = time.time()

    def update(self, refined_data: List[Dict], summary: Dict) -> None:
        if not refined_data:
            return
        with self._lock:
            self._buffer.extend(refined_data)
            if len(self._buffer) >= self.flush_size or (time.time() - self._last_write) >= self.flush_timeout:
                self._flush()

    def _flush(self):
        if not self._buffer:
            return
        try:
            import json
            with open(self.db_path, 'a', encoding='utf-8') as f:
                for entry in self._buffer:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            self.logger.debug(f"Escritos {len(self._buffer)} eventos a {self.db_path}")
            self._buffer.clear()
            self._last_write = time.time()
        except Exception as e:
            self.logger.error(f"Error escribiendo a DB: {e}")

    def on_detach(self):
        self._flush()
        super().on_detach()


class SignalMonitor(SignalObserver):
    """Monitor simple en consola (barra de calidad en tiempo real)."""
    def __init__(self):
        super().__init__(name="SignalMonitor", priority=ObserverPriority.LOW)
        self._last_quality = 0

    def update(self, refined_data: List[Dict], summary: Dict) -> None:
        quality = summary.get("avg_quality", 0)
        # Mostrar solo cambios significativos
        if abs(quality - self._last_quality) > 5:
            self._display(quality, summary)
            self._last_quality = quality

    def _display(self, quality: float, summary: Dict):
        bar_len = 30
        filled = int(bar_len * quality / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        if quality >= 70:
            color = "🟢"
        elif quality >= 40:
            color = "🟡"
        else:
            color = "🔴"

        print(f"\r{color} Calidad: [{bar}] {quality:.1f}% | "
              f"Señal: {summary.get('avg_signal', 'N/A')} dBm | "
              f"Red: {summary.get('common_rat', 'N/A')}", end="", flush=True)


# ============================================================================
# PRUEBA RÁPIDA (ejecutar directamente)
# ============================================================================

if __name__ == "__main__":
    import time

    print("Iniciando pipeline de prueba (simulación con ADB real si está conectado)...")

    pipeline = SignalPipeline(batch_size=5, batch_timeout=1.0, enable_stats=True)

    # Suscribir algunos observadores de ejemplo
    pipeline.subscribe(AlarmSystem())
    pipeline.subscribe(DatabaseModule())
    pipeline.subscribe(SignalMonitor())

    try:
        if pipeline.start():
            print("\nPipeline en ejecución. Presiona Ctrl+C para detener...\n")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nDeteniendo...")
    finally:
        pipeline.stop()
        print("\nEstadísticas finales:", pipeline.get_stats())