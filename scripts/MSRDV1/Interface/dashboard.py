"""
Escena principal (dashboard) con métricas clave.
"""

from asciimatics.widgets import Label, Divider, Layout, Frame, Widget

from .base import BaseScene


class DashboardScene(BaseScene):
    def _setup_layout(self):
        # --- Layout para el contenido principal (dos columnas) ---
        main_layout = Layout([1, 1])   # dos columnas de igual ancho
        self.add_layout(main_layout)

        # Columna izquierda: métricas actuales
        left = main_layout[0]          # type: ignore
        left.add_widget(Label("📡 MÉTRICAS ACTUALES"), 0)
        left.add_widget(Divider(), 0)
        self.signal_label = Label("Señal: -- dBm")
        self.quality_label = Label("Calidad: --%")
        self.rat_label = Label("Red: --")
        self.rate_label = Label("Eventos/s: --")
        left.add_widget(self.signal_label, 0)
        left.add_widget(self.quality_label, 0)
        left.add_widget(self.rat_label, 0)
        left.add_widget(self.rate_label, 0)

        # Columna derecha: estadísticas rápidas
        right = main_layout[1]         # type: ignore
        right.add_widget(Label("📈 ESTADÍSTICAS"), 0)
        right.add_widget(Divider(), 0)
        self.total_label = Label("Total eventos: --")
        self.avg_quality_label = Label("Calidad promedio: --%")
        self.max_quality_label = Label("Calidad máx: --%")
        self.min_quality_label = Label("Calidad mín: --%")
        right.add_widget(self.total_label, 0)
        right.add_widget(self.avg_quality_label, 0)
        right.add_widget(self.max_quality_label, 0)
        right.add_widget(self.min_quality_label, 0)

        # --- Layout para el pie de página ---
        footer_layout = Layout([1])    # una sola columna
        self.add_layout(footer_layout)
        footer = footer_layout[0]      # type: ignore
        footer.add_widget(Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---"))

        self.fix()

    def refresh(self):
        # Acceder al modelo compartido (self.data es SharedData)
        with self.data.lock:          # type: ignore
            summary = self.data.current_summary   # type: ignore
            stats = self.data.get_stats()         # type: ignore

        # Actualizar labels de métricas actuales
        self.signal_label.text = f"Señal: {summary.get('avg_signal', 'N/A')} dBm"
        self.quality_label.text = f"Calidad: {summary.get('avg_quality', 0):.1f}%"
        self.rat_label.text = f"Red: {summary.get('common_rat', 'N/A')}"
        self.rate_label.text = f"Eventos/s: {summary.get('event_rate', 0)}"

        # Actualizar labels de estadísticas
        self.total_label.text = f"Total eventos: {stats['total_events']}"
        self.avg_quality_label.text = f"Calidad promedio: {stats['quality_avg']:.1f}%"
        self.max_quality_label.text = f"Calidad máx: {stats['quality_max']:.1f}%"
        self.min_quality_label.text = f"Calidad mín: {stats['quality_min']:.1f}%"