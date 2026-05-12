"""
Escena para mostrar logs crudos en tiempo real.
"""

from asciimatics.widgets import ListBox, Layout, Label, Widget

from .base import BaseScene


class RawLogsScene(BaseScene):
    def _setup_layout(self):
        # Layout principal (una columna) para la lista
        main_layout = Layout([1])
        self.add_layout(main_layout)

        # ListBox que ocupará todo el espacio disponible
        self.log_list = ListBox(Widget.FILL_FRAME, [], name="raw_logs")
        
        # FIX: Añadimos el widget de forma correcta a la columna 0
        main_layout.add_widget(self.log_list, 0)

        # Layout para el pie de página
        footer_layout = Layout([1])
        self.add_layout(footer_layout)
        
        # FIX: Añadimos el footer de forma correcta a la columna 0
        footer_layout.add_widget(
            Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---"), 0
        )

        self.fix()

    def refresh(self):
        with self.model.lock:
            logs = list(self.model.raw_logs)

        # Preparar lista de tuplas (valor, índice) para ListBox
        items = [(log, i) for i, log in enumerate(logs[-100:])]
        self.log_list.options = items
        
        if items:
            # Seleccionar el último (más reciente) para hacer auto-scroll
            self.log_list.value = len(items) - 1