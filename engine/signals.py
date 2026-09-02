"""
MAIA · Señales por creativo y veredicto por umbral (sin LLM).

Para cada anuncio: hook rate / hold (solo video), CTR / CPC (imagen y video),
ROAS vs objetivo, fatiga (tendencia), y en qué escalón del embudo se rompe.
De ahí sale un veredicto por REGLAS: escalar / iterar / matar / poca data.

La lógica es transparente y auditable: cada veredicto viene con sus "motivos".
"""
from __future__ import annotations
from typing import Dict, List, Optional
import re

from .config import Thresholds, VERDICTS, SIGNAL_LEVELS
from .metrics import (
    _sum_rows, compute_metrics, data_sufficiency, build_funnel,
    split_trend_halves, _safe_div,
)

VIDEO_HINT = re.compile(r"\b(video|reel|vid|vsl|ugc|tiktok|gancho|hook)\b", re.I)


def guess_format(ad_name: str, totals: dict) -> str:
    """video si el nombre lo sugiere o si hubo reproducciones de video."""
    if totals.get("video_3s", 0) > 0 or totals.get("thruplay", 0) > 0:
        return "video"
    if VIDEO_HINT.search(ad_name or ""):
        return "video"
    return "imagen"


def signal_strength(totals: dict, objective: str, th: Thresholds) -> dict:
    """Semáforo de CONFIANZA (fuerza de señal), independiente del veredicto.

    Responde: ¿cuánto podemos confiar en la lectura de este creativo?
      - 🟢 fuerte : hay conversiones suficientes -> el ROAS/CPA es confiable.
      - 🟡 media  : hay pocas conversiones (tendencia) o volumen alto de embudo
                    (impresiones/clics) -> se lee por CTR/hook, no por ROAS.
      - ⚪ sin    : ni siquiera hay volumen arriba -> no se puede decir nada.
    """
    impr = float(totals.get("impressions", 0) or 0)
    clicks = float(totals.get("link_clicks", 0) or 0)
    if objective == "ventas":
        conv = float(totals.get("purchases", 0) or 0)
        c_strong, c_med, unit = th.conf_conv_strong, th.conf_conv_medium, "compras"
    else:
        conv = float(totals.get("leads", 0) or 0)
        c_strong, c_med, unit = th.conf_leads_strong, th.conf_leads_medium, "leads"

    if conv >= c_strong:
        level = "fuerte"
        basis = f"{int(conv)} {unit} → alcanza para confiar en el ROAS/CPA"
    elif conv >= c_med or impr >= th.conf_impr_medium:
        level = "media"
        if conv >= c_med:
            basis = f"{int(conv)} {unit}: tendencia, no confirmación (firme con ≥{c_strong})"
        else:
            basis = (f"{int(conv)} {unit} · {int(impr)} impr: alcanza para leer "
                     f"CTR/hook, todavía no el ROAS")
    else:
        level = "sin"
        basis = f"{int(impr)} impr: sin volumen para juzgar (piso {th.conf_impr_medium})"

    return {"level": level, "basis": basis, **SIGNAL_LEVELS[level]}


def detect_fatigue(rows: List[dict], th: Thresholds) -> dict:
    early, late = split_trend_halves(rows)
    if not early or not late:
        return {"fatigue": False, "reason": "poca serie temporal"}
    def ctr(t):
        return _safe_div(t["link_clicks"], t["impressions"]) or 0.0
    def cpm(t):
        return _safe_div(t["spend"] * 1000, t["impressions"]) or 0.0
    ctr_drop = _safe_div(ctr(early) - ctr(late), ctr(early)) or 0.0
    cpm_rise = _safe_div(cpm(late) - cpm(early), cpm(early)) or 0.0
    fat = ctr_drop >= th.fatigue_ctr_drop and cpm_rise >= th.fatigue_cpm_rise
    return {
        "fatigue": bool(fat),
        "ctr_drop_pct": round(ctr_drop * 100, 1),
        "cpm_rise_pct": round(cpm_rise * 100, 1),
        "reason": ("CTR ↓ y CPM ↑ en la 2da mitad" if fat else "estable"),
    }


