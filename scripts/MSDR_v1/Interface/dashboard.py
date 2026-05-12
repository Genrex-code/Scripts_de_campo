"""
Escena principal (dashboard) con métricas clave.
"""

from asciimatics.widgets import Label, Divider, Layout
from .base import BaseScene


class DashboardScene(BaseScene):
    def _setup_layout(self):
        # --- Layout para el contenido principal (dos columnas) ---
        main_layout = Layout([1, 1])   # dos columnas de igual ancho
        self.add_layout(main_layout)

        # Columna izquierda (Índice 0): métricas actuales
        main_layout.add_widget(Label("📡 MÉTRICAS ACTUALES"), 0)
        main_layout.add_widget(Divider(), 0)
        self.signal_label = Label("Señal: -- dBm")
        self.quality_label = Label("Calidad: --%")
        self.rat_label = Label("Red: --")
        self.rate_label = Label("Eventos/s: --")
        
        main_layout.add_widget(self.signal_label, 0)
        main_layout.add_widget(self.quality_label, 0)
        main_layout.add_widget(self.rat_label, 0)
        main_layout.add_widget(self.rate_label, 0)

        # Columna derecha (Índice 1): estadísticas rápidas
        main_layout.add_widget(Label("📈 ESTADÍSTICAS"), 1)
        main_layout.add_widget(Divider(), 1)
        self.total_label = Label("Total eventos: --")
        self.avg_quality_label = Label("Calidad promedio: --%")
        self.max_quality_label = Label("Calidad máx: --%")
        self.min_quality_label = Label("Calidad mín: --%")
        
        main_layout.add_widget(self.total_label, 1)
        main_layout.add_widget(self.avg_quality_label, 1)
        main_layout.add_widget(self.max_quality_label, 1)
        main_layout.add_widget(self.min_quality_label, 1)

        # --- Layout para el pie de página ---
        footer_layout = Layout([1])
        self.add_layout(footer_layout)
        # Aquí también se añade indicando la columna 0
        footer_layout.add_widget(
            Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---"), 0
        )

        self.fix()