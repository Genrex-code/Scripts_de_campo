"""
Módulo principal de la TUI con asciimatics.
Gestiona el modelo compartido, las escenas y la comunicación con el pipeline.
"""

import threading
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
        self._refresh_timer = None
        self._tui_thread = None

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
            self._running = False

    def _main(self, screen):
        self._screen = screen
        self._scenes = [
            DashboardScene(screen, self, title="📡 Dashboard"),
            RawLogsScene(screen, self, title="📜 Raw Logs"),
            AlertsScene(screen, self, title="⚠️ Alertas"),
            StatsScene(screen, self, title="📊 Estadísticas")
        ]
        # Crear una escena inicial
        current_scene = Scene([self._scenes[0]], -1, name="Dashboard")
        # Lanzar el bucle principal (esto bloquea hasta que se cierre)
        screen.play([current_scene], stop_on_resize=False, start_scene=current_scene)

    def change_scene(self, idx: int):
        """Cambia a la escena índice idx."""
        if 0 <= idx < len(self._scenes):
            self._current_scene_idx = idx
            # Lanzar excepción para que asciimatics cambie de escena
            raise NextScene(self._scenes[idx].name)

    def start(self):
        """Inicia la TUI en un hilo separado."""
        self._tui_thread = threading.Thread(target=self._run_asciimatics, daemon=True)
        self._tui_thread.start()
        self.logger.info("TUI asciimatics iniciada")

    def stop(self):
        """Detiene la TUI (cierra la aplicación)."""
        self._running = False
        if self._screen:
            # Forzar salida
            self._screen.quit()
        if self._tui_thread:
            self._tui_thread.join(timeout=2)
        self.logger.info("TUI detenida")

    def on_attach(self, pipeline):
        super().on_attach(pipeline)
        self.start()

    def on_detach(self):
        self.stop()
        super().on_detach()