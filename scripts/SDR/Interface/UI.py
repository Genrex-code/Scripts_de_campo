from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.console import Console, Group
from rich.align import Align
from rich import box
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
import threading
import time
import sys
import os

# Intentar importar plotext con manejo de error
try:
    import plotext as plt
    PLOTEXT_AVAILABLE = True
except ImportError:
    PLOTEXT_AVAILABLE = False
    print("⚠️ plotext no instalado. Las gráficas no estarán disponibles.")
    print("   Instálalo con: pip install plotext")

# IMPORTACIONES CORREGIDAS - Estas son las que faltaban
from abc import ABC, abstractmethod

# Definir ObserverPriority si no está importado de otro lado
class ObserverPriority:
    """Prioridades para los observadores"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    DEBUG = 4

# Definir SignalObserver si no está importado
class SignalObserver(ABC):
    """Clase base para observadores de señal"""
    
    def __init__(self, name: Optional[str] = None, priority: ObserverPriority = ObserverPriority.NORMAL):
        self.name = name or self.__class__.__name__
        self.priority = priority
        self.logger = logging.getLogger(f"Observer.{self.name}")
        self.is_active = True
        self.error_count = 0
    
    @abstractmethod
    def update(self, refined_data: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
        pass
    
    def on_error(self, error: Exception) -> None:
        self.error_count += 1
        self.logger.error(f"Error en observador: {error}")
        if self.error_count > 10:
            self.logger.critical(f"Demasiados errores ({self.error_count}) en {self.name}, desactivando...")
            self.is_active = False
    
    def on_attach(self, pipeline) -> None:
        self.logger.info(f"Observador {self.name} conectado al pipeline")
    
    def on_detach(self) -> None:
        self.logger.info(f"Observador {self.name} desconectado")

# Configuración de logging
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

console = Console()

class SignalMetricsDisplay:
    """Clase auxiliar para gestionar las métricas históricas"""
    
    def __init__(self, max_history: int = 100):
        self.max_history = max_history
        self.dbm_history = deque(maxlen=max_history)
        self.quality_history = deque(maxlen=max_history)
        self.rsrq_history = deque(maxlen=max_history)
        self.snr_history = deque(maxlen=max_history)
        self.timestamps = deque(maxlen=max_history)
        
    def add_reading(self, dbm: Optional[int], quality: float, rsrq: Optional[int], snr: Optional[int]):
        """Añade una nueva lectura al historial"""
        timestamp = datetime.now()
        self.timestamps.append(timestamp)
        self.dbm_history.append(dbm if dbm is not None else -140)
        self.quality_history.append(quality)
        self.rsrq_history.append(rsrq if rsrq is not None else -20)
        self.snr_history.append(snr if snr is not None else 0)
    
    def get_time_range(self) -> Tuple[datetime, datetime]:
        """Obtiene el rango de tiempo para las gráficas"""
        if not self.timestamps:
            return (datetime.now() - timedelta(minutes=5), datetime.now())
        
        start_time = self.timestamps[0]
        end_time = self.timestamps[-1]
        
        # Si hay pocos datos, mostrar ventana más amplia
        if len(self.timestamps) < 10:
            start_time = end_time - timedelta(minutes=2)
        
        return start_time, end_time


class TUIObserver(SignalObserver):
    """
    Interfaz de Terminal para monitoreo en tiempo real con:
    - Tabla de métricas actuales
    - Gráficas de evolución temporal
    - Alertas visuales
    - Estadísticas en tiempo real
    """
    
    def __init__(self, refresh_rate: float = 1.0, max_history: int = 100):
        """
        Args:
            refresh_rate: Tasa de actualización en segundos
            max_history: Número máximo de lecturas históricas
        """
        # CORRECCIÓN: Usar ObserverPriority.LOW correctamente
        super().__init__(name="TUI", priority=ObserverPriority.LOW)
        
        self.refresh_rate = refresh_rate
        self.max_history = max_history
        
        # Datos históricos
        self.metrics_history = SignalMetricsDisplay(max_history)
        
        # Últimas métricas recibidas
        self.last_metrics = {
            "avg_dbm": None,
            "avg_rsrp": None,
            "avg_rsrq": None,
            "avg_quality": 0,
            "common_rat": "N/A",
            "total_alerts": 0,
            "security_issues": 0,
            "last_alert": None,
            "event_rate": 0
        }
        
        # Control de refresco
        self.last_update_time = datetime.now()
        self.events_in_window = 0
        self.window_start = datetime.now()
        
        # Layout de la interfaz
        self.layout = Layout()
        self._setup_layout()
        
        # Estado de la gráfica
        self.plot_initialized = False
        
        # Lock para thread-safety
        self._lock = threading.Lock()
        
        # Live display
        self.live = None
        self._tui_thread = None
        
    def _setup_layout(self):
        """Configura la estructura de paneles de la interfaz"""
        # Dividir en 3 secciones principales
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=3)
        )
        
        # Dividir la sección principal en dos columnas
        self.layout["main"].split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=3)
        )
        
        # Dividir la columna izquierda en tres filas
        self.layout["left"].split(
            Layout(name="current_metrics", size=10),
            Layout(name="stats", size=8),
            Layout(name="alerts", size=5)
        )
    
    def _create_header(self) -> Panel:
        """Crea el panel superior con título y estado"""
        header_text = Text()
        header_text.append("📡 MONITOR DE SEÑAL MÓVIL\n", style="bold cyan")
        header_text.append("Snapdragon Modem - Radio Logs", style="dim")
        
        # Indicador de estado en vivo
        status = Text(" ● EN VIVO ", style="bold green blink")
        
        # Hora actual
        current_time = datetime.now().strftime("%H:%M:%S")
        time_text = Text(f"🕐 {current_time}", style="bold yellow")
        
        # Combinar elementos
        content = Group(
            Align.center(header_text),
            Align.center(Group(status, "   ", time_text))
        )
        
        return Panel(content, box=box.HEAVY, border_style="cyan")
    
    def _create_metrics_table(self) -> Panel:
        """Crea la tabla con las métricas actuales"""
        table = Table(show_header=False, box=box.MINIMAL, padding=(0, 1))
        table.add_column("Métrica", style="bold cyan")
        table.add_column("Valor", justify="right")
        table.add_column("Estado", justify="right")
        
        with self._lock:
            # Intensidad de señal
            signal = self.last_metrics.get("avg_dbm")
            if signal is not None:
                if signal <= -110:
                    signal_status = "🔴 CRÍTICA"
                    signal_color = "red"
                elif signal <= -85:
                    signal_status = "🟡 DÉBIL"
                    signal_color = "yellow"
                else:
                    signal_status = "🟢 BUENA"
                    signal_color = "green"
                
                table.add_row("📶 Señal", f"{signal} dBm", f"[{signal_color}]{signal_status}[/{signal_color}]")
            else:
                table.add_row("📶 Señal", "N/A", "⚪ SIN DATOS")
            
            # RSRQ (Calidad)
            rsrq = self.last_metrics.get("avg_rsrq")
            if rsrq is not None:
                if rsrq <= -18:
                    rsrq_status = "🔴 MALA"
                    rsrq_color = "red"
                elif rsrq <= -12:
                    rsrq_status = "🟡 ACEPTABLE"
                    rsrq_color = "yellow"
                else:
                    rsrq_status = "🟢 EXCELENTE"
                    rsrq_color = "green"
                
                table.add_row("📊 Calidad", f"{rsrq}", f"[{rsrq_color}]{rsrq_status}[/{rsrq_color}]")
            
            # Calidad general
            quality = self.last_metrics.get("avg_quality", 0)
            if quality >= 70:
                quality_status = "🟢 EXCELENTE"
                quality_color = "green"
            elif quality >= 40:
                quality_status = "🟡 ACEPTABLE"
                quality_color = "yellow"
            else:
                quality_status = "🔴 CRÍTICA"
                quality_color = "red"
            
            table.add_row("⭐ Score", f"{quality:.1f}%", f"[{quality_color}]{quality_status}[/{quality_color}]")
            
            # Tipo de Red
            rat = self.last_metrics.get("common_rat", "N/A")
            rat_icons = {"LTE": "📶 4G", "NR": "🚀 5G", "WCDMA": "📱 3G", "GSM": "📞 2G"}
            rat_display = rat_icons.get(rat, f"❓ {rat}")
            table.add_row("📡 Red", rat_display, "")
            
            # Tasa de eventos
            rate = self.last_metrics.get("event_rate", 0)
            table.add_row("⚡ Eventos/s", f"{rate:.1f}", "")
        
        return Panel(table, title="📊 MÉTRICAS ACTUALES", border_style="green", box=box.ROUNDED)
    
    def _create_stats_panel(self) -> Panel:
        """Panel con estadísticas agregadas"""
        stats_lines = []
        
        with self._lock:
            # Historial de datos
            total_readings = len(self.metrics_history.dbm_history)
            stats_lines.append(f"📈 Lecturas totales: {total_readings}")
            
            # Promedios históricos
            if self.metrics_history.dbm_history:
                avg_dbm = sum(self.metrics_history.dbm_history) / len(self.metrics_history.dbm_history)
                stats_lines.append(f"📊 Señal promedio: {avg_dbm:.1f} dBm")
            
            if self.metrics_history.quality_history:
                avg_quality = sum(self.metrics_history.quality_history) / len(self.metrics_history.quality_history)
                stats_lines.append(f"⭐ Calidad promedio: {avg_quality:.1f}%")
            
            # Alertas
            alerts = self.last_metrics.get("total_alerts", 0)
            security = self.last_metrics.get("security_issues", 0)
            stats_lines.append(f"⚠️ Alertas totales: {alerts}")
            stats_lines.append(f"🔒 Incidentes seguridad: {security}")
        
        stats_text = Text("\n".join(stats_lines))
        return Panel(stats_text, title="📈 ESTADÍSTICAS", border_style="blue", box=box.ROUNDED)
    
    def _create_alerts_panel(self) -> Panel:
        """Panel que muestra las últimas alertas"""
        alerts_text = Text()
        
        with self._lock:
            last_alert = self.last_metrics.get("last_alert")
            security_issues = self.last_metrics.get("security_issues", 0)
            
            if security_issues > 0:
                alerts_text.append("🚨 ALERTA DE SEGURIDAD\n", style="bold red")
                alerts_text.append("Cifrado desactivado - Posible celda falsa\n", style="red")
            
            if last_alert:
                alerts_text.append(last_alert, style="yellow")
            else:
                alerts_text.append("✅ Sin alertas activas", style="green")
        
        return Panel(alerts_text, title="⚠️ ALERTAS", border_style="red", box=box.ROUNDED)
    
    def _create_plot(self) -> Panel:
        """Crea la gráfica de evolución temporal usando plotext"""
        if not PLOTEXT_AVAILABLE:
            return Panel("⚠️ plotext no instalado\n\nInstálalo con:\npip install plotext", 
                        title="📈 GRÁFICA NO DISPONIBLE", 
                        border_style="red")
        
        with self._lock:
            if len(self.metrics_history.dbm_history) < 2:
                return Panel("⏳ Esperando datos para generar gráfica...", 
                            title="📈 EVOLUCIÓN DE SEÑAL", 
                            border_style="yellow")
            
            # Preparar datos
            dbm_data = list(self.metrics_history.dbm_history)[-50:]
            quality_data = list(self.metrics_history.quality_history)[-50:]
            x_data = list(range(len(dbm_data)))
            
            # CORRECCIÓN: Usar plt correctamente
            plt.clear_figure()  # Usar clear_figure en lugar de clf
            plt.plot_size(80, 20)
            
            # Graficar
            plt.plot(x_data, dbm_data, label="Señal (dBm)", color="cyan", marker="dot")
            plt.plot(x_data, quality_data, label="Calidad (%)", color="yellow", marker="dot")
            
            # Configurar
            plt.title("Evolución Temporal")
            plt.xlabel("Muestras (últimas 50)")
            plt.ylabel("Valor")
            plt.grid(True)
            
            # CORRECCIÓN: Usar show_legend() en lugar de legend()
            plt.show_legend()
            
            # Ajustar límites
            all_values = dbm_data + quality_data
            if all_values:
                y_min = min(min(dbm_data, default=-140), min(quality_data, default=0)) - 10
                y_max = max(max(dbm_data, default=-40), max(quality_data, default=100)) + 10
                plt.ylim(y_min, y_max)
            
            # Obtener el gráfico como string
            plot_str = plt.build()
        
        return Panel(plot_str, title="📈 EVOLUCIÓN DE SEÑAL", border_style="magenta", box=box.ROUNDED)
    
    def _create_footer(self) -> Panel:
        """Panel inferior con información de control"""
        footer_text = Text()
        footer_text.append("📱 Ctrl+C ", style="bold yellow")
        footer_text.append("para salir • ")
        footer_text.append("🔄 Actualización en tiempo real", style="dim")
        
        return Panel(Align.center(footer_text), box=box.MINIMAL)
    
    # CORRECCIÓN: Cambiar tipo de retorno a Layout
    def _update_display(self) -> Layout:
        """Actualiza todos los paneles y retorna el layout completo"""
        # Actualizar cada panel
        self.layout["header"].update(self._create_header())
        self.layout["current_metrics"].update(self._create_metrics_table())
        self.layout["stats"].update(self._create_stats_panel())
        self.layout["alerts"].update(self._create_alerts_panel())
        self.layout["right"].update(self._create_plot())
        self.layout["footer"].update(self._create_footer())
        
        return self.layout
    
    def update(self, refined_data: List[Dict], summary: Dict):
        """Actualiza la interfaz con nuevos datos"""
        if not refined_data:
            return
        
        # Calcular tasa de eventos
        now = datetime.now()
        self.events_in_window += len(refined_data)
        if (now - self.window_start).total_seconds() >= 1.0:
            self.last_metrics["event_rate"] = self.events_in_window
            self.events_in_window = 0
            self.window_start = now
        
        with self._lock:
            # Actualizar últimas métricas
            self.last_metrics.update({
                "avg_dbm": summary.get("avg_signal"),
                "avg_rsrp": summary.get("avg_rsrp"),
                "avg_rsrq": summary.get("avg_rsrq"),
                "avg_quality": summary.get("avg_quality", 0),
                "common_rat": summary.get("common_rat", "N/A"),
                "total_alerts": summary.get("total_alerts", 0),
                "security_issues": summary.get("security_issues", 0)
            })
            
            # Actualizar última alerta
            if summary.get("total_alerts", 0) > 0:
                last_alert_time = datetime.now().strftime("%H:%M:%S")
                alert_type = "🔒 Seguridad" if summary.get("security_issues", 0) > 0 else "⚠️ Calidad"
                self.last_metrics["last_alert"] = f"[{last_alert_time}] {alert_type}: Señal débil/Inestable"
            
            # Añadir al historial
            if "avg_signal" in summary and summary["avg_signal"] is not None:
                self.metrics_history.add_reading(
                    dbm=summary["avg_signal"],
                    quality=summary.get("avg_quality", 0),
                    rsrq=summary.get("avg_rsrq"),
                    snr=summary.get("avg_snr")
                )
    
    def run(self):
        """Ejecuta la interfaz en modo live"""
        try:
            with Live(self._update_display(), refresh_per_second=1/self.refresh_rate, screen=True) as live:
                self.live = live
                
                while self.is_active:
                    time.sleep(self.refresh_rate)
                    live.update(self._update_display())
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.logger.error(f"Error en TUI: {e}")
    
    def on_attach(self, pipeline):
        """Cuando se conecta al pipeline, iniciar la interfaz"""
        super().on_attach(pipeline)
        self._tui_thread = threading.Thread(target=self.run, daemon=True)
        self._tui_thread.start()
        self.logger.info("Interfaz TUI iniciada")


class SimplifiedTUIObserver(SignalObserver):
    """Versión simplificada con solo métricas básicas (sin gráficas)"""
    
    def __init__(self, refresh_rate: float = 1.0):
        # CORRECCIÓN: Usar ObserverPriority.LOW correctamente
        super().__init__(name="SimpleTUI", priority=ObserverPriority.LOW)
        self.refresh_rate = refresh_rate
        self.last_summary = {}
        self._lock = threading.Lock()
        self._display_thread = None
        
    def update(self, refined_data: List[Dict], summary: Dict):
        with self._lock:
            self.last_summary = summary
    
    def _display(self):
        """Muestra una interfaz simple en consola"""
        with self._lock:
            summary = self.last_summary.copy()
        
        # Limpiar pantalla
        os.system('cls' if os.name == 'nt' else 'clear')
        
        # Título
        print("\n" + "="*60)
        print("📡 MONITOR DE SEÑAL MÓVIL".center(60))
        print("="*60)
        
        # Métricas principales
        signal = summary.get("avg_signal", "N/A")
        quality = summary.get("avg_quality", 0)
        rat = summary.get("common_rat", "N/A")
        
        # Barra de calidad
        bar_length = 30
        filled = int(bar_length * quality / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        # Color según calidad
        if quality >= 70:
            color = "\033[92m"  # Verde
        elif quality >= 40:
            color = "\033[93m"  # Amarillo
        else:
            color = "\033[91m"  # Rojo
        
        print(f"\n📶 Señal: \033[1m{signal} dBm\033[0m")
        print(f"⭐ Calidad: {color}{quality:.1f}%\033[0m")
        print(f"   [{color}{bar}\033[0m]")
        print(f"📡 Red: \033[1m{rat}\033[0m")
        
        # Alertas
        if summary.get("security_issues", 0) > 0:
            print("\n\033[91m🚨 ALERTA DE SEGURIDAD: Cifrado desactivado!\033[0m")
        elif quality < 40:
            print("\n\033[93m⚠️ ADVERTENCIA: Señal débil o inestable\033[0m")
        
        # Estadísticas
        print(f"\n\033[90m📊 Lecturas: {summary.get('total_events', 0)}\033[0m")
        print(f"\033[90m🕐 Última actualización: {datetime.now().strftime('%H:%M:%S')}\033[0m")
        
        print("\n" + "="*60)
        print("Ctrl+C para salir".center(60))
    
    def run(self):
        """Ejecuta el display simple"""
        try:
            while self.is_active:
                self._display()
                time.sleep(self.refresh_rate)
        except KeyboardInterrupt:
            pass
    
    def on_attach(self, pipeline):
        super().on_attach(pipeline)
        self._display_thread = threading.Thread(target=self.run, daemon=True)
        self._display_thread.start()
        self.logger.info("Interfaz TUI simplificada iniciada")


# --- Ejemplo de uso ---
if __name__ == "__main__":
    import random
    
    class MockPipeline:
        def __init__(self):
            self.observers = []
        
        def subscribe(self, observer):
            self.observers.append(observer)
            observer.on_attach(self)
        
        def send_mock_data(self):
            """Envía datos de prueba"""
            while True:
                mock_summary = {
                    "avg_signal": random.randint(-120, -60),
                    "avg_quality": random.randint(20, 95),
                    "common_rat": random.choice(["LTE", "NR", "WCDMA"]),
                    "total_alerts": random.randint(0, 2),
                    "security_issues": random.randint(0, 1),
                    "total_events": random.randint(100, 1000)
                }
                
                mock_data = [{
                    "metrics": {
                        "dbm": mock_summary["avg_signal"],
                        "rsrq": random.randint(-20, -3),
                        "rat": mock_summary["common_rat"]
                    },
                    "signal_quality": mock_summary["avg_quality"]
                }]
                
                for observer in self.observers:
                    observer.update(mock_data, mock_summary)
                
                time.sleep(0.5)
    
    print("Selecciona interfaz:")
    print("1. Completa (con gráficas)")
    print("2. Simple")
    
    choice = input("Opción: ").strip()
    
    if choice == "1":
        tui = TUIObserver(refresh_rate=0.5, max_history=100)
    else:
        tui = SimplifiedTUIObserver(refresh_rate=1.0)
    
    mock_pipeline = MockPipeline()
    mock_pipeline.subscribe(tui)
    
    print("\n✅ Interfaz iniciada. Presiona Ctrl+C para salir...\n")
    
    try:
        mock_pipeline.send_mock_data()
    except KeyboardInterrupt:
        print("\n\n👋 Saliendo...")