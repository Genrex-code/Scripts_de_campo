"""
MSDRV2 - Main
Punto de entrada unificado y simplificado.
Conecta el Input Engine (Stream) con el Logic Engine (Procesador).
"""
#import shingueasumadreelamerica as EXITO
import sys
import time
from input_engine import SignalStreamer
from logic_v2 import SignalProcessor

def clear_console():
    """Limpia la consola para que los datos se vean fijos (opcional)."""
    # Descomenta la siguiente línea si quieres que la pantalla se limpie en cada actualización
    # print('\033c', end='')
    pass

def main():
    print("="*60)
    print(" 🚀 MSDRV2 - MONITOR DE SEÑAL LINEAL")
    print("="*60)
    
    # 1. Inicializamos nuestros dos módulos
    streamer = SignalStreamer()
    processor = SignalProcessor()
    
    print("⏳ Conectando con el dispositivo...")
    
    try:
        # 2. El Bucle Principal (Reemplaza al antiguo Pipeline)
        for data in streamer.stream():
            raw_line = data['raw']
            print(f"DEBUG RECIBIDO : {raw_line[:50]} TONCS SI SE APARECE ESTO SIGNIFICA QUE SI JALA EL COSO")
            timestamp = data['timestamp']
            
            # 3. Procesamos la línea
            metrics = processor.parse_line(raw_line)
            
            # 4. Solo mostramos si realmente encontramos métricas de señal (dBm)
            if metrics.get('dbm') is not None:
                clear_console()
                
                dbm = metrics['dbm']
                calidad = metrics.get('percentage', 0)
                etiqueta = metrics.get('quality_label', 'DESCONOCIDA')
                
                # Formato visual en consola
                print(f"[{timestamp}] 📡 SEÑAL DETECTADA")
                print(f" ├─ Potencia : {dbm} dBm")
                print(f" ├─ Calidad  : {calidad}%")
                print(f" └─ Estado   : {etiqueta}")
                print("-" * 40)
                
    except KeyboardInterrupt:
        # Cierre limpio si presionas Ctrl+C
        print("\n\n🛑 Interrupción detectada. Apagando motores...")
        streamer.stop()
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error crítico en el Main: {e}")
        streamer.stop()
        sys.exit(1)

if __name__ == "__main__":
    main()