"""
Escena con estadísticas completas.
"""

from asciimatics.widgets import Label, Divider, Layout, Frame, Widget

from .base import BaseScene


class StatsScene(BaseScene):
    def _setup_layout(self):
        layout = Layout([1, 1])
        self.add_layout(layout)

        # Columna izquierda: distribuciones
        left = layout[0]
        left.add_widget(Label("📊 DISTRIBUCIÓN DE CALIDAD"), 0)
        left.add_widget(Divider(), 0)
        self.quality_dist_label = Label("Excelente: --\nBuena: --\nPobre: --")
        left.add_widget(self.quality_dist_label, 0)

        left.add_widget(Label("📡 TECNOLOGÍAS (RAT)"), 1)
        left.add_widget(Divider(), 1)
        self.rat_dist_label = Label("")
        left.add_widget(self.rat_dist_label, 1)

        # Columna derecha: métricas encontradas
        right = layout[1]
        right.add_widget(Label("🔍 MÉTRICAS DETECTADAS"), 0)
        right.add_widget(Divider(), 0)
        self.metrics_label = Label("")
        right.add_widget(self.metrics_label, 0)

        # Footer
        layout.add_widget(Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---"), 2)

        self.fix()

    def refresh(self):
        with self.data.lock:
            stats = self.data.get_stats()
            # Distribución de calidad (deberías tenerla en stats; si no, calcular desde quality_history)
            # Para este ejemplo, simulamos con counts de quality_range
            # Asumimos que en stats tienes 'quality_excellent', etc. Pero por ahora usamos quality_history.
            quality_list = list(self.data.quality_history)
            excellent = sum(1 for q in quality_list if q >= 70)
            good = sum(1 for q in quality_list if 40 <= q < 70)
            poor = sum(1 for q in quality_list if q < 40)
            self.quality_dist_label.text = f"Excelente: {excellent}\nBuena: {good}\nPobre: {poor}"

            # Rat distribution
            rat_text = "\n".join(f"{k}: {v}" for k, v in stats['rat_distribution'].items())
            self.rat_dist_label.text = rat_text if rat_text else "No hay datos"

            # Metrics distribution
            metrics_text = "\n".join(f"{k}: {v}" for k, v in stats['metrics_distribution'].items())
            self.metrics_label.text = metrics_text if metrics_text else "No hay datos"