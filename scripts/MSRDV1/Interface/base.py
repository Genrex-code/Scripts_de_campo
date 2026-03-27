"""
Módulo base para la interfaz asciimatics.
Contiene la clase SharedData (modelo de datos) y BaseScene (escena común).
"""

import threading
from collections import deque
from typing import List, Dict, Any, Optional, Deque
from datetime import datetime

from asciimatics.widgets import Frame


class SharedData:
    """
    Modelo de datos compartido entre todas las escenas.
    Thread-safe mediante locks.
    """
    def __init__(self):
        self.lock = threading.RLock()
        # Datos del último resumen
        self.current_summary: Dict[str, Any] = {}
        # Logs crudos (últimas 200 líneas)
        self.raw_logs: Deque[str] = deque(maxlen=200)
        # Alertas (últimas 100)
        self.alerts: Deque[Dict[str, str]] = deque(maxlen=100)
        # Historial de calidades (para estadísticas)
        self.quality_history: Deque[float] = deque(maxlen=1000)
        # Otras estadísticas agregadas
        self.total_events = 0
        self.rat_counts: Dict[str, int] = {}
        self.metrics_counts: Dict[str, int] = {}
        # Para debugging
        self.last_update_time: Optional[datetime] = None

    def update(self, refined_data: List[Dict], summary: Dict) -> None:
        """Actualiza el modelo con un nuevo lote procesado."""
        with self.lock:
            self.current_summary = summary
            self.last_update_time = datetime.now()
            self.total_events += len(refined_data)

            # Raw logs
            for entry in refined_data:
                self.raw_logs.append(entry.get("raw_payload", ""))

            # Alertas
            for entry in refined_data:
                for alert in entry.get("alerts", []):
                    self.alerts.append({
                        "timestamp": entry.get("timestamp", datetime.now().isoformat()),
                        "message": alert,
                        "type": "security" if "SECURITY" in alert else "warning"
                    })

            # Calidad (para estadísticas)
            quality = summary.get("avg_quality")
            if quality is not None:
                self.quality_history.append(quality)

            # Rat distribution
            rat = summary.get("common_rat")
            if rat:
                self.rat_counts[rat] = self.rat_counts.get(rat, 0) + 1

            # Métricas encontradas (de processor_stats si existe)
            if "processor_stats" in summary:
                for k, v in summary["processor_stats"].get("top_metrics", {}).items():
                    self.metrics_counts[k] = self.metrics_counts.get(k, 0) + v

    def get_stats(self) -> Dict[str, Any]:
        """Devuelve estadísticas agregadas."""
        with self.lock:
            return {
                "total_events": self.total_events,
                "quality_avg": sum(self.quality_history) / len(self.quality_history) if self.quality_history else 0,
                "quality_max": max(self.quality_history) if self.quality_history else 0,
                "quality_min": min(self.quality_history) if self.quality_history else 0,
                "rat_distribution": dict(self.rat_counts),
                "metrics_distribution": dict(self.metrics_counts),
                "last_update": self.last_update_time.isoformat() if self.last_update_time else None
            }


class BaseScene(Frame):
    """
    Clase base para todas las escenas.
    Cada escena debe implementar `refresh()` para actualizar sus widgets.
    """
    def __init__(self, screen, app, title: str):
        super().__init__(screen, screen.height, screen.width, title=title,data= None)
        self.app = app          # referencia a la aplicación principal
        self.data = app.data    # acceso al modelo compartido
        self._refresh_interval = app.refresh_rate
        self._setup_layout()
        #duerman a quien hiso esto

    def _setup_layout(self):
        """Construye el layout de la escena (debe ser sobrescrito)."""
        raise NotImplementedError

    def refresh(self):
        """Actualiza los valores de los widgets con los datos actuales."""
        raise NotImplementedError

    def process_event(self, event):
        # Capturar teclas para cambiar escena
        if isinstance(event, int):  # asciimatics pasa int para teclas
            if event == ord('1'):
                self.app.change_scene(0)
                return True
            elif event == ord('2'):
                self.app.change_scene(1)
                return True
            elif event == ord('3'):
                self.app.change_scene(2)
                return True
            elif event == ord('4'):
                self.app.change_scene(3)
                return True
        return super().process_event(event)