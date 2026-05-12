"""
Módulo base para la interfaz asciimatics.
Contiene la clase SharedData (modelo de datos) y BaseScene (escena común).
"""

import threading
from collections import deque
from typing import List, Dict, Any, Optional
from datetime import datetime

from asciimatics.widgets import Frame
from asciimatics.event import KeyboardEvent


class SharedData:
    """
    Modelo de datos compartido entre todas las escenas.
    Thread-safe mediante locks.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.current_summary: Dict[str, Any] = {}
        self.raw_logs: deque[str] = deque(maxlen=200)
        self.alerts: deque[Dict[str, str]] = deque(maxlen=100)
        self.quality_history: deque[float] = deque(maxlen=1000)
        self.total_events = 0
        self.rat_counts: Dict[str, int] = {}
        self.metrics_counts: Dict[str, int] = {}
        self.last_update_time: Optional[datetime] = None

    def update(self, refined_data: List[Dict], summary: Dict) -> None:
        """Actualiza el modelo con un nuevo lote procesado."""
        with self.lock:
            self.current_summary = summary
            self.last_update_time = datetime.now()
            self.total_events += len(refined_data)

            for entry in refined_data:
                self.raw_logs.append(entry.get("raw_payload", ""))

            for entry in refined_data:
                for alert in entry.get("alerts", []):
                    self.alerts.append({
                        "timestamp": entry.get("timestamp", datetime.now().isoformat()),
                        "message": alert,
                        "type": "security" if "SECURITY" in alert else "warning"
                    })

            quality = summary.get("avg_quality")
            if quality is not None:
                self.quality_history.append(quality)

            rat = summary.get("common_rat")
            if rat:
                self.rat_counts[rat] = self.rat_counts.get(rat, 0) + 1

            # Métricas encontradas: Logic.py ya nos manda el total acumulado, 
            # así que simplemente REEMPLAZAMOS el valor en lugar de sumarlo.
            if "processor_stats" in summary:
                self.metrics_counts = summary["processor_stats"].get("top_metrics", {}).copy()  
                
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
    """
    def __init__(self, screen, app, title: str):
        super().__init__(screen, screen.height, screen.width, title=title, data={})
        self.app = app
        self.model = app.data          # nuestro modelo compartido
        self._setup_layout()

    def _setup_layout(self):
        """Construye el layout de la escena (debe ser sobrescrito)."""
        raise NotImplementedError

    def refresh(self):
        """Actualiza los valores de los widgets con los datos actuales."""
        raise NotImplementedError

    def _update(self, frame_no):
        """
        Método nativo de Asciimatics. Se llama en cada ciclo de dibujado.
        Aprovechamos esto para actualizar los datos en pantalla con blindaje.
        """
        try:
            self.refresh()
        except NotImplementedError:
            pass
            
        super()._update(frame_no)

    def process_event(self, event):
        """Captura eventos del teclado de forma correcta para Asciimatics."""
        if isinstance(event, KeyboardEvent):
            if event.key_code == ord('1'):
                self.app.change_scene(0)
                return None 
            elif event.key_code == ord('2'):
                self.app.change_scene(1)
                return None
            elif event.key_code == ord('3'):
                self.app.change_scene(2)
                return None
            elif event.key_code == ord('4'):
                self.app.change_scene(3)
                return None
        
        return super().process_event(event)