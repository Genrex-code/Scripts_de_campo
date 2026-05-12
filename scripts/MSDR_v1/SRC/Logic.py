"""
Logic Module – Procesamiento de logs de señal para Snapdragon modems.
Extrae métricas, genera alertas y calcula calidad de señal.
"""

import re
import logging
from typing import List, Dict, Any, Optional
from collections import defaultdict
from dataclasses import dataclass, asdict
from enum import Enum

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class SignalType(Enum):
    """Tipos de señal"""
    LTE = "LTE"
    NR = "NR"
    WCDMA = "WCDMA"
    GSM = "GSM"
    UNKNOWN = "Unknown"


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

    def get_primary_signal(self) -> Optional[int]:
        """Obtiene la métrica de señal primaria (prioridad: rsrp > dbm)"""
        return self.rsrp or self.dbm


class SignalLogic:
    """
    Procesador de logs de señal.
    Extrae métricas mediante regex, valida rangos, genera alertas y calcula calidad.
    """

    def __init__(self, enable_stats: bool = True, debug_mode: bool = False):
        """
        Args:
            enable_stats: Habilita estadísticas de procesamiento
            debug_mode: Modo debug con logging más detallado
        """
        self.logger = logging.getLogger("SignalLogic")
        self.enable_stats = enable_stats
        self.debug_mode = debug_mode

        # Patrones regex para extraer métricas
        self.patterns = {
            "dbm": re.compile(r"SignalStrength:.*?dbm=(?P<val>-?\d+)", re.IGNORECASE),
            "rsrp": re.compile(r"rsrp=(?P<val>-?\d+)", re.IGNORECASE),
            "rsrq": re.compile(r"rsrq=(?P<val>-?\d+)", re.IGNORECASE),
            "snr": re.compile(r"snr=(?P<val>-?\d+)", re.IGNORECASE),
            "cqi": re.compile(r"cqi=(?P<val>\d+)", re.IGNORECASE),
            "rat": re.compile(r"rat=(?P<val>[A-Za-z0-9]+)", re.IGNORECASE),
            "cell_id": re.compile(r"cell(?:Identity)?[=:]\s*(?P<val>\d+)", re.IGNORECASE),
            "ciphering": re.compile(r"ciphering=(?P<val>[01])", re.IGNORECASE),
            "band": re.compile(r"band=(?P<val>\d+)", re.IGNORECASE),
        }

        # Rangos válidos para métricas (filtra valores corruptos)
        self.valid_ranges = {
            "dbm": (-140, -40),
            "rsrp": (-140, -40),
            "rsrq": (-30, -3),
            "snr": (-10, 40),
            "cqi": (0, 15),
        }

        # Umbrales para alertas
        self.thresholds = {
            "critical_signal": -120,   # Señal críticamente débil
            "weak_signal": -110,       # Señal débil
            "poor_quality": -15,       # Mala calidad (RSRQ)
            "low_snr": 5,              # SNR baja
        }

        # Pesos para cálculo de calidad (0-100)
        self.quality_weights = {
            "signal": 0.4,
            "rsrq": 0.35,
            "snr": 0.25,
        }

        # Estadísticas
        if self.enable_stats:
            self._reset_stats()

        self.logger.info("SignalLogic inicializado")

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
                "good": 0,       # >= 40
                "poor": 0,       # < 40
            }
        }

    # ------------------------------------------------------------------------
    # Métodos públicos principales
    # ------------------------------------------------------------------------

    def process_batch(self, batch: List[Dict]) -> List[Dict]:
        """
        Procesa un lote de entradas crudas y devuelve solo eventos útiles.

        Args:
            batch: Lista de entradas crudas del ADB (cada una con "raw_payload")

        Returns:
            Lista de entradas enriquecidas con métricas de señal
        """
        if not batch:
            return []

        refined = []

        for entry in batch:
            enriched = self._analyze_entry(entry)
            if enriched:
                refined.append(enriched)
                if self.enable_stats:
                    self._update_stats(enriched)
            elif self.enable_stats:
                self.stats["filtered_events"] += 1

            if self.enable_stats:
                self.stats["total_processed"] += 1

        if refined and self.debug_mode:
            self.logger.debug(
                f"Batch: {len(refined)} útiles / {len(batch)} totales "
                f"({len(batch) - len(refined)} descartados)"
            )

        return refined

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
                "avg_quality": 0.0
            }

        # Extraer métricas
        signal_metrics = [e.get("metrics", {}) for e in refined_batch]

        # Promedios
        dbm_vals = [m["dbm"] for m in signal_metrics if "dbm" in m]
        rsrp_vals = [m["rsrp"] for m in signal_metrics if "rsrp" in m]
        rsrq_vals = [m["rsrq"] for m in signal_metrics if "rsrq" in m]
        snr_vals = [m["snr"] for m in signal_metrics if "snr" in m]

        # RAT más común
        rat_counts = defaultdict(int)
        for m in signal_metrics:
            if "rat" in m:
                rat_counts[m["rat"]] += 1
        most_common_rat = max(rat_counts.items(), key=lambda x: x[1])[0] if rat_counts else "Unknown"

        # Calidad promedio
        quality_vals = [e.get("signal_quality", 0) for e in refined_batch]
        avg_quality = sum(quality_vals) / len(quality_vals) if quality_vals else 0

        # Tendencia
        trend = self._calculate_trend(quality_vals)

        summary = {
            "status": "success",
            "total_events": len(refined_batch),
            "timestamp": refined_batch[-1].get("timestamp"),
            "avg_signal": self._safe_average(dbm_vals) or self._safe_average(rsrp_vals),
            "avg_rsrp": self._safe_average(rsrp_vals),
            "avg_rsrq": self._safe_average(rsrq_vals),
            "avg_snr": self._safe_average(snr_vals),
            "common_rat": most_common_rat,
            "alerts": sum(1 for e in refined_batch if e.get("alerts")),
            "total_alerts": len([a for e in refined_batch for a in e.get("alerts", [])]),
            "avg_quality": avg_quality,
            "security_issues": sum(1 for e in refined_batch if "SECURITY" in str(e.get("alerts", []))),
            "trend": trend,
            "quality_category": self._get_quality_category(avg_quality)
        }

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
        """Obtiene las métricas más recientes del lote."""
        if not refined_batch:
            return {"has_data": False, "signal": None, "quality": 0, "rat": "Unknown"}

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

    # ------------------------------------------------------------------------
    # Métodos internos de procesamiento
    # ------------------------------------------------------------------------

    def _analyze_entry(self, entry: Dict) -> Optional[Dict]:
        """Analiza una entrada, extrae métricas y enriquece el diccionario."""
        payload = entry.get("raw_payload", "")
        if not payload:
            return None

        metrics = SignalMetrics()
        has_any = False

        for key, pattern in self.patterns.items():
            match = pattern.search(payload)
            if match:
                val = match.group("val")
                converted = self._convert_metric_value(key, val)
                if converted is not None:
                    setattr(metrics, key, converted)
                    has_any = True

        if not has_any:
            return None

        # Enriquecer entrada
        entry.update({
            "metrics": metrics.to_dict(),
            "has_signal_data": metrics.get_primary_signal() is not None,
            "alerts": self._generate_alerts(metrics),
            "signal_quality": self._calculate_quality_score(metrics),
            "timestamp": entry.get("timestamp")
        })
        return entry

    def _convert_metric_value(self, metric: str, value: str) -> Optional[Any]:
        """Convierte y valida el valor de una métrica."""
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
                return value.strip()
        except (ValueError, TypeError):
            if self.debug_mode:
                self.logger.debug(f"Error convertiendo {metric}={value}")
            return None

    def _generate_alerts(self, metrics: SignalMetrics) -> List[str]:
        """Genera alertas basadas en las métricas."""
        alerts = []
        signal = metrics.get_primary_signal()

        if signal is not None:
            if signal <= self.thresholds["critical_signal"]:
                alerts.append(f"🔴 CRITICAL: Señal extremadamente débil ({signal} dBm)")
                if self.enable_stats:
                    self.stats["alerts_generated"] += 1
            elif signal <= self.thresholds["weak_signal"]:
                alerts.append(f"🟡 WARNING: Señal débil ({signal} dBm)")

        if metrics.rsrq is not None and metrics.rsrq <= self.thresholds["poor_quality"]:
            alerts.append(f"🟡 WARNING: Mala calidad (RSRQ: {metrics.rsrq})")

        if metrics.snr is not None and metrics.snr <= self.thresholds["low_snr"]:
            alerts.append(f"🟡 WARNING: SNR baja ({metrics.snr} dB)")

        if metrics.ciphering == 0:
            alerts.append("🔴 SECURITY: Cifrado desactivado - Posible celda falsa")

        return alerts

    def _calculate_quality_score(self, metrics: SignalMetrics) -> float:
        """Calcula un score de calidad ponderado (0-100)."""
        scores = []
        weights = []

        signal = metrics.get_primary_signal()
        if signal is not None:
            # Mapeo lineal: -40 = 100, -120 = 0
            score = max(0, min(100, int((signal + 120) / 0.8)))
            scores.append(score)
            weights.append(self.quality_weights["signal"])

        if metrics.rsrq is not None:
            # RSRQ: -3 = 100, -20 = 0
            score = max(0, min(100, int((metrics.rsrq + 20) / 0.17)))
            scores.append(score)
            weights.append(self.quality_weights["rsrq"])

        if metrics.snr is not None:
            # SNR: 30 = 100, 0 = 0
            score = max(0, min(100, int(metrics.snr * 3.33)))
            scores.append(score)
            weights.append(self.quality_weights["snr"])

        if not scores:
            return 0.0

        total_weight = sum(weights)
        if total_weight == 0:
            return sum(scores) / len(scores)

        weighted = sum(s * w for s, w in zip(scores, weights)) / total_weight
        return min(100.0, max(0.0, weighted))

    # ------------------------------------------------------------------------
    # Métodos auxiliares de estadísticas
    # ------------------------------------------------------------------------

    def _update_stats(self, entry: Dict) -> None:
        """Actualiza estadísticas con una entrada procesada."""
        if not self.enable_stats:
            return

        self.stats["useful_events"] += 1

        metrics = entry.get("metrics", {})
        for key in metrics:
            self.stats["metrics_found"][key] += 1

        if "rat" in metrics:
            self.stats["rat_distribution"][metrics["rat"]] += 1

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
        """Calcula tendencia: improving, degrading, stable."""
        if len(quality_values) < 3:
            return "stable"
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
        """Clasifica la calidad en categorías."""
        if quality >= 70:
            return "excellent"
        elif quality >= 40:
            return "good"
        else:
            return "poor"

    # ------------------------------------------------------------------------
    # Métodos públicos adicionales
    # ------------------------------------------------------------------------

    def reset_stats(self) -> None:
        """Reinicia las estadísticas del procesador."""
        if self.enable_stats:
            self._reset_stats()
            self.logger.info("Estadísticas reiniciadas")

    def get_pattern_stats(self) -> Dict[str, int]:
        """Obtiene estadísticas de uso de patrones (debugging)."""
        if not self.enable_stats:
            return {}
        return dict(self.stats["metrics_found"])

    def get_quality_distribution(self) -> Dict[str, int]:
        """Obtiene la distribución de calidad de los eventos procesados."""
        if not self.enable_stats:
            return {}
        return dict(self.stats["quality_distribution"])

    def get_rat_distribution(self) -> Dict[str, int]:
        """Obtiene la distribución de tecnologías de red."""
        if not self.enable_stats:
            return {}
        return dict(self.stats["rat_distribution"])


