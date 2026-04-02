"""
Escena para mostrar alertas detalladas.
"""

from asciimatics.widgets import ListBox, Layout, Label, Widget

from .base import BaseScene


class AlertsScene(BaseScene):
    def _setup_layout(self):
        # Layout principal para la lista
        main_layout = Layout([1])
        self.add_layout(main_layout)

        # ListBox para las alertas
        self.alerts_list = ListBox(Widget.FILL_FRAME, [], name="alerts")
        main_layout.columns[0].add_widget(self.alerts_list, 0)

        # Layout para el pie de página
        footer_layout = Layout([1])
        self.add_layout(footer_layout)
        footer_layout.columns[0].add_widget(
            Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---")
        )

        self.fix()

    def refresh(self):
        with self.model.lock:
            alerts = list(self.model.alerts)

        # Formatear cada alerta para mostrar timestamp y mensaje
        items = [(f"[{a['timestamp'][:19]}] {a['message']}", i) for i, a in enumerate(alerts[-100:])]
        self.alerts_list.options = items
        self.alerts_list.value = len(items) - 1