def creative_signal(ad: dict, rows: List[dict], objective: str,
                    target_roas: float, th: Thresholds) -> dict:
    """Calcula señales + veredicto para un anuncio."""
    totals = _sum_rows(rows)
    fmt = ad.get("format") or guess_format(ad.get("ad_name", ""), totals)
    is_video = (fmt == "video")
    m = compute_metrics(totals, is_video=is_video)
    suff = data_sufficiency(totals, objective, th)
    fatigue = detect_fatigue(rows, th)
    funnel = build_funnel(totals, objective)

    motives: List[str] = []

    # --- señales individuales (banderas) ---
    flags = {}
    if is_video:
        hr, hd = m["hook_rate"], m["hold_rate"]
        flags["hook"] = _band(hr, th.hook_rate_bad, th.hook_rate_good)
        flags["hold"] = _band(hd, th.hold_rate_bad, th.hold_rate_good)
        if flags["hook"] == "bad":
            motives.append(f"Hook bajo ({hr}%): el arranque no frena el scroll")
        if flags["hold"] == "bad":
            motives.append(f"Hold bajo ({hd}%): no retiene después del gancho")
    flags["ctr"] = _band(m["ctr"], th.ctr_bad, th.ctr_good)
    if flags["ctr"] == "bad":
        motives.append(f"CTR bajo ({m['ctr']}%): el mensaje no genera clic")

    # ROAS relativo al objetivo (solo ventas)
    roas_ratio = None
    if objective == "ventas" and m["roas"] is not None and target_roas:
        roas_ratio = round(m["roas"] / target_roas, 2)

    # --- veredicto por reglas ---
    if not suff["enough"]:
        verdict = "poca_data"
        motives.insert(0, f"Poca data ({suff['reason']}): dejar correr / mirar señales de arriba")
    elif objective == "ventas":
        verdict = _verdict_sales(m, roas_ratio, flags, fatigue, funnel, th, motives)
    else:
        verdict = _verdict_leads(m, flags, fatigue, th, motives)

    # dónde se rompe: cuello de botella del propio creativo
    breaks_at = funnel.get("bottleneck_step")

    return {
        "ad_name": ad.get("ad_name"),
        "campaign": ad.get("campaign"),
        "adset": ad.get("adset"),
        "format": fmt,
        "metrics": m,
        "roas_vs_target": roas_ratio,
        "flags": flags,
        "fatigue": fatigue,
        "breaks_at": breaks_at,
        "breaks_at_label": funnel.get("diagnosis"),
        "verdict": verdict,
        "verdict_meta": VERDICTS[verdict],
        "confidence": signal_strength(totals, objective, th),
        "motives": motives[:4],
        "data_enough": suff["enough"],
    }


def _band(v: Optional[float], bad: float, good: float) -> str:
    if v is None:
        return "na"
    if v >= good:
        return "good"
    if v <= bad:
        return "bad"
    return "mid"


def _verdict_sales(m, roas_ratio, flags, fatigue, funnel, th: Thresholds, motives) -> str:
    roas_ratio = roas_ratio if roas_ratio is not None else 0
    top_ok = flags.get("ctr") in ("good", "mid") and flags.get("hook", "good") in ("good", "mid")

    # MATAR: ROAS muy por debajo del objetivo con señales flojas.
    if roas_ratio and roas_ratio < th.roas_kill_ratio and not top_ok:
        motives.append(f"ROAS {round(roas_ratio,2)}x del objetivo con señales flojas")
        return "matar"

    # ESCALAR: ROAS holgado sobre el objetivo y sin fatiga.
    if roas_ratio >= th.roas_scale_ratio and not fatigue["fatigue"]:
        motives.insert(0, f"ROAS {round(roas_ratio,2)}x del objetivo, sin fatiga → escalar")
        return "escalar"

    # Fatiga sobre un ganador → iterar (renovar antes de que caiga).
    if fatigue["fatigue"]:
        motives.insert(0, "Fatiga incipiente (CTR↓/CPM↑) → renovar variante")
        return "iterar"

    # ROAS cerca del objetivo pero embudo roto → iterar apuntando al cuello.
    return "iterar"


def _verdict_leads(m, flags, fatigue, th: Thresholds, motives) -> str:
    if flags.get("ctr") == "bad" and fatigue["fatigue"]:
        motives.insert(0, "CPL en riesgo: CTR bajo + fatiga → renovar")
        return "iterar"
    if flags.get("ctr") == "good" and not fatigue["fatigue"]:
        motives.insert(0, "CTR sano y estable → sostener / escalar")
        return "escalar"
    if fatigue["fatigue"]:
        motives.insert(0, "Fatiga → renovar creativo")
        return "iterar"
    return "iterar"


def rank_creatives(signals: List[dict], objective: str) -> List[dict]:
    """Ordena: primero escalar (mejor ROAS), después iterar, matar, poca data."""
    order = {"escalar": 0, "iterar": 1, "matar": 2, "poca_data": 3}
    def key(s):
        r = s["metrics"].get("roas") or 0
        vol = s["metrics"].get("purchases") or s["metrics"].get("leads") or 0
        return (order.get(s["verdict"], 9), -(r if objective == "ventas" else vol))
    return sorted(signals, key=key)
