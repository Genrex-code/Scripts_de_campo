"""
Escena con estadísticas completas.
"""

from asciimatics.widgets import Label, Divider, Layout

from .base import BaseScene


class StatsScene(BaseScene):
    def _setup_layout(self):
        # Layout principal con dos columnas
        main_layout = Layout([1, 1])
        self.add_layout(main_layout)

        # Columna izquierda: distribuciones
        left = main_layout[0]                     # type: ignore
        left.add_widget(Label("📊 DISTRIBUCIÓN DE CALIDAD"), 0)
        left.add_widget(Divider(), 0)
        self.quality_dist_label = Label("Excelente: --\nBuena: --\nPobre: --")
        left.add_widget(self.quality_dist_label, 0)

        left.add_widget(Label("📡 TECNOLOGÍAS (RAT)"), 1)
        left.add_widget(Divider(), 1)
        self.rat_dist_label = Label("")
        left.add_widget(self.rat_dist_label, 1)

        # Columna derecha: métricas encontradas
        right = main_layout[1]                    # type: ignore
        right.add_widget(Label("🔍 MÉTRICAS DETECTADAS"), 0)
        right.add_widget(Divider(), 0)
        self.metrics_label = Label("")
        right.add_widget(self.metrics_label, 0)

        # Layout separado para el pie de página
        footer_layout = Layout([1])
        self.add_layout(footer_layout)
        footer_layout[0].add_widget(               # type: ignore
            Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---")
        )

        self.fix()

    def refresh(self):
        with self.model.lock:                       # type: ignore
            stats = self.model.get_stats()          # type: ignore
            quality_list = list(self.model.quality_history)   # type: ignore

        # Distribución de calidad
        excellent = sum(1 for q in quality_list if q >= 70)
        good = sum(1 for q in quality_list if 40 <= q < 70)
        poor = sum(1 for q in quality_list if q < 40)
        self.quality_dist_label.text = f"Excelente: {excellent}\nBuena: {good}\nPobre: {poor}"

        # Distribución de tecnologías (RAT)
        rat_text = "\n".join(f"{k}: {v}" for k, v in stats.get('rat_distribution', {}).items())
        self.rat_dist_label.text = rat_text if rat_text else "No hay datos"

        # Distribución de métricas detectadas
        metrics_text = "\n".join(f"{k}: {v}" for k, v in stats.get('metrics_distribution', {}).items())
        self.metrics_label.text = metrics_text if metrics_text else "No hay datos"