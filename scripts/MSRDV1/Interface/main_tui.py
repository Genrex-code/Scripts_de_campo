"""
Módulo principal de la TUI con asciimatics.
Gestiona el modelo compartido, las escenas y la comunicación con el pipeline.
"""

import time
import logging
from typing import List, Dict, Any, Optional

from asciimatics.screen import Screen
from asciimatics.scene import Scene
from asciimatics.exceptions import NextScene, StopApplication

# Importar clases base del pipeline
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import SignalObserver, ObserverPriority

# Importar el modelo y las escenas
from .base import SharedData
from .dashboard import DashboardScene
from .rawlogs import RawLogsScene
from .alerts import AlertsScene
from .stats import StatsScene

class AsciiTUI(SignalObserver):
    """
    Observador que lanza la interfaz asciimatics y recibe datos del pipeline.
    """
    def __init__(self, refresh_rate: float = 1.0):
        super().__init__(name="AsciiTUI", priority=ObserverPriority.LOW)
        self.refresh_rate = refresh_rate
        self.data = SharedData()
        self._running = True
        self._screen = None
        self._scenes = []
        self._current_scene_idx = 0
        self.logger = logging.getLogger("AsciiTUI")

    def update(self, refined_data: List[Dict], summary: Dict) -> None:
        """Llamado por el pipeline. Actualiza el modelo y refresca la escena activa."""
        self.data.update(refined_data, summary)
        # Si ya hay una escena activa, forzamos su actualización
        if self._screen and self._current_scene_idx < len(self._scenes):
            # La actualización se hará en el timer de la escena (refresh)
            pass

    def _run_asciimatics(self):
        """Ejecuta el bucle principal de asciimatics."""
        try:
            Screen.wrapper(self._main)
        except Exception as e:
            self.logger.error(f"Error en asciimatics: {e}")
            import traceback
            traceback.print_exc() # Esto te dirá exactamente qué falla si hay otro error
            self._running = False

    def _main(self, screen):
        self._screen = screen
        self._scenes = [
            Scene([DashboardScene(screen, self, title="📡 Dashboard")], -1, name="Dashboard"),
            Scene([RawLogsScene(screen, self, title="📜 Raw Logs")], -1, name="RawLogs"),
            Scene([AlertsScene(screen, self, title="⚠️ Alertas")], -1, name="Alerts"),
            Scene([StatsScene(screen, self, title="📊 Estadísticas")], -1, name="Stats")
        ]
        
        # Lanzar el bucle principal (esto bloquea hasta que se cierre)
        # FIX: Eliminamos el código duplicado que estaba abajo de esto
        screen.play(self._scenes, stop_on_resize=False, start_scene=self._scenes[0])

    def change_scene(self, idx: int):
        """Cambia a la escena índice idx."""
        if 0 <= idx < len(self._scenes):
            self._current_scene_idx = idx
            # Como ahora self._scenes tiene objetos Scene, el atributo .name ya existe y no tira error.
            raise NextScene(self._scenes[idx].name)

    def start(self):
        """Ejecuta la TUI en el hilo principal (bloqueante)."""
        self.logger.info("Tomando el control de la terminal para Asciimatics...")
        self._run_asciimatics()

    def stop(self):
        """Detiene la TUI."""
        self._running = False
        if self._screen:
            # Forzar salida segura de la terminal
            self._screen.quit()
        self.logger.info("TUI detenida")

    def on_attach(self, pipeline):
        super().on_attach(pipeline)
        # ⚠️ IMPORTANTE: Eliminamos el self.start() de aquí. 
        # Lo llamaremos manualmente desde Main.py para no bloquear la suscripción de otros módulos.

    def on_detach(self):
        self.stop()
        super().on_detach()