"""
Logic Module - Procesamiento de logs de señal para Snapdragon modems.
Extrae métricas, genera alertas y calcula calidad de señal.

Características:
- Extracción de métricas mediante regex
- Validación de rangos y filtrado de datos corruptos
- Sistema de alertas multinivel (CRITICAL, WARNING, SECURITY)
- Score de calidad ponderado (0-100)
- Estadísticas de procesamiento en tiempo real
- Soporte para múltiples tecnologías (LTE, NR, WCDMA, GSM)
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from enum import Enum

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class SignalType(Enum):
    """Tipos de señal para mejor tipado"""
    LTE = "LTE"
    NR = "NR"      # 5G
    WCDMA = "WCDMA"
    GSM = "GSM"
    UNKNOWN = "Unknown"
    
    @classmethod
    def from_string(cls, value: str) -> 'SignalType':
        """Convierte string a SignalType"""
        try:
            return cls(value.upper())
        except ValueError:
            return cls.UNKNOWN


@dataclass
class SignalMetrics:
    """
    Estructura tipada para métricas de señal.
    Todos los campos son opcionales porque no siempre están presentes.
    """
    dbm: Optional[int] = None      # Potencia de señal (RSSI)
    rsrp: Optional[int] = None     # Reference Signal Received Power
    rsrq: Optional[int] = None     # Reference Signal Received Quality
    rat: Optional[str] = None      # Radio Access Technology
    snr: Optional[int] = None      # Signal-to-Noise Ratio
    cqi: Optional[int] = None      # Channel Quality Indicator
    band: Optional[int] = None     # Banda de frecuencia
    cell_id: Optional[int] = None  # Identificador de celda
    ciphering: Optional[int] = None # Estado de cifrado (0=off, 1=on)
    
    def is_valid(self) -> bool:
        """Verifica si al menos una métrica es válida"""
        return any(v is not None for v in asdict(self).values())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario, omitiendo valores None"""
        return {k: v for k, v in asdict(self).items() if v is not None}
    #bugfix 1
    def get_primary_signal(self) -> Optional[int]:
        if self.rsrp is not None:
            return self.rsrp
        return self.dbm
    
    def get_rat_enum(self) -> SignalType:
        """Obtiene el tipo de red como Enum"""
        return SignalType.from_string(self.rat) if self.rat else SignalType.UNKNOWN


