"""
UI Module – Interfaz de Terminal para monitoreo de señal móvil.
Modo completo (requiere rich y plotext) y modo simple (fallback automático).
"""

import sys
import os
import threading
import time
import logging
from datetime import datetime
from collections import deque
from typing import List, Dict, Any, Optional, Tuple

# Configuración de logging silencioso para la UI
logging.basicConfig(level=logging.WARNING)

# ============================================================================
# VERIFICACIÓN DE DEPENDENCIAS
# ============================================================================

RICH_AVAILABLE = False
PLOTEXT_AVAILABLE = False

try:
    from rich.live import Live
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.console import Console, Group
    from rich.align import Align
    from rich import box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    console = None
    print("⚠️ rich no instalado. Usando modo texto simple.")
    print("   Para mejor experiencia: pip install rich")

try:
    import plotext as plt
    PLOTEXT_AVAILABLE = True
except ImportError:
    print("⚠️ plotext no instalado. Gráficas no disponibles.")
    print("   Para gráficas: pip install plotext")

# Importar clases base del pipeline
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline import SignalObserver, ObserverPriority
except ImportError:
    # Fallback: definir localmente si no se puede importar
    from enum import Enum
    class ObserverPriority(Enum):
        CRITICAL = 0
        HIGH = 1
        NORMAL = 2
        LOW = 3
        DEBUG = 4

    from abc import ABC, abstractmethod
    class SignalObserver(ABC):
        def __init__(self, name=None, priority=ObserverPriority.NORMAL):
            self.name = name or self.__class__.__name__
            self.priority = priority
            self.is_active = True
            self.logger = logging.getLogger(f"Observer.{self.name}")

        @abstractmethod
        def update(self, refined_data, summary):
            pass


# ============================================================================
# CLASE AUXILIAR PARA HISTORIAL DE MÉTRICAS
# ============================================================================

