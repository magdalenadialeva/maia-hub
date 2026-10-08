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
              "purch", "pval", "leads", "v3s", "thru", "msgs", "lds"]
# (msgs = conversaciones de mensajería y lds = leads crudos, agregados oct-2026
#  al FINAL para no romper índices; el hub los lee por nombre.)

# Campo del hub  ->  campo interno del parser.
FIELD_SRC = {
    "spend": "spend", "impr": "impressions", "reach": "reach", "lc": "link_clicks",
    "lpv": "lpv", "atc": "atc", "ic": "ic", "purch": "purchases",
    "pval": "revenue", "leads": "leads", "v3s": "video_3s", "thru": "thruplay",
    "msgs": "msgs", "lds": "leads",
}
INT_FIELDS = {"impr", "reach", "lc", "lpv", "atc", "ic", "purch", "leads", "v3s", "thru",
              "msgs", "lds"}

# Objetivo de Meta -> tipo de resultado de la campaña (lo que se mide en el hub).
_SALES_OBJ = {"OUTCOME_SALES", "CONVERSIONS", "PRODUCT_CATALOG_SALES"}
_LEAD_OBJ = {"OUTCOME_LEADS", "LEAD_GENERATION"}


def camp_kind(c: dict) -> str:
    """purchase | lead | msg | traffic | other, según el objetivo de la campaña."""
    o = str(c.get("objective") or "").upper()
    if o in _SALES_OBJ:
        return "purchase"
    if o in _LEAD_OBJ:
        return "lead"
    if (c.get("msg") or 0) > 0 or o == "MESSAGES":
        return "msg"
    if o in ("LINK_CLICKS", "OUTCOME_TRAFFIC", "OUTCOME_ENGAGEMENT"):
        return "traffic"
    return "other"


def _num(x) -> float:
    if x is None:
        return 0.0
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(v) else v


def build_hub_client(client: dict, rows: List[dict], currency: str | None,
                     campaigns: List[dict] | None = None,
                     manual: List[dict] | None = None) -> dict:
    """rows = filas normalizadas (una por anuncio-día) de UN cliente.
    campaigns = exports/<slug>/<slug>_campaigns.json (para tipo de resultado y
    para mapear anuncio -> campaña cuando el CSV no trae la columna de campaña).
    manual = ventas que reporta la marca (config/manual_results.yaml)."""
    campaigns = campaigns or []
    camp_names: List[str] = [c.get("name") or "" for c in campaigns]
    camp_idx: Dict[str, int] = {n: i for i, n in enumerate(camp_names)}
    kind_by_camp = {c.get("name"): camp_kind(c) for c in campaigns}
    # Fallback anuncio -> campaña desde campaigns.json (CSV viejos sin columna).
    ad2camp_fb: Dict[str, str] = {}
    for c in campaigns:
        for an in c.get("ads") or []:
            ad2camp_fb.setdefault(an, c.get("name"))

    def _camp_of(r) -> str:
        cn = r.get("campaign")
        if isinstance(cn, str) and cn.strip() and cn.lower() != "nan":
            return cn.strip()
        return ad2camp_fb.get(r.get("ad_name") or "", "") or ""

    objective = client.get("objective", "ventas")
    obj_code = client.get("obj_code")
    is_leads = (objective == "leads")
    # El campo interno `leads` guarda el "conteo de resultado" no-venta: leads en
    # lead-gen y conversaciones en campañas de Mensajes (obj_code msg).
    has_result_count = is_leads or (obj_code == "msg")

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
    # Un "anuncio" del hub = (campaña, nombre): el mismo nombre en dos campañas
    # son dos filas distintas, así se puede mirar cada campaña por separado.
    ads: List[str] = []
    adcamp: List[int] = []
    ad_idx: Dict[tuple, int] = {}
    dates_set = set()
    for r in active_rows:
        name = r.get("ad_name") or "sin nombre"
        cn = _camp_of(r)
        if cn and cn not in camp_idx:
            camp_idx[cn] = len(camp_names)
            camp_names.append(cn)
        key = (cn, name)
        if key not in ad_idx:
            ad_idx[key] = len(ads)
            ads.append(name)
            adcamp.append(camp_idx[cn] if cn else -1)
        dates_set.add(r["date"])
    dates = sorted(dates_set)
    date_idx = {d: i for i, d in enumerate(dates)}

    # Consolidar por (ad, date) por si hubiera filas repetidas.
    bucket: Dict[tuple, Dict[str, float]] = {}
    for r in active_rows:
        cn = _camp_of(r)
        key = (date_idx[r["date"]], ad_idx[(cn, r.get("ad_name") or "sin nombre")])
        acc = bucket.setdefault(key, {f: 0.0 for f in HUB_FIELDS})
        ck = kind_by_camp.get(cn)
        for hub_f, src in FIELD_SRC.items():
            if hub_f == "leads":
                # conteo de resultado no-venta (leads o conversaciones). Si la
                # campaña es de mensajes / leads y el CSV trae la columna propia,
                # se usa esa; si no, "Resultados" de Meta (como antes).
                if ck == "msg" and _num(r.get("msgs")) > 0:
                    val = _num(r.get("msgs"))
                elif ck == "lead" and _num(r.get("leads")) > 0:
                    val = _num(r.get("leads"))
                else:
                    val = _num(r.get("results")) if has_result_count else 0.0
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
        # Fecha de inicio de pauta con MAIA (ISO) -> columna "Inicio pauta".
        "startIso": str(client.get("start_maia") or "") or None,
        "endIso": str(client.get("end_maia") or "") or None,
        # Cuenta perdida -> va al capítulo CUENTAS PERDIDAS del General.
        "lost": str(client.get("status") or "").lower() in ("perdida", "perdido", "lost"),
        "lastchg": client.get("lastchg"),
        # Fases de optimización de la cuenta (por qué evento optimizó en cada tramo).
        # Se declaran en config/clients.yaml -> opt_phases: [{from, opt, label?}].
        # El hub las usa para (a) pintar en el gráfico los días de cada optimización
        # en un color y (b) elegir qué métrica-resultado mostrar. Vacío = una sola
        # optimización, el gráfico se comporta como siempre.
        "optphases": client.get("opt_phases") or [],
        "th": th_out,
        "ads": ads,
        "adcamp": adcamp,             # anuncio -> índice en `camps` (-1 = sin campaña)
        "camps": camp_names,          # nombres de campaña (mismo orden que campaigns.json + extras)
        "campkind": [kind_by_camp.get(n) or "other" for n in camp_names],
        "manual": manual or [],
        "dates": dates,
        "f": list(HUB_FIELDS),
        "rows": out_rows,
    }
