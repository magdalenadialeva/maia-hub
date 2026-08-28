"""
MAIA · Configuración del motor (umbrales, benchmarks, reglas de veredicto).

Todo lo "de criterio" vive acá para que se ajuste sin tocar la lógica.
Nada de esto depende de un LLM: son reglas puras.

Los valores por defecto salen del Manual y del Estado de Clientes de MAIA.
Se pueden pisar por cliente desde config/clients.yaml (bloque `overrides`).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Benchmarks de embudo (conversión sana de un paso al siguiente, %).
# Fuente: diagnóstico MAIA (Piera, Mossy, Vresia) del período 27jul–25ago.
# "sano" = objetivo de referencia; por debajo de `alert_ratio`*sano se marca
# el paso como cuello de botella.
# ---------------------------------------------------------------------------
FUNNEL_STEPS_SALES = ["impressions", "link_clicks", "lpv", "atc", "ic", "purchases"]
FUNNEL_STEPS_LEADS = ["impressions", "link_clicks", "leads"]

# Etiquetas legibles para el hub / reporte.
STEP_LABELS = {
    "impressions": "Impresiones",
    "reach": "Alcance",
    "clicks": "Clics (todos)",
    "link_clicks": "Clics en el enlace",
    "lpv": "Vieron la landing (LPV)",
    "atc": "Agregaron al carrito",
    "ic": "Iniciaron el pago",
    "purchases": "Compraron",
    "leads": "Leads",
}

# Conversión sana esperada de cada transición (paso[i] -> paso[i+1]), en %.
HEALTHY_FUNNEL_SALES = {
    "link_clicks->lpv": 80.0,   # de los que clickean, la mayoría debería ver la landing
    "lpv->atc": 13.0,           # Piera: sano ≈13% (hoy 2%)
    "atc->ic": 45.0,            # Mossy/Vresia: sano ≈45% (hoy 10.8/12.8%)
    "ic->purchases": 45.0,      # iniciaron pago -> compraron
}
HEALTHY_FUNNEL_LEADS = {
    "link_clicks->leads": 20.0,  # Paz: Clics->Leads sano ≈20-25%
}

# ---------------------------------------------------------------------------
# Umbrales de señales por creativo (ecommerce / DTC, Meta).
# Todo en fracción de la métrica cruda; los % se expresan 0-100.
# ---------------------------------------------------------------------------
@dataclass
class Thresholds:
    # --- Suficiencia de datos ("poca data") ---
    min_spend_sales: float = 8000.0     # gasto mínimo (moneda local) para juzgar
    min_purchases_sales: int = 3        # compras mínimas para veredicto firme
    min_leads: int = 15                 # leads mínimos para veredicto firme (lead-gen)
    min_impressions: int = 1500         # piso de impresiones para señales de video/CTR

    # --- Video ---
    hook_rate_good: float = 30.0        # % (rep. 3s / impresiones): thumb-stop sano
    hook_rate_bad: float = 20.0         # por debajo: el arranque no frena el scroll
    hold_rate_good: float = 20.0        # % (ThruPlay / rep. 3s): retención sana
    hold_rate_bad: float = 10.0

    # --- Click / imagen ---
    ctr_good: float = 1.2               # % CTR link sano ecommerce
    ctr_bad: float = 0.6
    # CPC se juzga relativo al promedio del cliente (ratio), no absoluto.
    cpc_bad_ratio: float = 1.5          # CPC > 1.5x el promedio del cliente = caro

    # --- ROAS relativo al objetivo del cliente ---
    roas_scale_ratio: float = 1.2       # ROAS >= 1.2x objetivo -> candidato a escalar
    roas_kill_ratio: float = 0.6        # ROAS <  0.6x objetivo -> candidato a matar

    # --- Fatiga (tendencia dentro de la ventana) ---
    fatigue_ctr_drop: float = 0.20      # caída relativa de CTR (mitad tardía vs temprana)
    fatigue_cpm_rise: float = 0.20      # suba relativa de CPM
    fatigue_freq: float = 2.5           # frecuencia por encima de la cual vigilar

    def merged(self, overrides: Optional[dict]) -> "Thresholds":
        if not overrides:
            return self
        base = asdict(self)
        base.update({k: v for k, v in overrides.items() if k in base})
        return Thresholds(**base)


# ROAS objetivo por defecto si el cliente no declara margen.
# (Con margen bruto m, breakeven ROAS = 1/m; objetivo = breakeven / margen_seguridad.)
DEFAULT_TARGET_ROAS = 2.0
DEFAULT_MARGIN = 0.5           # 50% margen bruto -> breakeven ROAS = 2.0
TARGET_ROAS_SAFETY = 1.0       # objetivo = breakeven (subir a >1 para exigir ganancia)

def target_roas_from_margin(margin: Optional[float]) -> float:
    if not margin or margin <= 0:
        return DEFAULT_TARGET_ROAS
    breakeven = 1.0 / margin
    return round(breakeven * TARGET_ROAS_SAFETY, 2)


# Etiquetas y colores de veredicto (deben matchear el hub).
VERDICTS = {
    "escalar":   {"label": "Escalar",   "color": "#16a34a", "emoji": "🟢"},
    "iterar":    {"label": "Iterar",    "color": "#f59e0b", "emoji": "🟡"},
    "matar":     {"label": "Matar",     "color": "#dc2626", "emoji": "🔴"},
    "poca_data": {"label": "Poca data", "color": "#94a3b8", "emoji": "⚪"},
}

DEFAULT_THRESHOLDS = Thresholds()
