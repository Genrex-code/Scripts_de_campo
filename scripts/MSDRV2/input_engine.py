"""
MSDRV2 - Input Engine
Módulo simplificado para captura de logs de señal vía ADB usando un generador.
"""

import subprocess
import time
import logging
from datetime import datetime

# Configuración básica de consola
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - ⚙️ ENGINE - %(message)s',
    datefmt='%H:%M:%S'
)

class SignalStreamer:
    def __init__(self):
        # Comando optimizado: solo lee el buffer de radio y filtra por "SignalStrength"
        self.adb_cmd = ["adb", "logcat", "SignalStrength:V", "*:S"]
        self.is_running = False

    def check_device(self) -> bool:
        """Verifica si hay un dispositivo conectado por ADB."""
        try:
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=3)
            # Si hay un dispositivo, aparecerá la palabra "device" junto al ID
            lines = result.stdout.strip().split('\n')
            return any("device" in line and "devices" not in line for line in lines)
        except FileNotFoundError:
            logging.error("No se encontró ADB en el sistema. ¿Está en el PATH?")
            return False
        except Exception as e:
            logging.error(f"Error al verificar ADB: {e}")
            return False

    def stream(self):
        self.is_running = True
        
        while self.is_running:
            if not self.check_device():
                logging.warning("Dispositivo no detectado. Esperando conexión...")
                time.sleep(3)
                continue
                
            logging.info("Dispositivo conectado. Iniciando captura...")
            
            try:
                process = subprocess.Popen(
                    self.adb_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )
                
                # Validamos que el canal de salida exista antes de leer
                if process.stdout is not None:
                    for line in iter(process.stdout.readline, ''):
                        if not self.is_running:
                            break
                        #aca van 3 bugfix
                        line = line.strip()
                        if "dbm=" in line.lower(): # Filtro extra de seguridad
                            yield {
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "raw": line
                            }
                
                process.terminate()
                
            except Exception as e:
                logging.error(f"Error en el stream: {e}")
                time.sleep(2)
    
    def stop(self):
        """Detiene el streamer."""
        self.is_running = False
        logging.info("Deteniendo Input Engine...")


# ==========================================
# ZONA DE PRUEBAS LOCALES (Solo se ejecuta si corres este archivo directamente)
# ==========================================
if __name__ == "__main__":
    print("="*50)
    print("🚀 INICIANDO PRUEBA DE INPUT ENGINE")
    print("="*50)
    
    streamer = SignalStreamer()
    
    try:
        # Probamos el generador consumiendo solo las primeras 10 líneas
        contador = 0
        for dato in streamer.stream():
            print(f"[{dato['timestamp']}] 📡 RAW: {dato['raw']}")
            contador += 1
            
            if contador >= 10:
                print("\n✅ Prueba de 10 líneas completada con éxito.")
                streamer.stop()
                break
                
    except KeyboardInterrupt:
        streamer.stop()
        print("\n👋 Prueba cancelada por el usuario.")
