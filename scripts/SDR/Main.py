import sys
import time
from scripts.SDR.pipeline import SignalPipeline
from scripts.SDR.Interface.UI import TUIObserver # Usando tu clase de UI.py

def main():
    # 1. Inicializar el motor (Pipeline)
    # batch_size=30 para que la UI sea fluida
    pipeline = SignalPipeline(batch_size=30, batch_timeout=1.0, enable_stats=True)
    
    # 2. Inicializar tu interfaz "bonita"
    # Usamos la versión completa con gráficas
    tui = TUIObserver(refresh_rate=0.2, max_history=100)
    
    # 3. Suscribir la UI al flujo de datos
    pipeline.subscribe(tui)
    
    print("🚀 Iniciando Sistema de Monitoreo...")
    
    # 4. Arrancar
    if not pipeline.start():
        print("❌ ERROR: No se detectó dispositivo ADB o fallo en componentes.")
        return

    try:
        # Ejecutamos el loop de la UI (esto bloquea el hilo principal)
        tui.run_live() 
    except KeyboardInterrupt:
        print("\nTerminando de forma segura...")
    finally:
        pipeline.stop()
        print("✅ Sistema detenido.")

if __name__ == "__main__":
    main()