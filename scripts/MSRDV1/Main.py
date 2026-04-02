#!/usr/bin/env python3
"""
Main Module – Punto de entrada del sistema de monitoreo de señal.
Integra ADBInput, SignalLogic y Pipeline, con observadores básicos y TUI asciimatics.
"""

import sys
import time
import signal
import argparse
import logging
import subprocess
from typing import Optional

# Importaciones locales
from pipeline import SignalPipeline, AlarmSystem, DatabaseModule, SignalMonitor
from Interface.main_tui import AsciiTUI 

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger("Main")


class GracefulKiller:
    """Manejador de señales para cierre graceful."""
    def __init__(self):
        self.kill_now = False
        signal.signal(signal.SIGINT, self._exit_gracefully)
        signal.signal(signal.SIGTERM, self._exit_gracefully)

    def _exit_gracefully(self, signum, frame):
        print("\n\n🛑 Recibida señal de interrupción. Deteniendo...")
        self.kill_now = True


def parse_args():
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Signal Monitor - Monitoreo de señal móvil desde ADB"
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=30,
        help="Tamaño del lote (número de líneas) (default: 30)"
    )
    parser.add_argument(
        "--batch-timeout", "-t",
        type=float,
        default=1.0,
        help="Timeout para forzar flush del lote en segundos (default: 1.0)"
    )
    parser.add_argument(
        "--no-alarms",
        action="store_true",
        help="Desactivar sistema de alarmas"
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Desactivar persistencia en base de datos"
    )
    parser.add_argument(
        "--no-monitor",
        action="store_true",
        help="Desactivar monitor de consola"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Activar modo debug (logging más detallado)"
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Desactivar interfaz TUI (solo observadores básicos)"
    )
    return parser.parse_args()


def main():
    """Punto de entrada principal."""
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("🐛 Modo debug activado")

    # --- 1. Verificar ADB ---
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        
        # FIX: Buscar exactamente el tabulador + "device" para evitar el falso positivo del encabezado
        # y descartar dispositivos "unauthorized" o "offline"
        if "\tdevice" not in result.stdout:
            print("\n⚠️  ADB no detecta dispositivos válidos o autorizados.")
            print("   Asegúrate de que el teléfono esté conectado, con la pantalla desbloqueada")
            print("   y la depuración USB aceptada en el dispositivo.\n")
            
            # Es mejor detener el programa aquí si no hay de dónde sacar datos
            sys.exit(1)
        else:
            print("\n✅ ADB detecta un dispositivo conectado y autorizado.\n")
            
    except FileNotFoundError:
        print("\n❌ ADB no encontrado en el sistema. Instálalo y asegúrate de que esté en el PATH.\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n⚠️  Error verificando ADB: {e}\n")
        sys.exit(1)

    # --- 2. Crear Pipeline ---
    pipeline = SignalPipeline(
        batch_size=args.batch_size,
        batch_timeout=args.batch_timeout,
        enable_stats=True
    )

    # --- 3. Suscribir Observadores de Fondo ---
    if not args.no_alarms:
        pipeline.subscribe(AlarmSystem())
    
    if not args.no_db:
        pipeline.subscribe(DatabaseModule())

    # --- 4. Configurar Interfaz (TUI o Monitor Básico) ---
    tui_instance = None
    if not args.no_tui:
        try:
            # La TUI se suscribe pero NO se inicia todavía
            tui_instance = AsciiTUI(refresh_rate=0.5)
            pipeline.subscribe(tui_instance)
            logger.info("TUI asciimatics preparada")
        except Exception as e:
            logger.error(f"Error al preparar TUI: {e}")
    
    # Solo usamos el monitor de texto si NO hay TUI activa
    if not tui_instance and not args.no_monitor:
        pipeline.subscribe(SignalMonitor())

    # Manejar cierre graceful
    killer = GracefulKiller()

    # --- 5. Iniciar Pipeline (Procesamiento en hilos de fondo) ---
    if not pipeline.start():
        logger.error("No se pudo iniciar el pipeline")
        sys.exit(1)

    logger.info("✅ Pipeline iniciado correctamente.")

    # --- 6. Ejecución del Ciclo Principal (Hilo Principal) ---
    try:
        if tui_instance:
            # Asciimatics toma el control total (bloqueante)
            tui_instance.start()
        else:
            # Ciclo de espera para modo consola
            print("\n📡 Monitoreando señal en modo consola...")
            print("   (Presiona Ctrl+C para salir)\n")
            while not killer.kill_now:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        # --- 7. Apagado y Estadísticas ---
        pipeline.stop()
        
        stats = pipeline.get_stats()
        print("\n" + "=" * 60)
        print("📊 ESTADÍSTICAS FINALES")
        print("=" * 60)
        if 'adb_stats' in stats:
            print(f"  Líneas capturadas: {stats['adb_stats']['lines_captured']}")
            print(f"  Lotes procesados: {stats['adb_stats']['batches_processed']}")
            print(f"  Lotes perdidos: {stats['adb_stats']['batches_dropped']}")
            print(f"  Eficiencia: {stats['adb_stats']['efficiency']:.1f}%")
        else:
            print("  No se pudieron obtener estadísticas completas.")
        print("=" * 60)
        logger.info("Sistema detenido correctamente")


if __name__ == "__main__":
    main()