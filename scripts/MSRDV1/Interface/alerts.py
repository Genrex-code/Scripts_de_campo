"""
Escena para mostrar alertas detalladas.
"""

from asciimatics.widgets import ListBox, Layout, Frame, Label

from .base import BaseScene


class AlertsScene(BaseScene):
    def _setup_layout(self):
        layout = Layout([1])
        self.add_layout(layout)
        # ListBox para las alertas
        self.alerts_list = ListBox(Widget.FILL_FRAME, [], name="alerts")
        layout.add_widget(self.alerts_list, 0)
        layout.add_widget(Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---"), 1)
        self.fix()

    def refresh(self):
        with self.data.lock:
            alerts = list(self.data.alerts)
        # Formatear cada alerta para mostrar timestamp y mensaje
        items = [(f"[{a['timestamp'][:19]}] {a['message']}", i) for i, a in enumerate(alerts[-100:])]
        self.alerts_list.options = items
        self.alerts_list.value = len(items) - 1