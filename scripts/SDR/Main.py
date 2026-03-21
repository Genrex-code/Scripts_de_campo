#!/usr/bin/env python3
"""
Main Module - Punto de entrada principal del sistema de monitoreo de señal.
Integra ADBInput, SignalLogic, Pipeline y TUI en una sola aplicación.

Uso:
    python main.py [--mode full|simple] [--batch-size 30] [--refresh 1.0]

Ejemplos:
    python main.py                          # Modo automático
    python main.py --mode full              # Modo completo con gráficas
    python main.py --mode simple            # Modo simple sin gráficas
    python main.py --batch-size 50          # Batch más grande
"""

import sys
import os
import argparse
import signal
import logging
import time
import threading
from typing import Optional

# Agregar la ruta actual al path para importaciones
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importaciones locales
from pipeline import SignalPipeline, AlarmSystem, DatabaseModule, SignalMonitor
from Interface.UI import create_tui_observer, RICH_AVAILABLE, PLOTEXT_AVAILABLE

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger("Main")


# ============================================================================
# CLASE PRINCIPAL
# ============================================================================

class SignalMonitorApp:
    """
    Aplicación principal de monitoreo de señal.
    Orquesta todos los componentes del sistema.
    """
    
    def __init__(self, args: argparse.Namespace):
        """
        Inicializa la aplicación con los argumentos de línea de comandos.
        
        Args:
            args: Argumentos parseados
        """
        self.args = args
        self.pipeline: Optional[SignalPipeline] = None
        self.tui = None
        self._running = False
        
        # Configuración
        self.batch_size = args.batch_size
        self.batch_timeout = args.batch_timeout
        self.refresh_rate = args.refresh_rate
        self.mode = args.mode
        
        # Flags
        self.enable_alarms = not args.no_alarms
        self.enable_database = not args.no_database
        self.enable_monitor = not args.no_monitor
        
        logger.info("=" * 60)
        logger.info("📡 SIGNAL MONITOR - Sistema de Monitoreo de Señal")
        logger.info("=" * 60)
        logger.info(f"Configuración:")
        logger.info(f"  📊 Batch size: {self.batch_size} líneas")
        logger.info(f"  ⏱️  Batch timeout: {self.batch_timeout}s")
        logger.info(f"  🔄 Refresh rate: {self.refresh_rate}s")
        logger.info(f"  🎨 Modo UI: {self.mode}")
        logger.info(f"  🚨 Alarmas: {'✅' if self.enable_alarms else '❌'}")
        logger.info(f"  💾 Base de datos: {'✅' if self.enable_database else '❌'}")
        logger.info(f"  📊 Monitor simple: {'✅' if self.enable_monitor else '❌'}")
        logger.info("=" * 60)
    
    def _setup_pipeline(self) -> bool:
        """
        Configura y inicializa el pipeline.
        
        Returns:
            bool: True si se configuró correctamente
        """
        try:
            # Crear pipeline
            self.pipeline = SignalPipeline(
                batch_size=self.batch_size,
                batch_timeout=self.batch_timeout,
                enable_stats=True
            )
            
            # Suscribir observadores según configuración
            if self.enable_alarms:
                self.pipeline.subscribe(AlarmSystem(cooldown=10))
                logger.info("✅ AlarmSystem suscrito")
            
            if self.enable_database:
                self.pipeline.subscribe(DatabaseModule(
                    db_path="signal_data.jsonl",
                    flush_size=50,
                    flush_timeout=5.0
                ))
                logger.info("✅ DatabaseModule suscrito")
            
            if self.enable_monitor and not self.tui:
                # Solo usar SignalMonitor si no hay TUI
                self.pipeline.subscribe(SignalMonitor(use_print=True))
                logger.info("✅ SignalMonitor suscrito")
            
            # Configurar TUI
            if self.tui:
                self.pipeline.subscribe(self.tui)
                logger.info(f"✅ TUI ({self.mode}) suscrita")
            
            return True
            
        except Exception as e:
            logger.error(f"Error configurando pipeline: {e}")
            return False
    
    def _setup_tui(self):
        """Configura la interfaz TUI según el modo seleccionado."""
        try:
            self.tui = create_tui_observer(
                mode=self.mode,
                refresh_rate=self.refresh_rate,
                max_history=100
            )
            logger.info(f"✅ TUI creada (modo: {self.mode})")
        except Exception as e:
            logger.error(f"Error creando TUI: {e}")
            self.tui = None
    
    def _print_startup_info(self):
        """Muestra información de inicio en consola."""
        print("\n" + "=" * 70)
        print("📡 SIGNAL MONITOR - Sistema de Monitoreo de Señal Móvil")
        print("=" * 70)
        
        if self.mode == "full" and RICH_AVAILABLE and PLOTEXT_AVAILABLE:
            print("🎨 Modo: COMPLETO (con gráficas)")
            print("   📊 Panel de métricas | 📈 Gráfica en tiempo real | ⚠️ Alertas")
        elif self.mode == "full":
            print("🎨 Modo: COMPLETO (fallback a simple)")
            print("   ⚠️  rich o plotext no disponible. Usando modo texto.")
        else:
            print("📟 Modo: SIMPLE (solo texto)")
            print("   📊 Métricas básicas en consola")
        
        print(f"📊 Batch size: {self.batch_size} líneas")
        print(f"⏱️  Batch timeout: {self.batch_timeout}s")
        print(f"🔄 Actualización: cada {self.refresh_rate}s")
        
        if self.enable_alarms:
            print("🚨 Alarmas: ACTIVADAS")
        if self.enable_database:
            print("💾 Persistencia: ACTIVADA (signal_data.jsonl)")
        
        print("\n📱 Conectando a dispositivo Android...")
        print("   Asegúrate de que:")
        print("   1. USB Debugging está activado")
        print("   2. El dispositivo está conectado")
        print("   3. ADB está instalado y en el PATH")
        print("\n" + "=" * 70)
        print("Presiona Ctrl+C para detener el monitoreo\n")
    
    def _setup_signal_handlers(self):
        """Configura manejadores de señales para cierre graceful."""
        def signal_handler(signum, frame):
            print("\n\n🛑 Recibida señal de interrupción. Deteniendo...")
            self.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def start(self):
        """Inicia la aplicación."""
        self._setup_signal_handlers()
        
        # Configurar TUI
        self._setup_tui()
        
        # Configurar pipeline
        if not self._setup_pipeline():
            logger.error("No se pudo configurar el pipeline")
            return False
        
        # Mostrar información de inicio
        self._print_startup_info()
        
        # Iniciar pipeline
        try:
            if not self.pipeline.start():
                logger.error("No se pudo iniciar el pipeline")
                return False
            
            self._running = True
            logger.info("✅ Sistema iniciado correctamente")
            
            # Mantener el programa vivo
            while self._running:
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\n")
            self.stop()
        except Exception as e:
            logger.error(f"Error en ejecución: {e}")
            self.stop()
            return False
        
        return True
    
    def stop(self):
        """Detiene la aplicación gracefulmente."""
        if not self._running:
            return
        
        self._running = False
        logger.info("Deteniendo sistema...")
        
        # Detener pipeline
        if self.pipeline:
            try:
                self.pipeline.stop()
                logger.info("Pipeline detenido")
            except Exception as e:
                logger.error(f"Error deteniendo pipeline: {e}")
        
        # Mostrar estadísticas finales
        if self.pipeline and self.pipeline.stats:
            stats = self.pipeline.get_stats()
            if stats:
                print("\n" + "=" * 60)
                print("📊 ESTADÍSTICAS FINALES")
                print("=" * 60)
                print(f"  📦 Lotes procesados: {stats.get('total_batches', 0)}")
                print(f"  📱 Eventos procesados: {stats.get('total_events', 0)}")
                print(f"  ⚠️  Errores: {stats.get('errors_count', 0)}")
                if stats.get('avg_processing_ms'):
                    print(f"  ⏱️  Tiempo promedio: {stats['avg_processing_ms']:.2f}ms")
                print("=" * 60)
        
        logger.info("✅ Sistema detenido correctamente")


