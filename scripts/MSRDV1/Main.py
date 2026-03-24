#!/usr/bin/env python3
"""
Main Module – Punto de entrada del sistema de monitoreo de señal.
Integra ADBInput, SignalLogic y Pipeline, con observadores básicos.
"""

import sys
import time
import signal
import argparse
import logging
from typing import Optional

# Importaciones locales (ajusta según tu estructura)
from pipeline import SignalPipeline, AlarmSystem, DatabaseModule, SignalMonitor
from Interface.UI import create_tui_observer   # Asegúrate de que la ruta sea correcta

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger("Main")


class GracefulKiller:
    """
    Manejador de señales para cierre graceful.
    """
    def __init__(self):
        self.kill_now = False
        signal.signal(signal.SIGINT, self._exit_gracefully)
        signal.signal(signal.SIGTERM, self._exit_gracefully)

    def _exit_gracefully(self, signum, frame):
        print("\n\n🛑 Recibida señal de interrupción. Deteniendo...")
        self.kill_now = True


def parse_args():
    """
    Parsea argumentos de línea de comandos.
    """
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
        help="Desactivar interfaz TUI"
    )
    return parser.parse_args()


def main():
    """Punto de entrada principal."""
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("🐛 Modo debug activado")

    # Verificar ADB
    import subprocess
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        if "device" not in result.stdout:
            print("\n⚠️  ADB no detecta dispositivos conectados.")
            print("   Asegúrate de que el dispositivo esté conectado y USB Debugging activado.\n")
        else:
            print("\n✅ ADB detecta dispositivos conectados.\n")
    except FileNotFoundError:
        print("\n❌ ADB no encontrado en el sistema. Instálalo y asegúrate de que esté en el PATH.\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n⚠️  Error verificando ADB: {e}\n")

    # Crear pipeline
    pipeline = SignalPipeline(
        batch_size=args.batch_size,
        batch_timeout=args.batch_timeout,
        enable_stats=True
    )

    # Suscribir observadores según opciones
    if not args.no_alarms:
        pipeline.subscribe(AlarmSystem())
        logger.info("AlarmSystem suscrito")
    if not args.no_db:
        pipeline.subscribe(DatabaseModule())
        logger.info("DatabaseModule suscrito")
    if not args.no_monitor:
        pipeline.subscribe(SignalMonitor())
        logger.info("SignalMonitor suscrito")
    # Suscripción de TUI (independiente de los demás)
    if not args.no_tui:
        tui = create_tui_observer(mode="auto", refresh_rate=0.5, max_history=100)
        pipeline.subscribe(tui)
        logger.info("TUI suscrita")

    # Manejar cierre graceful
    killer = GracefulKiller()

    # Iniciar pipeline
    if not pipeline.start():
        logger.error("No se pudo iniciar el pipeline")
        sys.exit(1)

    logger.info("✅ Sistema iniciado. Presiona Ctrl+C para detener.")
    print("\n📡 Monitoreando señal...\n")

    try:
        while not killer.kill_now:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()
        # Mostrar estadísticas finales
        stats = pipeline.get_stats()
        print("\n" + "=" * 60)
        print("📊 ESTADÍSTICAS FINALES")
        print("=" * 60)
        # Asegurar que 'adb_stats' exista (puede que no si hubo error)
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