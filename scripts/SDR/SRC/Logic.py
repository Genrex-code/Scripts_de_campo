import re
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from collections import defaultdict
from dataclasses import dataclass, asdict
from enum import Enum

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class SignalType(Enum):
    """Tipos de señal para mejor tipado"""
    LTE = "LTE"
    NR = "NR"  # 5G
    WCDMA = "WCDMA"
    GSM = "GSM"
    UNKNOWN = "Unknown"

@dataclass
class SignalMetrics:
    """Estructura tipada para métricas de señal"""
    dbm: Optional[int] = None
    rsrp: Optional[int] = None
    rsrq: Optional[int] = None
    rat: Optional[str] = None
    snr: Optional[int] = None       # <-- FALTABA
    cqi: Optional[int] = None       # <-- FALTABA
    band: Optional[int] = None      # <-- FALTABA
    cell_id: Optional[int] = None
    ciphering: Optional[int] = None
    
    def is_valid(self) -> bool:
        """Verifica si al menos una métrica es válida"""
        return any(v is not None for v in asdict(self).values())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario, omitiendo valores None"""
        return {k: v for k, v in asdict(self).items() if v is not None}


class SignalLogic:
    """
    Procesador de logs de señal para Snapdragon modems.
    Extrae métricas relevantes y genera análisis en tiempo real.
    """
    
    def __init__(self, enable_stats: bool = True):
        """
        Inicializa el procesador de señales.
        
        Args:
            enable_stats: Habilita estadísticas de procesamiento
        """
        self.logger = logging.getLogger(__name__)
        self.enable_stats = enable_stats
        
        # Diccionario de Patrones optimizado
        self.patterns = {
            "dbm": re.compile(r"SignalStrength:.*?dbm=(?P<val>-?\d+)", re.IGNORECASE),
            "rsrp": re.compile(r"rsrp=(?P<val>-?\d+)", re.IGNORECASE),
            "rsrq": re.compile(r"rsrq=(?P<val>-?\d+)", re.IGNORECASE),
            "snr": re.compile(r"snr=(?P<val>-?\d+)", re.IGNORECASE),  # Relación señal/ruido
            "cqi": re.compile(r"cqi=(?P<val>\d+)", re.IGNORECASE),      # Channel Quality Indicator
            "rat": re.compile(r"rat=(?P<val>[A-Za-z0-9]+)", re.IGNORECASE),
            "cell_id": re.compile(r"cell(?:Identity)?[=:]\s*(?P<val>\d+)", re.IGNORECASE),
            "ciphering": re.compile(r"ciphering=(?P<val>[01])", re.IGNORECASE),
            "band": re.compile(r"band=(?P<val>\d+)", re.IGNORECASE),     # Banda de frecuencia
        }
        
        # Rango válido para métricas (ayuda a filtrar valores corruptos)
        self.valid_ranges = {
            "dbm": (-140, -40),      # Rango típico de RSSI/RSRP
            "rsrp": (-140, -40),     # Rango típico de RSRP
            "rsrq": (-30, -3),       # Rango típico de RSRQ
            "snr": (-10, 40),        # Rango típico de SNR
            "cqi": (0, 15),          # Rango típico de CQI
        }
        
        # Umbrales para alertas
        self.thresholds = {
            "weak_signal": -110,      # Señal débil
            "critical_signal": -120,  # Señal crítica
            "poor_quality": -15,      # Mala calidad (RSRQ)
            "low_snr": 5,             # SNR baja
        }
        
        # Estadísticas de procesamiento
        if self.enable_stats:
            self.stats = {
                "total_processed": 0,
                "useful_events": 0,
                "filtered_events": 0,
                "alerts_generated": 0,
                "metrics_found": defaultdict(int),
                "rat_distribution": defaultdict(int),
            }
        
        self.logger.info("SignalLogic inicializado correctamente")
    
    def process_batch(self, batch: List[Dict]) -> List[Dict]:
        """
        Procesa un lote de entradas crudas y devuelve solo eventos útiles.
        
        Args:
            batch: Lista de entradas crudas del ADB
            
        Returns:
            Lista de entradas enriquecidas con métricas de señal
        """
        refined_data = []
        
        for entry in batch:
            cleaned_entry = self._analyze_entry(entry)
            if cleaned_entry:
                refined_data.append(cleaned_entry)
                
                # Actualizar estadísticas
                if self.enable_stats:
                    self.stats["useful_events"] += 1
                    metrics = cleaned_entry.get("metrics", {})
                    for key in metrics.keys():
                        self.stats["metrics_found"][key] += 1
                    if "rat" in metrics:
                        self.stats["rat_distribution"][metrics["rat"]] += 1
            elif self.enable_stats:
                self.stats["filtered_events"] += 1
            
            if self.enable_stats:
                self.stats["total_processed"] += 1
        
        if refined_data:
            self.logger.debug(
                f"Limpieza completada: {len(refined_data)} eventos útiles "
                f"(descartados: {len(batch) - len(refined_data)})"
            )
        
        return refined_data
    
    def _analyze_entry(self, entry: Dict) -> Optional[Dict]:
        """
        Analiza una entrada aplicando regex y validaciones.
        
        Args:
            entry: Entrada cruda del ADB
            
        Returns:
            Entrada enriquecida o None si no hay datos relevantes
        """
        payload = entry.get("raw_payload", "")
        if not payload:
            return None
        
        # Extraer métricas usando los patrones
        metrics = SignalMetrics()
        has_any_metric = False
        
        for key, pattern in self.patterns.items():
            match = pattern.search(payload)
            if match:
                val = match.group("val")
                converted = self._convert_metric_value(key, val)
                
                if converted is not None:
                    setattr(metrics, key, converted)
                    has_any_metric = True
        
        # Si no hay métricas, descartar
        if not has_any_metric:
            return None
        
        # Enriquecer la entrada
        entry.update({
            "metrics": metrics.to_dict(),
            "has_signal_data": metrics.dbm is not None or metrics.rsrp is not None,
            "alerts": self._generate_alerts(metrics),
            "signal_quality": self._calculate_quality_score(metrics),
            "timestamp": entry.get("timestamp")  # Preservar timestamp original
        })
        
        return entry
    
    def _convert_metric_value(self, metric: str, value: str) -> Optional[Any]:
        """
        Convierte y valida el valor de una métrica.
        
        Args:
            metric: Nombre de la métrica
            value: Valor en string
            
        Returns:
            Valor convertido o None si es inválido
        """
        try:
            # Convertir a int para métricas numéricas
            if metric in self.valid_ranges:
                num_val = int(value)
                
                # Validar rango
                min_val, max_val = self.valid_ranges[metric]
                if min_val <= num_val <= max_val:
                    return num_val
                else:
                    self.logger.debug(f"Valor fuera de rango para {metric}: {num_val}")
                    return None
            else:
                # Métricas no numéricas (rat, etc.)
                return value.strip()
                
        except (ValueError, TypeError) as e:
            self.logger.debug(f"Error convertiendo {metric}={value}: {e}")
            return None
    
    def _generate_alerts(self, metrics: SignalMetrics) -> List[str]:
        """
        Genera alertas basadas en las métricas actuales.
        
        Args:
            metrics: Métricas de señal
            
        Returns:
            Lista de alertas generadas
        """
        alerts = []
        
        # Alertas de intensidad de señal
        signal_val = metrics.dbm or metrics.rsrp
        if signal_val is not None:
            if signal_val <= self.thresholds["critical_signal"]:
                alerts.append(f"CRITICAL: Señal extremadamente débil ({signal_val} dBm)")
                if self.enable_stats:
                    self.stats["alerts_generated"] += 1
            elif signal_val <= self.thresholds["weak_signal"]:
                alerts.append(f"WARNING: Señal débil ({signal_val} dBm)")
        
        # Alertas de calidad
        if metrics.rsrq is not None and metrics.rsrq <= self.thresholds["poor_quality"]:
            alerts.append(f"WARNING: Mala calidad de señal (RSRQ: {metrics.rsrq})")
        
        # Alertas de SNR
        if metrics.snr is not None and metrics.snr <= self.thresholds["low_snr"]:
            alerts.append(f"WARNING: SNR baja ({metrics.snr} dB)")
        
        # Alertas de seguridad
        if metrics.ciphering == 0:
            alerts.append("SECURITY: Cifrado desactivado")
        
        return alerts
    
    def _calculate_quality_score(self, metrics: SignalMetrics) -> float:
        """
        Calcula un score de calidad de señal (0-100).
        
        Args:
            metrics: Métricas de señal
            
        Returns:
            Score de calidad (0-100)
        """
        scores = []
        
        # Score basado en intensidad de señal
        signal_val = metrics.dbm or metrics.rsrp
        if signal_val is not None:
            # Mapeo lineal: -40 = 100, -120 = 0
            signal_score = max(0, min(100, int((signal_val + 120) / 0.8)))
            scores.append(signal_score)
        
        # Score basado en RSRQ (calidad)
        if metrics.rsrq is not None:
            # RSRQ: -3 = 100, -20 = 0
            rsrq_score = max(0, min(100, int((metrics.rsrq + 20) / 0.17)))
            scores.append(rsrq_score)
        
        # Score basado en SNR
        if metrics.snr is not None:
            # SNR: 30 = 100, 0 = 0
            snr_score = max(0, min(100, int(metrics.snr * 3.33)))
            scores.append(snr_score)
        
        # Promedio de scores disponibles
        return sum(scores) / len(scores) if scores else 0.0
    
    def get_summary(self, refined_batch: List[Dict]) -> Dict[str, Any]:
        """
        Genera un resumen estadístico del lote procesado.
        
        Args:
            refined_batch: Lote de datos ya procesados
            
        Returns:
            Diccionario con estadísticas agregadas
        """
        if not refined_batch:
            return {
                "status": "empty",
                "message": "No hay datos para analizar"
            }
        
        # Calcular métricas agregadas
        signal_metrics = [e.get("metrics", {}) for e in refined_batch]
        
        # Extraer valores para promedios
        dbm_values = [m["dbm"] for m in signal_metrics if "dbm" in m]
        rsrp_values = [m["rsrp"] for m in signal_metrics if "rsrp" in m]
        rsrq_values = [m["rsrq"] for m in signal_metrics if "rsrq" in m]
        
        # Obtener la RAT más común
        rat_counts = defaultdict(int)
        for m in signal_metrics:
            if "rat" in m:
                rat_counts[m["rat"]] += 1
        most_common_rat = max(rat_counts.items(), key=lambda x: x[1])[0] if rat_counts else "Unknown"
        
        # Generar resumen
        summary = {
            "status": "success",
            "total_events": len(refined_batch),
            "timestamp": refined_batch[-1].get("timestamp"),
            "avg_signal": sum(dbm_values) / len(dbm_values) if dbm_values else None,
            "avg_rsrp": sum(rsrp_values) / len(rsrp_values) if rsrp_values else None,
            "avg_rsrq": sum(rsrq_values) / len(rsrq_values) if rsrq_values else None,
            "common_rat": most_common_rat,
            "alerts": sum(1 for e in refined_batch if e.get("alerts")),
            "total_alerts": len([a for e in refined_batch for a in e.get("alerts", [])]),
            "avg_quality": sum(e.get("signal_quality", 0) for e in refined_batch) / len(refined_batch),
            "security_issues": sum(1 for e in refined_batch if "SECURITY" in str(e.get("alerts", [])))
        }
        
        # Añadir estadísticas del procesador si están habilitadas
        if self.enable_stats:
            summary["processor_stats"] = {
                "total_processed": self.stats["total_processed"],
                "useful_ratio": (self.stats["useful_events"] / self.stats["total_processed"] * 100) 
                                if self.stats["total_processed"] > 0 else 0,
                "top_metrics": dict(sorted(self.stats["metrics_found"].items(), 
                                          key=lambda x: x[1], reverse=True)[:5])
            }
        
        self.logger.info(f"Resumen generado: {len(refined_batch)} eventos, "
                        f"calidad promedio: {summary['avg_quality']:.1f}")
        
        return summary
    
    def reset_stats(self) -> None:
        """Reinicia las estadísticas del procesador"""
        if self.enable_stats:
            self.stats = {
                "total_processed": 0,
                "useful_events": 0,
                "filtered_events": 0,
                "alerts_generated": 0,
                "metrics_found": defaultdict(int),
                "rat_distribution": defaultdict(int),
            }
            self.logger.info("Estadísticas reiniciadas")
    
    def get_pattern_stats(self) -> Dict[str, int]:
        """
        Obtiene estadísticas de uso de patrones (útil para debugging).
        
        Returns:
            Diccionario con conteo de matches por patrón
        """
        if not self.enable_stats:
            return {}
        return dict(self.stats["metrics_found"])


# Ejemplo de uso y pruebas
if __name__ == "__main__":
    # Datos de prueba simulados
    test_batch = [
        {
            "timestamp": "2026-01-20T10:30:45",
            "raw_payload": "SignalStrength: dbm=-85 rsrp=-87 rsrq=-12 rat=LTE cellIdentityCellid=12345 ciphering=1"
        },
        {
            "timestamp": "2026-01-20T10:30:46",
            "raw_payload": "SignalStrength: dbm=-115 rsrp=-118 rsrq=-18 rat=NR ciphering=0 snr=3"
        },
        {
            "timestamp": "2026-01-20T10:30:47",
            "raw_payload": "Sistema operativo: Android 12 - Mensaje irrelevante"
        },
        {
            "timestamp": "2026-01-20T10:30:48",
            "raw_payload": "SignalStrength: dbm=-65 rsrp=-67 rsrq=-5 rat=LTE band=3"
        }
    ]
    
    # Inicializar procesador
    processor = SignalLogic()
    
    # Procesar lote
    refined = processor.process_batch(test_batch)
    
    # Mostrar resultados
    print("\n=== RESULTADOS DEL PROCESAMIENTO ===")
    for i, entry in enumerate(refined):
        print(f"\nEvento {i+1}:")
        print(f"  Métricas: {entry['metrics']}")
        print(f"  Alertas: {entry.get('alerts', [])}")
        print(f"  Calidad: {entry.get('signal_quality', 0):.1f}/100")
    
    # Mostrar resumen
    summary = processor.get_summary(refined)
    print(f"\n=== RESUMEN ===")
    for key, value in summary.items():
        if key != "processor_stats":
            print(f"{key}: {value}")
    
    # Mostrar estadísticas de patrones
    print(f"\n=== ESTADÍSTICAS DE PATRONES ===")
    for pattern, count in processor.get_pattern_stats().items():
        print(f"{pattern}: {count} matches")