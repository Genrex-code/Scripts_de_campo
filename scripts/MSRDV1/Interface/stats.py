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

        # Columna izquierda (Índice 0): distribuciones
        main_layout.add_widget(Label("📊 DISTRIBUCIÓN DE CALIDAD"), 0)
        main_layout.add_widget(Divider(), 0)
        # Añadimos height=3 para que los saltos de línea (\n) se vean
        self.quality_dist_label = Label("Excelente: --\nBuena: --\nPobre: --", height=3)
        main_layout.add_widget(self.quality_dist_label, 0)

        # Se corrige el índice a 0 para mantenerlo en la columna izquierda
        main_layout.add_widget(Label("📡 TECNOLOGÍAS (RAT)"), 0)
        main_layout.add_widget(Divider(), 0)
        # Le damos un height de 5 para tener espacio para varias redes
        self.rat_dist_label = Label("", height=5)
        main_layout.add_widget(self.rat_dist_label, 0)

        # Columna derecha (Índice 1): métricas encontradas
        main_layout.add_widget(Label("🔍 MÉTRICAS DETECTADAS"), 1)
        main_layout.add_widget(Divider(), 1)
        # Le damos un height de 10 por si encuentra muchas métricas
        self.metrics_label = Label("", height=10)
        main_layout.add_widget(self.metrics_label, 1)

        # Layout separado para el pie de página
        footer_layout = Layout([1])
        self.add_layout(footer_layout)
        # Corrección del footer
        footer_layout.add_widget(
            Label("--- Teclas: 1 Dashboard | 2 Raw Logs | 3 Alertas | 4 Estadísticas ---"), 0
        )

        self.fix()

    def refresh(self):
        with self.model.lock:
            stats = self.model.get_stats()
            quality_list = list(self.model.quality_history)

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