class SignalLogic:
    """
    Procesador de logs de señal para Snapdragon modems.
    Extrae métricas relevantes y genera análisis en tiempo real.
    
    Uso típico:
        processor = SignalLogic()
        refined = processor.process_batch(raw_batch)
        summary = processor.get_summary(refined)
    """
    
    # Constantes de clase
    DEFAULT_QUALITY = 0.0
    MIN_QUALITY = 0
    MAX_QUALITY = 100
    
    def __init__(self, enable_stats: bool = True, debug_mode: bool = False):
        """
        Inicializa el procesador de señales.
        
        Args:
            enable_stats: Habilita estadísticas de procesamiento
            debug_mode: Modo debug con logging más detallado
        """
        self.logger = logging.getLogger(f"SignalLogic")
        self.enable_stats = enable_stats
        self.debug_mode = debug_mode
        
        # Diccionario de Patrones optimizado
        self._init_patterns()
        
        # Rangos válidos para métricas (filtra valores corruptos)
        self.valid_ranges = {
            "dbm": (-140, -40),      # Rango típico de RSSI/RSRP
            "rsrp": (-140, -40),     # Rango típico de RSRP
            "rsrq": (-30, -3),       # Rango típico de RSRQ
            "snr": (-10, 40),        # Rango típico de SNR
            "cqi": (0, 15),          # Rango típico de CQI (0-15 en LTE)
        }
        
        # Umbrales para alertas
        self.thresholds = {
            "critical_signal": -120,  # Señal críticamente débil
            "weak_signal": -110,      # Señal débil
            "poor_quality": -15,      # Mala calidad (RSRQ)
            "low_snr": 5,             # SNR baja
            "excellent_signal": -65,  # Señal excelente
            "excellent_quality": -8,  # Calidad excelente (RSRQ)
        }
        
        # Pesos para cálculo de calidad (ajustables)
        self.quality_weights = {
            "signal": 0.4,    # Peso de intensidad de señal
            "rsrq": 0.35,     # Peso de calidad de señal
            "snr": 0.25,      # Peso de relación señal/ruido
        }
        
        # Estadísticas de procesamiento
        if self.enable_stats:
            self._reset_stats()
        
        self.logger.info("SignalLogic inicializado correctamente")
        if debug_mode:
            self.logger.info("Modo debug activado")
    
    def _init_patterns(self) -> None:
        """Inicializa los patrones regex para extracción de métricas."""
        self.patterns = {
            #bugfix2
            "dbm": re.compile(r"dbm=(?P<val>-?\d+)", re.IGNORECASE),
            "rsrp": re.compile(r"rsrp=(?P<val>-?\d+)", re.IGNORECASE),
            "rsrq": re.compile(r"rsrq=(?P<val>-?\d+)", re.IGNORECASE),
            "snr": re.compile(r"snr=(?P<val>-?\d+)", re.IGNORECASE),
            "cqi": re.compile(r"cqi=(?P<val>\d+)", re.IGNORECASE),
            #bugfix3
            "rat": re.compile(r"rat=(?P<val>[A-Za-z0-9_]+)", re.IGNORECASE),
            #bugfix4
            "cell_id": re.compile(r"cell\w*[=:]\s*(?P<val>\d+)", re.IGNORECASE),
            "ciphering": re.compile(r"ciphering=(?P<val>[01])", re.IGNORECASE),
            "band": re.compile(r"band=(?P<val>\d+)", re.IGNORECASE),
        }
    
    def _reset_stats(self) -> None:
        """Reinicia las estadísticas internas."""
        self.stats = {
            "total_processed": 0,
            "useful_events": 0,
            "filtered_events": 0,
            "alerts_generated": 0,
            "metrics_found": defaultdict(int),
            "rat_distribution": defaultdict(int),
            "quality_distribution": {
                "excellent": 0,  # >= 70
                "good": 0,      # >= 40
                "poor": 0,      # < 40
            }
        }
    
    # ========================================================================
    # Métodos Públicos Principales
    # ========================================================================
    
    def process_batch(self, batch: List[Dict]) -> List[Dict]:
        """
        Procesa un lote de entradas crudas y devuelve solo eventos útiles.
        
        Args:
            batch: Lista de entradas crudas del ADB
            
        Returns:
            Lista de entradas enriquecidas con métricas de señal
        """
        if not batch:
            return []
        
        refined_data = []
        
        for entry in batch:
            cleaned_entry = self._analyze_entry(entry)
            if cleaned_entry:
                refined_data.append(cleaned_entry)
                
                if self.enable_stats:
                    self._update_stats(cleaned_entry)
            elif self.enable_stats:
                self.stats["filtered_events"] += 1
            
            if self.enable_stats:
                self.stats["total_processed"] += 1
        
        if refined_data and self.debug_mode:
            self.logger.debug(
                f"Batch: {len(refined_data)} útiles / {len(batch)} totales "
                f"({len(batch) - len(refined_data)} descartados)"
            )
        
        return refined_data
    
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
                "message": "No hay datos para analizar",
                "avg_quality": self.DEFAULT_QUALITY
            }
        
        # Calcular métricas agregadas
        signal_metrics = [e.get("metrics", {}) for e in refined_batch]
        
        # Promedios de métricas numéricas
        dbm_values = [m["dbm"] for m in signal_metrics if "dbm" in m]
        rsrp_values = [m["rsrp"] for m in signal_metrics if "rsrp" in m]
        rsrq_values = [m["rsrq"] for m in signal_metrics if "rsrq" in m]
        snr_values = [m["snr"] for m in signal_metrics if "snr" in m]
        
        # RAT más común
        rat_counts = defaultdict(int)
        for m in signal_metrics:
            if "rat" in m:
                rat_counts[m["rat"]] += 1
        most_common_rat = max(rat_counts.items(), key=lambda x: x[1])[0] if rat_counts else "Unknown"
        
        # Calidad promedio
        quality_values = [e.get("signal_quality", 0) for e in refined_batch]
        avg_quality = sum(quality_values) / len(quality_values) if quality_values else 0
        
        # Calcular tendencia (mejorando o empeorando)
        trend = self._calculate_trend(quality_values)
        
        summary = {
            "status": "success",
            "total_events": len(refined_batch),
            "timestamp": refined_batch[-1].get("timestamp"),
            "avg_signal": self._safe_average(dbm_values) or self._safe_average(rsrp_values),
            "avg_rsrp": self._safe_average(rsrp_values),
            "avg_rsrq": self._safe_average(rsrq_values),
            "avg_snr": self._safe_average(snr_values),
            "common_rat": most_common_rat,
            #bugfix5
            "events_with_alerts": sum(1 for e in refined_batch if e.get("alerts")),
            "total_alerts": sum(len(e.get("alerts", [])) for e in refined_batch),
            "avg_quality": avg_quality,
            #bugfix 6
            "security_issues": sum(
    1 for e in refined_batch
    for alert in e.get("alerts", [])
    if "SECURITY" in alert
),
            "trend": trend,
            "quality_category": self._get_quality_category(avg_quality)
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
        
        self.logger.info(f"Resumen: {len(refined_batch)} eventos, calidad: {avg_quality:.1f}%")
        return summary
    
    def get_latest_metrics(self, refined_batch: List[Dict]) -> Dict[str, Any]:
        """
        Obtiene las métricas más recientes del lote.
        Útil para actualizar interfaces en tiempo real.
        
        Args:
            refined_batch: Lote de datos procesados
            
        Returns:
            Diccionario con las métricas más recientes
        """
        if not refined_batch:
            return {
                "has_data": False,
                "signal": None,
                "quality": 0,
                "rat": "Unknown"
            }
        
        latest = refined_batch[-1]
        metrics = latest.get("metrics", {})
        
        return {
            "has_data": True,
            "timestamp": latest.get("timestamp"),
            "signal": metrics.get("dbm") or metrics.get("rsrp"),
            "rsrq": metrics.get("rsrq"),
            "snr": metrics.get("snr"),
            "quality": latest.get("signal_quality", 0),
            "rat": metrics.get("rat", "Unknown"),
            "alerts": latest.get("alerts", [])
        }
    
    # ========================================================================
    # Métodos Internos de Procesamiento
    # ========================================================================
    
    def _analyze_entry(self, entry: Dict) -> Optional[Dict]:
        """
        Analiza una entrada aplicando regex y validaciones.
        IMPORTANTE: Modifica el diccionario original añadiendo campos.
        
        Args:
            entry: Entrada cruda del ADB
            
        Returns:
            Entrada enriquecida o None si no hay datos relevantes
        """
        payload = entry.get("raw_payload", "")
        if not payload:
            return None
        
        # Extraer métricas
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
            "timestamp": entry.get("timestamp")
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
            if metric in self.valid_ranges:
                num_val = int(value)
                min_val, max_val = self.valid_ranges[metric]
                
                if min_val <= num_val <= max_val:
                    return num_val
                else:
                    if self.debug_mode:
                        self.logger.debug(f"Valor fuera de rango {metric}: {num_val}")
                    return None
            else:
                # Métricas no numéricas
                return value.strip()
                
        except (ValueError, TypeError):
            if self.debug_mode:
                self.logger.debug(f"Error convertiendo {metric}={value}")
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
        signal_val = metrics.get_primary_signal()
        
        # Alertas de intensidad de señal
        if signal_val is not None:
            if signal_val <= self.thresholds["critical_signal"]:
                alerts.append(f"🔴 CRITICAL: Señal extremadamente débil ({signal_val} dBm)")
                if self.enable_stats:
                    self.stats["alerts_generated"] += 1
            elif signal_val <= self.thresholds["weak_signal"]:
                alerts.append(f"🟡 WARNING: Señal débil ({signal_val} dBm)")
        
        # Alertas de calidad (RSRQ)
        if metrics.rsrq is not None and metrics.rsrq <= self.thresholds["poor_quality"]:
            alerts.append(f"🟡 WARNING: Mala calidad (RSRQ: {metrics.rsrq})")
        
        # Alertas de SNR
        if metrics.snr is not None and metrics.snr <= self.thresholds["low_snr"]:
            alerts.append(f"🟡 WARNING: SNR baja ({metrics.snr} dB)")
        
        # Alertas de seguridad (CRÍTICAS)
        if metrics.ciphering == 0:
            alerts.append("🔴 SECURITY: Cifrado desactivado - Posible celda falsa")
        
        return alerts
    
    def _calculate_quality_score(self, metrics: SignalMetrics) -> float:
        """
        Calcula un score de calidad de señal (0-100) usando pesos configurables.
        
        Args:
            metrics: Métricas de señal
            
        Returns:
            Score de calidad (0-100)
        """
        scores = []
        weights = []
        
        # Score basado en intensidad de señal
        signal_val = metrics.get_primary_signal()
        if signal_val is not None:
            # Mapeo: -40 dBm = 100, -120 dBm = 0
            signal_score = max(0, min(100, int((signal_val + 120) / 0.8)))
            scores.append(signal_score)
            weights.append(self.quality_weights["signal"])
        
        # Score basado en RSRQ (calidad)
        if metrics.rsrq is not None:
            # Mapeo: -3 = 100, -20 = 0
            rsrq_score = max(0, min(100, int((metrics.rsrq + 20) / 0.17)))
            scores.append(rsrq_score)
            weights.append(self.quality_weights["rsrq"])
        
        # Score basado en SNR
        if metrics.snr is not None:
            # Mapeo: 30 dB = 100, 0 dB = 0
            snr_score = max(0, min(100, int(metrics.snr * 3.33)))
            scores.append(snr_score)
            weights.append(self.quality_weights["snr"])
        
        # Calcular promedio ponderado
        if not scores:
            return self.DEFAULT_QUALITY
        
        total_weight = sum(weights)
        if total_weight == 0:
            return sum(scores) / len(scores)
        
        weighted_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
        return min(self.MAX_QUALITY, max(self.MIN_QUALITY, weighted_score))
    
    # ========================================================================
    # Métodos de Estadísticas y Utilidades
    # ========================================================================
    
    def _update_stats(self, entry: Dict) -> None:
        """Actualiza estadísticas con una entrada procesada."""
        if not self.enable_stats:
            return
        
        self.stats["useful_events"] += 1
        
        # Actualizar conteo de métricas
        metrics = entry.get("metrics", {})
        for key in metrics.keys():
            self.stats["metrics_found"][key] += 1
        
        # Actualizar distribución de RAT
        if "rat" in metrics:
            self.stats["rat_distribution"][metrics["rat"]] += 1
        
        # Actualizar distribución de calidad
        quality = entry.get("signal_quality", 0)
        if quality >= 70:
            self.stats["quality_distribution"]["excellent"] += 1
        elif quality >= 40:
            self.stats["quality_distribution"]["good"] += 1
        else:
            self.stats["quality_distribution"]["poor"] += 1
    
    def _safe_average(self, values: List[float]) -> Optional[float]:
        """Calcula promedio de forma segura."""
        if not values:
            return None
        return sum(values) / len(values)
    
    def _calculate_trend(self, quality_values: List[float]) -> str:
        """
        Calcula la tendencia de calidad (mejorando/empeorando/estable).
        
        Args:
            quality_values: Lista de valores de calidad
            
        Returns:
            "improving", "degrading", o "stable"
        """
        if len(quality_values) < 3:
            return "stable"
        
        # Comparar primeros 3 con últimos 3
        first_avg = sum(quality_values[:3]) / 3
        last_avg = sum(quality_values[-3:]) / 3
        
        diff = last_avg - first_avg
        
        if diff > 5:
            return "improving"
        elif diff < -5:
            return "degrading"
        else:
            return "stable"
    
    def _get_quality_category(self, quality: float) -> str:
        """
        Clasifica la calidad en categorías.
        
        Args:
            quality: Score de calidad (0-100)
            
        Returns:
            "excellent", "good", o "poor"
        """
        if quality >= 70:
            return "excellent"
        elif quality >= 40:
            return "good"
        else:
            return "poor"
    
    def reset_stats(self) -> None:
        """Reinicia las estadísticas del procesador."""
        if self.enable_stats:
            self._reset_stats()
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
    
    def get_quality_distribution(self) -> Dict[str, int]:
        """
        Obtiene la distribución de calidad de los eventos procesados.
        
        Returns:
            Diccionario con conteos por categoría
        """
        if not self.enable_stats:
            return {}
        return dict(self.stats["quality_distribution"])
    
    def get_rat_distribution(self) -> Dict[str, int]:
        """
        Obtiene la distribución de tecnologías de red.
        
        Returns:
            Diccionario con conteos por RAT
        """
        if not self.enable_stats:
            return {}
        return dict(self.stats["rat_distribution"])


# ========================================================================
# Punto de Entrada (Pruebas)
# ========================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("   🔧 SIGNAL LOGIC - Prueba de Procesamiento")
    print("=" * 60)
    
    # Datos de prueba
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
            "raw_payload": "SignalStrength: dbm=-65 rsrp=-67 rsrq=-5 rat=LTE band=3 snr=25"
        }
    ]
    
    # Inicializar procesador
    processor = SignalLogic(enable_stats=True, debug_mode=True)
    
    # Procesar lote
    refined = processor.process_batch(test_batch)
    
    # Mostrar resultados
    print("\n📊 RESULTADOS DEL PROCESAMIENTO")
    print("-" * 40)
    for i, entry in enumerate(refined):
        print(f"\nEvento {i+1}:")
        print(f"  📱 Métricas: {entry['metrics']}")
        print(f"  ⚠️  Alertas: {entry.get('alerts', [])}")
        print(f"  ⭐ Calidad: {entry.get('signal_quality', 0):.1f}/100")
    
    # Mostrar resumen
    summary = processor.get_summary(refined)
    print("\n📈 RESUMEN DEL LOTE")
    print("-" * 40)
    print(f"  Eventos útiles: {summary['total_events']}")
    print(f"  Señal promedio: {summary['avg_signal']} dBm")
    print(f"  Calidad promedio: {summary['avg_quality']:.1f}%")
    print(f"  Red predominante: {summary['common_rat']}")
    print(f"  Alertas totales: {summary['total_alerts']}")
    print(f"  Problemas seguridad: {summary['security_issues']}")
    print(f"  Tendencia: {summary['trend']}")
    print(f"  Categoría: {summary['quality_category']}")
    
    # Mostrar estadísticas de patrones
    print("\n📊 ESTADÍSTICAS DE PATRONES")
    print("-" * 40)
    for pattern, count in processor.get_pattern_stats().items():
        print(f"  {pattern}: {count} matches")
    
    # Mostrar distribución de calidad
    print("\n📊 DISTRIBUCIÓN DE CALIDAD")
    print("-" * 40)
    for category, count in processor.get_quality_distribution().items():
        print(f"  {category}: {count} eventos")
    
    print("\n✅ Prueba completada")