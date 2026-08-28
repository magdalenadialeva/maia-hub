"""
MAIA · Motor de métricas, embudo y agregación (sin LLM).

Toma las filas normalizadas (una por anuncio-día) y calcula:
  - métricas por anuncio y por cliente (ROAS, CPA/CPL, CPM, CTR, CPC, hook, hold, freq)
  - embudo a total campaña con el paso "cuello de botella"
  - agregación por período (7d / 30d / rango exacto) desde la data diaria
  - significancia simple (suficiencia de datos por creativo)
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional
import math
import pandas as pd

from .config import (
    FUNNEL_STEPS_SALES, FUNNEL_STEPS_LEADS, STEP_LABELS,
    HEALTHY_FUNNEL_SALES, HEALTHY_FUNNEL_LEADS, Thresholds,
)

SUM_FIELDS = [
    "spend", "impressions", "reach", "clicks", "link_clicks",
    "video_3s", "thruplay", "video_25", "video_50", "video_75", "video_100",
    "lpv", "atc", "ic", "purchases", "revenue", "leads",
]


def _g(d: dict, k: str) -> float:
    v = d.get(k, 0.0)
    return 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _safe_div(a: float, b: float) -> Optional[float]:
    return (a / b) if b else None


def _sum_rows(rows: List[dict]) -> Dict[str, float]:
    tot = {f: 0.0 for f in SUM_FIELDS}
    for r in rows:
        for f in SUM_FIELDS:
            tot[f] += _g(r, f)
    return tot


def compute_metrics(totals: Dict[str, float], is_video: bool = True) -> Dict[str, Optional[float]]:
    """Métricas derivadas a partir de sumas crudas."""
    imp = totals["impressions"]
    spend = totals["spend"]
    m: Dict[str, Optional[float]] = {}
    m["spend"] = round(spend, 2)
    m["impressions"] = int(imp)
    m["reach"] = int(totals["reach"])
    m["purchases"] = int(totals["purchases"])
    m["leads"] = int(totals["leads"])
    m["revenue"] = round(totals["revenue"], 2)
    m["roas"] = _rnd(_safe_div(totals["revenue"], spend), 2)
    m["cpa"] = _rnd(_safe_div(spend, totals["purchases"]), 0)
    m["cpl"] = _rnd(_safe_div(spend, totals["leads"]), 0)
    m["cpm"] = _rnd(_safe_div(spend * 1000, imp), 0)
    m["ctr"] = _rnd(_pct(_safe_div(totals["link_clicks"], imp)), 2)      # % CTR link
    m["cpc"] = _rnd(_safe_div(spend, totals["link_clicks"]), 2)
    m["frequency"] = _rnd(_safe_div(imp, totals["reach"]), 2)
    # video
    m["hook_rate"] = _rnd(_pct(_safe_div(totals["video_3s"], imp)), 2) if is_video else None
    m["hold_rate"] = _rnd(_pct(_safe_div(totals["thruplay"], totals["video_3s"])), 2) if is_video else None
    m["conv_rate"] = _rnd(_pct(_safe_div(totals["purchases"], totals["link_clicks"])), 2)
    return m


def _pct(x: Optional[float]) -> Optional[float]:
    return None if x is None else x * 100.0


def _rnd(x: Optional[float], nd: int) -> Optional[float]:
    if x is None:
        return None
    return round(x, nd) if nd > 0 else round(x)


# ---------------------------------------------------------------------------
# Embudo
# ---------------------------------------------------------------------------
def build_funnel(totals: Dict[str, float], objective: str) -> dict:
    """
    Devuelve el embudo a total campaña: cada paso con su volumen, la conversión
    hacia el paso siguiente, el benchmark sano y si es el cuello de botella.
    """
    steps = FUNNEL_STEPS_SALES if objective == "ventas" else FUNNEL_STEPS_LEADS
    healthy = HEALTHY_FUNNEL_SALES if objective == "ventas" else HEALTHY_FUNNEL_LEADS

    nodes = []
    for i, s in enumerate(steps):
        vol = int(totals.get(s, 0.0))
        node = {"step": s, "label": STEP_LABELS.get(s, s), "value": vol}
        if i < len(steps) - 1:
            nxt = steps[i + 1]
            conv = _safe_div(totals.get(nxt, 0.0), totals.get(s, 0.0))
            conv_pct = _rnd(_pct(conv), 1)
            bench = healthy.get(f"{s}->{nxt}")
            node["conv_to_next_pct"] = conv_pct
            node["healthy_pct"] = bench
            # ratio de salud: conv / benchmark
            node["health_ratio"] = (round(conv_pct / bench, 2)
                                    if (conv_pct is not None and bench) else None)
        nodes.append(node)

    # cuello de botella = transición con menor health_ratio (peor vs su benchmark)
    scored = [n for n in nodes if n.get("health_ratio") is not None]
    bottleneck = min(scored, key=lambda n: n["health_ratio"]) if scored else None
    bn_step = bottleneck["step"] if bottleneck else None

    diagnosis = None
    if bottleneck:
        nxt_label = STEP_LABELS.get(_next_step(steps, bn_step), "")
        diagnosis = (f"El cuello está en {bottleneck['label']} → {nxt_label}: "
                     f"{bottleneck['conv_to_next_pct']}% vs ~{bottleneck['healthy_pct']}% sano.")
    return {
        "objective": objective,
        "steps": nodes,
        "bottleneck_step": bn_step,
        "diagnosis": diagnosis,
    }


def _next_step(steps: List[str], step: str) -> Optional[str]:
    i = steps.index(step)
    return steps[i + 1] if i + 1 < len(steps) else None


# ---------------------------------------------------------------------------
# Significancia / suficiencia de datos
# ---------------------------------------------------------------------------
def data_sufficiency(totals: Dict[str, float], objective: str, th: Thresholds) -> dict:
    """¿Alcanza la data para un veredicto firme? Regla simple, sin LLM."""
    if objective == "ventas":
        enough = (totals["spend"] >= th.min_spend_sales
                  and totals["purchases"] >= th.min_purchases_sales) \
                 or totals["purchases"] >= th.min_purchases_sales * 2
        reason = f"{int(totals['purchases'])} compras · gasto {int(totals['spend'])}"
    else:
        enough = totals["leads"] >= th.min_leads
        reason = f"{int(totals['leads'])} leads"
    enough = enough and totals["impressions"] >= th.min_impressions
    return {"enough": bool(enough), "reason": reason}


def two_prop_ztest(x1: float, n1: float, x2: float, n2: float) -> Optional[float]:
    """z de diferencia de proporciones (p.ej. CTR del creativo vs cuenta). Devuelve |z|."""
    if not n1 or not n2:
        return None
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    return abs((p1 - p2) / se)


# ---------------------------------------------------------------------------
# Agregación por período desde data diaria
# ---------------------------------------------------------------------------
def rows_in_range(rows: List[dict], start: Optional[str], end: Optional[str]) -> List[dict]:
    if not start and not end:
        return rows
    out = []
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append(r)
    return out


def period_bounds(all_dates: List[str], window: str) -> Dict[str, Optional[str]]:
    """window in {'7d','30d','all'} -> {start,end} usando la última fecha del dataset."""
    ds = sorted(d for d in all_dates if d)
    if not ds:
        return {"start": None, "end": None}
    end = ds[-1]
    if window == "all":
        return {"start": ds[0], "end": end}
    days = 7 if window == "7d" else 30
    end_d = date.fromisoformat(end)
    start_d = end_d - timedelta(days=days - 1)
    return {"start": start_d.isoformat(), "end": end}


def split_trend_halves(rows: List[dict]) -> tuple[Dict[str, float], Dict[str, float]]:
    """Divide las filas por fecha en mitad temprana / tardía (para fatiga)."""
    dated = sorted([r for r in rows if r.get("date")], key=lambda r: r["date"])
    if len(dated) < 4:
        return {}, {}
    mid = len(dated) // 2
    return _sum_rows(dated[:mid]), _sum_rows(dated[mid:])