# ------------------------------------------------------------------------
# Prueba rápida (ejecutar directamente)
# ------------------------------------------------------------------------

if __name__ == "__main__":
    # Datos de prueba simulados (como los que enviaría ADB_input)
    test_batch = [
        {"raw_payload": "SignalStrength: dbm=-85 rsrp=-87 rsrq=-12 rat=LTE cellIdentityCellid=12345 ciphering=1"},
        {"raw_payload": "SignalStrength: dbm=-115 rsrp=-118 rsrq=-18 rat=NR ciphering=0 snr=3"},
        {"raw_payload": "Sistema operativo: Android 12 - Mensaje irrelevante"},
        {"raw_payload": "SignalStrength: dbm=-65 rsrp=-67 rsrq=-5 rat=LTE band=3 snr=25"},
    ]

    logic = SignalLogic(enable_stats=True, debug_mode=True)
    refined = logic.process_batch(test_batch)
    summary = logic.get_summary(refined)

    print("\n=== RESULTADOS ===")
    for i, entry in enumerate(refined):
        print(f"\nEvento {i+1}:")
        print(f"  Métricas: {entry['metrics']}")
        print(f"  Alertas: {entry.get('alerts', [])}")
        print(f"  Calidad: {entry.get('signal_quality', 0):.1f}/100")

    print("\n=== RESUMEN ===")
    for k, v in summary.items():
        if k != "processor_stats":
            print(f"{k}: {v}")

    print("\n=== ESTADÍSTICAS DE PATRONES ===")
    for pat, cnt in logic.get_pattern_stats().items():
        print(f"  {pat}: {cnt}")

    print("\n✅ Prueba completada")
