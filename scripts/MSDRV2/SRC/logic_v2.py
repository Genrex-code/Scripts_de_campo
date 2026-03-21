"""
MSDRV2 - Logic Engine
Procesador de métricas de señal para módems Snapdragon.
"""

import re
import logging

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - 🧠 LOGIC - %(message)s',
    datefmt='%H:%M:%S'
)

class SignalProcessor:
    def __init__(self):
        # Patrones Regex para extraer los valores numéricos
        self.patterns = {
            'dbm': r'dbm=(-?\[0-9]+)',
            'rsrp': r'rsrp=(-?\[0-9]+)',
            'rsrq': r'rsrq=(-?\[0-9]+)',
            'rssnr': r'rssnr=(-?\[0-9]+)'
        }

    def parse_line(self, raw_line: str) -> dict:
        """Extrae métricas de una línea de log raw."""
        metrics = {}
        
        for key, pattern in self.patterns.items():
            match = re.search(pattern, raw_line)
            if match:
                val = int(match.group(1))
                # Filtrar valores nulos de Android (2147483647 es el máximo entero)
                metrics[key] = val if abs(val) < 2000 else None
            else:
                metrics[key] = None
        
        if metrics.get('dbm') is not None:
            metrics['quality_label'] = self._get_label(metrics['dbm'])
            metrics['percentage'] = self._calculate_percentage(metrics['dbm'])
            
        return metrics

    def _get_label(self, dbm: int) -> str:
        """Categoriza la calidad de la señal."""
        if dbm >= -80: return "EXCELENTE"
        if dbm >= -90: return "BUENA"
        if dbm >= -100: return "REGULAR"
        return "MALA / CRÍTICA"

    def _calculate_percentage(self, dbm: int) -> int:
        """Convierte dBm a un porcentaje aproximado (0-100%)."""
        # Rango típico: -110 (0%) a -60 (100%)
        p = int((dbm + 110) * (100 / 50))
        return max(0, min(100, p))

# ==========================================
# PRUEBA RÁPIDA
# ==========================================
if __name__ == "__main__":
    processor = SignalProcessor()
    # Una línea de ejemplo basada en lo que soltó tu teléfono
    test_line = "SignalStrength: SignalStrength: dbm=-85 rsrp=-89 rsrq=-11 rssnr=12"
    
    result = processor.parse_line(test_line)
    print("\n📊 RESULTADO DEL PROCESAMIENTO:")
    for k, v in result.items():
        print(f"  🔹 {k.upper()}: {v}")