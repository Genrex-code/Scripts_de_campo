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
        main_layout[0].add_widget(self.log_list, 0)   # type: ignore

        # Layout para el pie de página
        footer_layout = Layout([1])
        self.add_layout(footer_layout)
        footer = footer_layout[0]                     # type: ignore
        footer.add_widget(Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---"))

        self.fix()

    def refresh(self):
        # Acceder al modelo compartido
        with self.data.lock:                          # type: ignore
            logs = list(self.data.raw_logs)           # type: ignore

        # Preparar lista de tuplas (valor, índice) para ListBox
        # Mostrar solo las últimas 100 líneas
        items = [(log, i) for i, log in enumerate(logs[-100:])]
        self.log_list.options = items
        if items:
            self.log_list.value = len(items) - 1      # seleccionar el último (más reciente)