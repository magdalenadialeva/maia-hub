"""
MAIA · Emisor del formato compacto que consume el hub (data.js / data.json).

El hub guarda por cliente un objeto columnar:
    { name, cur, obj, start, lastchg, ads, dates, f, rows }
donde:
    f    = ["spend","impr","reach","lc","lpv","atc","ic","purch","pval","leads","v3s","thru"]
    rows = [ [dateIdx, adIdx, <12 valores en el orden de f>], ... ]  (solo días con actividad)

Este módulo convierte las filas normalizadas del parser a ESE formato exacto,
para que el hub existente funcione sin cambios (solo se desengancha el DATA
embebido y se lee desde data.js). Sin LLM.
"""
from __future__ import annotations
from typing import Dict, List
import math

from .config import DEFAULT_THRESHOLDS, target_roas_from_margin

# Orden EXACTO de campos que espera el hub (no cambiar).
HUB_FIELDS = ["spend", "impr", "reach", "lc", "lpv", "atc", "ic",
              "purch", "pval", "leads", "v3s", "thru"]

# Campo del hub  ->  campo interno del parser.
FIELD_SRC = {
    "spend": "spend", "impr": "impressions", "reach": "reach", "lc": "link_clicks",
    "lpv": "lpv", "atc": "atc", "ic": "ic", "purch": "purchases",
    "pval": "revenue", "leads": "leads", "v3s": "video_3s", "thru": "thruplay",
}
INT_FIELDS = {"impr", "reach", "lc", "lpv", "atc", "ic", "purch", "leads", "v3s", "thru"}


def _num(x) -> float:
    if x is None:
        return 0.0
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(v) else v


def build_hub_client(client: dict, rows: List[dict], currency: str | None) -> dict:
    """rows = filas normalizadas (una por anuncio-día) de UN cliente."""
    objective = client.get("objective", "ventas")
    is_leads = (objective == "leads")

    _SIGNAL = ["spend", "impressions", "link_clicks", "lpv", "atc", "ic",
               "purchases", "revenue", "results", "video_3s", "thruplay"]

    def _active(r) -> bool:
        # Se conserva la fila si tiene CUALQUIER señal (gasto, impresiones o
        # una conversión atribuida). Solo se descartan filas 100% vacías, así
        # no se pierde ninguna compra/lead por ventana de atribución.
        return any(_num(r.get(k)) > 0 for k in _SIGNAL)

    active_rows = [r for r in rows if r.get("date") and _active(r)]

    # Orden estable: anuncios por primera aparición (solo los que tienen
    # actividad), fechas ascendentes.
    ads: List[str] = []
    ad_idx: Dict[str, int] = {}
    dates_set = set()
    for r in active_rows:
        name = r.get("ad_name") or "sin nombre"
        if name not in ad_idx:
            ad_idx[name] = len(ads)
            ads.append(name)
        dates_set.add(r["date"])
    dates = sorted(dates_set)
    date_idx = {d: i for i, d in enumerate(dates)}

    # Consolidar por (ad, date) por si hubiera filas repetidas.
    bucket: Dict[tuple, Dict[str, float]] = {}
    for r in active_rows:
        key = (date_idx[r["date"]], ad_idx[r.get("ad_name") or "sin nombre"])
        acc = bucket.setdefault(key, {f: 0.0 for f in HUB_FIELDS})
        for hub_f, src in FIELD_SRC.items():
            if hub_f == "leads":
                # leads solo para lead-gen: viene de "results" (Resultados de Meta).
                val = _num(r.get("results")) if is_leads else 0.0
            else:
                val = _num(r.get(src))
            acc[hub_f] += val

    out_rows = []
    for (di, ai), vals in sorted(bucket.items()):
        row = [di, ai]
        for f in HUB_FIELDS:
            v = vals[f]
            row.append(int(round(v)) if f in INT_FIELDS else round(v, 2))
        out_rows.append(row)

    # Umbrales canónicos (fuente única: engine/config.py) para que el hub
    # calcule el MISMO veredicto que el reporte Python. Sin esto, el JS tenía
    # sus propias reglas y no coincidía (p.ej. escalar con 1 sola compra).
    th = DEFAULT_THRESHOLDS.merged(client.get("overrides"))
    target = target_roas_from_margin(client.get("margin"))
    th_out = {
        "minP": th.min_purchases_sales,   # compras mínimas para veredicto firme
        "minSpend": th.min_spend_sales,   # gasto mínimo para juzgar (ventas)
        "minImpr": th.min_impressions,    # piso de impresiones
        "minLeads": th.min_leads,         # leads mínimos (lead-gen)
        "tgt": target,                    # ROAS objetivo del cliente (de su margen)
        "scale": th.roas_scale_ratio,     # >= tgt*scale -> escalar
        "kill": th.roas_kill_ratio,       # <  tgt*kill  -> candidato a matar
        "ctrBad": th.ctr_bad,             # CTR por debajo = señal floja
        "hookBad": th.hook_rate_bad,      # hook por debajo = señal floja (video)
        # Semáforo de confianza (fuerza de señal) — mismos umbrales que el reporte.
        "cCS": th.conf_conv_strong, "cCM": th.conf_conv_medium,
        "cLS": th.conf_leads_strong, "cLM": th.conf_leads_medium,
        "cIM": th.conf_impr_medium,
    }

    return {
        "name": client.get("name", client["slug"]),
        "cur": client.get("currency") or currency or "ARS",
        "obj": client.get("obj_code") or ("lead" if is_leads else "purchase"),
        "start": client.get("start_label") or client.get("start_maia") or "",
        "lastchg": client.get("lastchg"),
        "th": th_out,
        "ads": ads,
        "dates": dates,
        "f": list(HUB_FIELDS),
        "rows": out_rows,
    }