class MetricsHistory:
    """Almacena historial de métricas para gráficas y estadísticas."""
    
    def __init__(self, max_history: int = 100):
        self.max_history = max_history
        self.dbm_history = deque(maxlen=max_history)
        self.quality_history = deque(maxlen=max_history)
        self.rsrq_history = deque(maxlen=max_history)
        self.snr_history = deque(maxlen=max_history)
        self.timestamps = deque(maxlen=max_history)
    
    def add_reading(self, dbm: Optional[int], quality: float, 
                    rsrq: Optional[int], snr: Optional[int]) -> None:
        """Añade una nueva lectura al historial."""
        self.timestamps.append(datetime.now())
        self.dbm_history.append(dbm if dbm is not None else -140)
        self.quality_history.append(quality)
        self.rsrq_history.append(rsrq if rsrq is not None else -20)
        self.snr_history.append(snr if snr is not None else 0)
    
    def get_last_n(self, n: int = 50) -> Tuple[List, List, List, List]:
        """Obtiene las últimas n lecturas para gráficas."""
        return (
            list(self.dbm_history)[-n:],
            list(self.quality_history)[-n:],
            list(self.rsrq_history)[-n:],
            list(self.snr_history)[-n:]
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas del historial."""
        return {
            "total_readings": len(self.dbm_history),
            "avg_dbm": sum(self.dbm_history) / len(self.dbm_history) if self.dbm_history else None,
            "avg_quality": sum(self.quality_history) / len(self.quality_history) if self.quality_history else 0,
            "max_quality": max(self.quality_history) if self.quality_history else 0,
            "min_quality": min(self.quality_history) if self.quality_history else 0,
        }


# ============================================================================
# TUI COMPLETA (MODO GRÁFICO)
# ============================================================================

class FullTUIObserver(SignalObserver):
    """Interfaz completa con paneles, tablas y gráficas."""
    
    def __init__(self, refresh_rate: float = 1.0, max_history: int = 100):
        super().__init__(name="FullTUI", priority=ObserverPriority.LOW)
        
        self.refresh_rate = refresh_rate
        self.max_history = max_history
        
        # Historial de métricas
        self.history = MetricsHistory(max_history)
        
        # Últimas métricas recibidas
        self.current = {
            "signal": None,
            "quality": 0,
            "rsrq": None,
            "snr": None,
            "rat": "N/A",
            "alerts": 0,
            "security": 0,
            "event_rate": 0
        }
        
        # Estado interno
        self._lock = threading.Lock()
        self._events_in_window = 0
        self._window_start = datetime.now()
        self._running = True
        self._tui_thread = None
        self._live = None
        
        # Layout (solo si rich está disponible)
        self._layout = None
        if RICH_AVAILABLE:
            self._setup_layout()
        
        self.logger = logging.getLogger("FullTUI")
        self.logger.info(f"TUI completa inicializada (refresh={refresh_rate}s)")
    
    def _setup_layout(self):
        """Configura la estructura de paneles."""
        if not RICH_AVAILABLE:
            return
        
        self._layout = Layout()
        
        # Dividir en 3 secciones verticales
        self._layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=3)
        )
        
        # Dividir sección principal en dos columnas
        self._layout["main"].split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=3)
        )
        
        # Dividir columna izquierda en tres filas
        self._layout["left"].split(
            Layout(name="metrics", size=10),
            Layout(name="stats", size=8),
            Layout(name="alerts", size=5)
        )
    
    def _create_header(self):
        """Crea el panel superior."""
        if not RICH_AVAILABLE:
            return Panel("")
        
        header = Text()
        header.append("📡 MONITOR DE SEÑAL MÓVIL\n", style="bold cyan")
        header.append("Snapdragon Modem - Radio Logs", style="dim")
        
        status = Text(" ● EN VIVO ", style="bold green blink")
        time_str = Text(f"🕐 {datetime.now().strftime('%H:%M:%S')}", style="bold yellow")
        
        content = Group(Align.center(header), Align.center(Group(status, "   ", time_str)))
        
        return Panel(content, box=box.HEAVY, border_style="cyan")
    
    def _create_metrics_panel(self):
        """Crea la tabla de métricas actuales."""
        if not RICH_AVAILABLE:
            return Panel("")
        
        table = Table(show_header=False, box=box.MINIMAL, padding=(0, 1))
        table.add_column("Métrica", style="bold cyan")
        table.add_column("Valor", justify="right")
        table.add_column("Estado", justify="right")
        
        with self._lock:
            # Señal
            signal = self.current.get("signal")
            if signal is not None:
                if signal <= -110:
                    status = "🔴 CRÍTICA"
                    color = "red"
                elif signal <= -85:
                    status = "🟡 DÉBIL"
                    color = "yellow"
                else:
                    status = "🟢 BUENA"
                    color = "green"
                table.add_row("📶 Señal", f"{signal} dBm", f"[{color}]{status}[/{color}]")
            else:
                table.add_row("📶 Señal", "N/A", "⚪ SIN DATOS")
            
            # Calidad
            quality = self.current.get("quality", 0)
            if quality >= 70:
                status = "🟢 EXCELENTE"
                color = "green"
            elif quality >= 40:
                status = "🟡 ACEPTABLE"
                color = "yellow"
            else:
                status = "🔴 CRÍTICA"
                color = "red"
            table.add_row("⭐ Score", f"{quality:.1f}%", f"[{color}]{status}[/{color}]")
            
            # Red
            rat = self.current.get("rat", "N/A")
            icons = {"LTE": "📶 4G", "NR": "🚀 5G", "WCDMA": "📱 3G", "GSM": "📞 2G"}
            table.add_row("📡 Red", icons.get(rat, rat), "")
            
            # Tasa de eventos
            rate = self.current.get("event_rate", 0)
            table.add_row("⚡ Eventos/s", f"{rate:.1f}", "")
        
        return Panel(table, title="📊 MÉTRICAS ACTUALES", border_style="green", box=box.ROUNDED)
    
    def _create_stats_panel(self):
        """Crea el panel de estadísticas."""
        if not RICH_AVAILABLE:
            return Panel("")
        
        stats = self.history.get_stats()
        
        lines = [
            f"📈 Lecturas: {stats['total_readings']}",
            f"📊 Señal promedio: {stats['avg_dbm'] or 'N/A'} dBm",
            f"⭐ Calidad promedio: {stats['avg_quality']:.1f}%",
            f"📈 Calidad máx: {stats['max_quality']:.1f}%",
            f"📉 Calidad mín: {stats['min_quality']:.1f}%"
        ]
        
        content = Text("\n".join(lines))
        return Panel(content, title="📈 ESTADÍSTICAS", border_style="blue", box=box.ROUNDED)
    
    def _create_alerts_panel(self):
        """Crea el panel de alertas."""
        if not RICH_AVAILABLE:
            return Panel("")
        
        with self._lock:
            security = self.current.get("security", 0)
            alerts = self.current.get("alerts", 0)
        
        content = Text()
        if security > 0:
            content.append("🚨 ALERTA DE SEGURIDAD\n", style="bold red")
            content.append("Cifrado desactivado\n", style="red")
        if alerts > 0:
            content.append(f"⚠️ {alerts} alertas activas", style="yellow")
        else:
            content.append("✅ Sin alertas activas", style="green")
        
        return Panel(content, title="⚠️ ALERTAS", border_style="red", box=box.ROUNDED)
    
    def _create_plot_panel(self):
        """Crea el panel con la gráfica."""
        if not RICH_AVAILABLE:
            return Panel("")
        
        if not PLOTEXT_AVAILABLE:
            return Panel(
                "⚠️ plotext no instalado\n\npip install plotext",
                title="📈 GRÁFICA NO DISPONIBLE",
                border_style="red"
            )
        
        with self._lock:
            dbm_data, quality_data, _, _ = self.history.get_last_n(50)
        
        if len(dbm_data) < 2:
            return Panel(
                "⏳ Esperando datos...",
                title="📈 EVOLUCIÓN DE SEÑAL",
                border_style="yellow"
            )
        
        try:
            plt.clear_figure()
            plt.plot_size(80, 18)
            
            x = list(range(len(dbm_data)))
            plt.plot(x, dbm_data, label="Señal (dBm)", color="cyan", marker="dot")
            plt.plot(x, quality_data, label="Calidad (%)", color="yellow", marker="dot")
            
            plt.title("Evolución Temporal")
            plt.xlabel("Muestras (últimas 50)")
            plt.ylabel("Valor")
            plt.grid(True)
            
            # Intentar mostrar leyenda (compatible con versiones de plotext)
            try:
                plt.show_legend()
            except AttributeError:
                try:
                    plt.legend()
                except AttributeError:
                    pass
            
            # Ajustar límites
            all_vals = dbm_data + quality_data
            if all_vals:
                y_min = min(min(dbm_data, default=-140), min(quality_data, default=0)) - 10
                y_max = max(max(dbm_data, default=-40), max(quality_data, default=100)) + 10
                plt.ylim(y_min, y_max)
            
            plot_str = plt.build()
            
        except Exception as e:
            plot_str = f"Error generando gráfica: {e}"
        
        return Panel(plot_str, title="📈 EVOLUCIÓN DE SEÑAL", border_style="magenta", box=box.ROUNDED)
    
    def _create_footer(self):
        """Crea el panel inferior."""
        if not RICH_AVAILABLE:
            return Panel("")
        
        text = Text()
        text.append("📱 Ctrl+C ", style="bold yellow")
        text.append("para salir • ")
        text.append("🔄 Actualización en tiempo real", style="dim")
        
        return Panel(Align.center(text), box=box.MINIMAL)
    
    def _render_layout(self):
        """Renderiza el layout completo."""
        if not RICH_AVAILABLE:
            return Layout()
        
        self._layout["header"].update(self._create_header())
        self._layout["metrics"].update(self._create_metrics_panel())
        self._layout["stats"].update(self._create_stats_panel())
        self._layout["alerts"].update(self._create_alerts_panel())
        self._layout["right"].update(self._create_plot_panel())
        self._layout["footer"].update(self._create_footer())
        
        return self._layout
    
    def update(self, refined_data: List[Dict], summary: Dict) -> None:
        """Actualiza la interfaz con nuevos datos (llamado por pipeline)."""
        if not refined_data:
            return
        
        # Calcular tasa de eventos
        now = datetime.now()
        self._events_in_window += len(refined_data)
        if (now - self._window_start).total_seconds() >= 1.0:
            with self._lock:
                self.current["event_rate"] = self._events_in_window
            self._events_in_window = 0
            self._window_start = now
        
        with self._lock:
            # Actualizar métricas actuales
            self.current.update({
                "signal": summary.get("avg_signal"),
                "quality": summary.get("avg_quality", 0),
                "rsrq": summary.get("avg_rsrq"),
                "rat": summary.get("common_rat", "N/A"),
                "alerts": summary.get("total_alerts", 0),
                "security": summary.get("security_issues", 0)
            })
            
            # Actualizar historial
            if summary.get("avg_signal") is not None:
                self.history.add_reading(
                    dbm=summary["avg_signal"],
                    quality=summary.get("avg_quality", 0),
                    rsrq=summary.get("avg_rsrq"),
                    snr=summary.get("avg_snr")
                )
    
    def _run(self):
        """Ejecuta el loop principal de la interfaz."""
        if not RICH_AVAILABLE:
            self._run_simple_fallback()
            return
        
        try:
            with Live(self._render_layout(), refresh_per_second=1/self.refresh_rate, screen=True) as live:
                self._live = live
                while self._running:
                    time.sleep(self.refresh_rate)
                    live.update(self._render_layout())
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.logger.error(f"Error en TUI: {e}")
            self._run_simple_fallback()
    
    def _run_simple_fallback(self):
        """Modo simple sin rich."""
        try:
            while self._running:
                with self._lock:
                    signal = self.current.get("signal", "N/A")
                    quality = self.current.get("quality", 0)
                    rat = self.current.get("rat", "N/A")
                
                os.system('cls' if os.name == 'nt' else 'clear')
                print("\n" + "="*50)
                print("   MONITOR DE SEÑAL MÓVIL".center(50))
                print("="*50)
                print(f"\n📶 Señal: {signal} dBm")
                print(f"⭐ Calidad: {quality:.1f}%")
                print(f"📡 Red: {rat}")
                print("\n" + "="*50)
                print("Ctrl+C para salir".center(50))
                
                time.sleep(self.refresh_rate)
        except KeyboardInterrupt:
            pass
    
    def on_attach(self, pipeline):
        """Cuando se conecta al pipeline."""
        super().on_attach(pipeline)
        self._tui_thread = threading.Thread(target=self._run, daemon=True)
        self._tui_thread.start()
    
    def on_detach(self):
        """Cuando se desconecta del pipeline."""
        self._running = False
        super().on_detach()


# ============================================================================
# TUI SIMPLIFICADA (MODO TEXTO)
# ============================================================================

class SimpleTUIObserver(SignalObserver):
    """Versión simplificada sin gráficas, solo texto en consola."""
    
    def __init__(self, refresh_rate: float = 1.0):
        super().__init__(name="SimpleTUI", priority=ObserverPriority.LOW)
        self.refresh_rate = refresh_rate
        self._lock = threading.Lock()
        self._running = True
        self._last_summary = {}
        self._thread = None
    
    def update(self, refined_data: List[Dict], summary: Dict) -> None:
        """Actualiza el resumen."""
        with self._lock:
            self._last_summary = summary
    
    def _display(self):
        """Muestra la interfaz en consola."""
        with self._lock:
            summary = self._last_summary.copy()
        
        os.system('cls' if os.name == 'nt' else 'clear')
        
        print("\n" + "="*60)
        print("📡 MONITOR DE SEÑAL MÓVIL".center(60))
        print("="*60)
        
        signal = summary.get("avg_signal", "N/A")
        quality = summary.get("avg_quality", 0)
        rat = summary.get("common_rat", "N/A")
        
        # Barra de calidad
        bar_len = 30
        filled = int(bar_len * quality / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        
        # Color ANSI
        if quality >= 70:
            color = "\033[92m"
        elif quality >= 40:
            color = "\033[93m"
        else:
            color = "\033[91m"
        
        print(f"\n📶 Señal: \033[1m{signal} dBm\033[0m")
        print(f"⭐ Calidad: {color}{quality:.1f}%\033[0m")
        print(f"   [{color}{bar}\033[0m]")
        print(f"📡 Red: \033[1m{rat}\033[0m")
        
        # Alertas
        if summary.get("security_issues", 0) > 0:
            print("\n\033[91m🚨 ALERTA DE SEGURIDAD: Cifrado desactivado!\033[0m")
        elif quality < 40:
            print("\n\033[93m⚠️ ADVERTENCIA: Señal débil o inestable\033[0m")
        
        print(f"\n\033[90m📊 Lecturas: {summary.get('total_events', 0)}\033[0m")
        print(f"\033[90m🕐 {datetime.now().strftime('%H:%M:%S')}\033[0m")
        
        print("\n" + "="*60)
        print("Ctrl+C para salir".center(60))
    
    def _run(self):
        """Loop principal."""
        try:
            while self._running:
                self._display()
                time.sleep(self.refresh_rate)
        except KeyboardInterrupt:
            pass
    
    def on_attach(self, pipeline):
        """Cuando se conecta al pipeline."""
        super().on_attach(pipeline)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def on_detach(self):
        """Cuando se desconecta del pipeline."""
        self._running = False
        super().on_detach()


# ============================================================================
# FUNCIÓN FACTORY
# ============================================================================

def create_tui_observer(mode: str = "auto", refresh_rate: float = 1.0, max_history: int = 100):
    """
    Crea un observador TUI según el modo seleccionado.
    
    Args:
        mode: "full", "simple", o "auto"
        refresh_rate: Tasa de actualización (segundos)
        max_history: Máximo histórico (solo modo full)
    
    Returns:
        Instancia del observador TUI
    """
    if mode == "full":
        if RICH_AVAILABLE and PLOTEXT_AVAILABLE:
            return FullTUIObserver(refresh_rate=refresh_rate, max_history=max_history)
        else:
            print("⚠️ Modo completo no disponible. Usando modo simple...")
            return SimpleTUIObserver(refresh_rate=refresh_rate)
    elif mode == "simple":
        return SimpleTUIObserver(refresh_rate=refresh_rate)
    else:  # auto
        if RICH_AVAILABLE and PLOTEXT_AVAILABLE:
            return FullTUIObserver(refresh_rate=refresh_rate, max_history=max_history)
        else:
            return SimpleTUIObserver(refresh_rate=refresh_rate)


# ============================================================================
# PRUEBA RÁPIDA (ejecutar directamente)
# ============================================================================

if __name__ == "__main__":
    import random
    
    class MockPipeline:
        def __init__(self):
            self.observers = []
        
        def subscribe(self, observer):
            self.observers.append(observer)
            observer.on_attach(self)
        
        def simulate(self):
            events = 0
            while True:
                events += 1
                mock_summary = {
                    "avg_signal": random.randint(-120, -60),
                    "avg_quality": random.randint(20, 95),
                    "common_rat": random.choice(["LTE", "NR", "WCDMA"]),
                    "total_alerts": random.randint(0, 2),
                    "security_issues": random.randint(0, 1),
                    "total_events": events
                }
                
                mock_data = [{
                    "metrics": {"dbm": mock_summary["avg_signal"], "rat": mock_summary["common_rat"]},
                    "signal_quality": mock_summary["avg_quality"]
                }]
                
                for obs in self.observers:
                    obs.update(mock_data, mock_summary)
                
                time.sleep(0.5)
    
    print("\n" + "="*60)
    print("   📡 INTERFAZ TUI - PRUEBA")
    print("="*60)
    print("\nSelecciona modo:")
    print("  [1] Completo (con gráficas)")
    print("  [2] Simple (solo texto)")
    print("  [3] Auto")
    
    choice = input("\nOpción: ").strip()
    
    if choice == "1":
        tui = create_tui_observer("full", refresh_rate=0.5)
    elif choice == "2":
        tui = create_tui_observer("simple", refresh_rate=1.0)
    else:
        tui = create_tui_observer("auto", refresh_rate=0.5)
    
    pipeline = MockPipeline()
    pipeline.subscribe(tui)
    
    print("\n✅ Interfaz iniciada. Presiona Ctrl+C para salir...\n")
    time.sleep(2)
    
    try:
        pipeline.simulate()
    except KeyboardInterrupt:
        print("\n\n👋 Saliendo...")