# ============================================================================
# PARSER DE ARGUMENTOS
# ============================================================================

def parse_arguments():
    """
    Parsea los argumentos de línea de comandos.
    
    Returns:
        argparse.Namespace: Argumentos parseados
    """
    parser = argparse.ArgumentParser(
        description="Signal Monitor - Monitoreo de señal móvil desde ADB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  %(prog)s                              # Modo automático
  %(prog)s --mode full                  # Modo completo con gráficas
  %(prog)s --mode simple                # Modo simple sin gráficas
  %(prog)s --batch-size 50              # Batch más grande
  %(prog)s --refresh 0.5                # Actualización más rápida
  %(prog)s --no-alarms                  # Desactivar alarmas
  %(prog)s --no-database                # No guardar en archivo
        """
    )
    
    # Modo de interfaz
    parser.add_argument(
        '--mode', '-m',
        type=str,
        default='auto',
        choices=['auto', 'full', 'simple'],
        help='Modo de interfaz (auto=detecta automáticamente, full=completo, simple=solo texto)'
    )
    
    # Configuración de captura
    parser.add_argument(
        '--batch-size', '-b',
        type=int,
        default=30,
        help='Tamaño del lote para procesamiento (default: 30)'
    )
    
    parser.add_argument(
        '--batch-timeout', '-t',
        type=float,
        default=1.0,
        help='Timeout para forzar flush del lote en segundos (default: 1.0)'
    )
    
    parser.add_argument(
        '--refresh-rate', '-r',
        type=float,
        default=1.0,
        help='Tasa de actualización de la interfaz en segundos (default: 1.0)'
    )
    
    # Opciones de componentes
    parser.add_argument(
        '--no-alarms',
        action='store_true',
        help='Desactivar sistema de alarmas'
    )
    
    parser.add_argument(
        '--no-database',
        action='store_true',
        help='Desactivar persistencia en base de datos'
    )
    
    parser.add_argument(
        '--no-monitor',
        action='store_true',
        help='Desactivar monitor simple (solo si no hay TUI)'
    )
    
    # Debug
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Activar modo debug (logging más detallado)'
    )
    
    return parser.parse_args()


# ============================================================================
# PUNTO DE ENTRADA PRINCIPAL
# ============================================================================

def main():
    """Punto de entrada principal."""
    # Parsear argumentos
    args = parse_arguments()
    
    # Configurar nivel de logging
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("🐛 Modo debug activado")
    
    # Verificar ADB
    import subprocess
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        if "device" not in result.stdout:
            print("\n⚠️  ADB no detecta dispositivos conectados.")
            print("   Asegúrate de que:")
            print("   1. El dispositivo está conectado por USB")
            print("   2. USB Debugging está activado")
            print("   3. Has aceptado la huella digital en el dispositivo")
            print("\n   Continuando de todas formas...\n")
    except FileNotFoundError:
        print("\n❌ ADB no encontrado en el sistema.")
        print("   Por favor instala Android Debug Bridge (ADB):")
        print("   - Windows: https://developer.android.com/studio/releases/platform-tools")
        print("   - Linux: sudo apt install adb")
        print("   - macOS: brew install android-platform-tools")
        sys.exit(1)
    except Exception as e:
        print(f"\n⚠️  Error verificando ADB: {e}")
    
    # Iniciar aplicación
    app = SignalMonitorApp(args)
    
    try:
        if app.start():
            logger.info("Aplicación finalizada correctamente")
        else:
            logger.error("Error al iniciar la aplicación")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n👋 Saliendo...")
    except Exception as e:
        logger.error(f"Error inesperado: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()