"""
MAIA · Fetch automático desde la API de Meta (sin LLM, sin tokens de Claude).

Corre en GitHub Actions cada mañana. Para cada cliente con `ad_account_id`
en config/clients.yaml, pide a la Graph API los insights a NIVEL ANUNCIO con
desglose DIARIO (time_increment=1) de los últimos N días, y escribe un CSV
con EXACTAMENTE los mismos encabezados que el export manual de Ads Manager.
Así el resto del pipeline (parse -> build -> report -> brandbrain) funciona
sin ningún cambio.

Diseño a prueba de fallos:
  - Sin META_TOKEN en el entorno  -> no hace nada y sale 0 (el build sigue
    con los CSV que ya estén en exports/). Nunca rompe el deploy.
  - Un cliente sin ad_account_id   -> se saltea.
  - Un error de API por cliente    -> se loguea y se sigue con el resto; el
    CSV viejo de ese cliente queda intacto.

Requiere solo la librería estándar (urllib) + PyYAML (ya en requirements).

Uso:
    META_TOKEN=xxxx python -m engine.fetch_meta
    META_TOKEN=xxxx python -m engine.fetch_meta --days 35 --exports exports
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

try:
    from zoneinfo import ZoneInfo
    _ART = ZoneInfo("America/Argentina/Buenos_Aires")
except Exception:  # pragma: no cover
    _ART = None

API_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{API_VERSION}"


def _today_art() -> str:
    """Fecha de HOY en horario de Argentina (cuando corre la actualización)."""
    now = datetime.now(_ART) if _ART else datetime.now()
    return now.strftime("%Y-%m-%d")

# Campos que pedimos a la API (nivel anuncio, desglose diario).
INSIGHT_FIELDS = [
    "ad_name",
    "spend",
    "impressions",
    "reach",
    "frequency",
    "inline_link_clicks",
    "actions",
    "action_values",
    "video_play_actions",
    "video_thruplay_watched_actions",
]

# action_type de Meta -> columna interna. Se prueban en orden; el primero que
# aparezca gana (omni_* agrega web+app; el fb_pixel_* es solo web).
ACTION_MAP = {
    "lpv":  ["landing_page_view", "omni_landing_page_view"],
    "atc":  ["omni_add_to_cart", "add_to_cart", "offsite_conversion.fb_pixel_add_to_cart"],
    "ic":   ["omni_initiated_checkout", "initiate_checkout",
             "offsite_conversion.fb_pixel_initiate_checkout"],
    "purch":["omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"],
    "lead": ["lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"],
}

# Encabezados de salida = idénticos al export manual de Ads Manager (español).
def _headers(currency: str) -> List[str]:
    return [
        "Nombre del anuncio",
        "Inicio del informe",
        f"Importe gastado ({currency})",
        "Impresiones",
        "Alcance",
        "Frecuencia",
        "Clics en el enlace",
        "Visitas a la página de destino",
        "Artículos agregados al carrito",
        "Pagos iniciados",
        "Compras",
        "Valor de conversión de compras",
        "Reproducciones de video de 3 segundos",
        "ThruPlays",
        "Resultados",
    ]


def _get_url(url: str) -> dict:
    """GET a una URL absoluta con reintentos (backoff)."""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f"HTTP {e.code}: {body[:400]}")
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f"URLError: {e}")
    raise RuntimeError("agotados los reintentos")


def _api_get(path: str, params: dict, token: str) -> dict:
    params = dict(params)
    params["access_token"] = token
    return _get_url(f"{GRAPH}/{path}?" + urllib.parse.urlencode(params))


def _sum_action(items, keys) -> float:
    """Suma el value de la primera action_type que matchee alguno de `keys`."""
    if not items:
        return 0.0
    by_type = {}
    for it in items:
        try:
            by_type[it.get("action_type")] = float(it.get("value") or 0)
        except (TypeError, ValueError):
            pass
    for k in keys:
        if k in by_type:
            return by_type[k]
    return 0.0


def _account_currency(acct: str, token: str, fallback: str) -> str:
    try:
        d = _api_get(f"act_{acct}", {"fields": "currency"}, token)
        return d.get("currency") or fallback
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# HISTORIAL DE CAMBIOS por cuenta.
# Fuente 1 (autoritativa): /activities de Meta = el log real de la cuenta
#   (presupuesto, pausas/activaciones, optimización, creación de anuncios).
# Fuente 2 (respaldo): deducido del gasto diario por anuncio que ya traemos
#   (encendido/apagado de creativos, días de cuenta en pausa).
# ---------------------------------------------------------------------------

# event_type que DESCARTAMOS (ruido: facturación, pagos, TOS…).
_CH_DROP = ("billing", "receipt", "funding", "payment", "tos", "invoice",
            "charge", "business_information", "spending_limit_reached",
            "account_spending_limit", "email", "notification")
# substrings que SÍ nos interesan si el categorizador no los ubicó.
_CH_KEEP = ("budget", "bid", "run_status", "status", "optimization", "objective",
            "billing_event", "create_ad", "delete_ad", "update_ad", "update_campaign",
            "update_ad_set", "pause", "activate", "spec", "targeting")


def _change_cat(et: str) -> str:
    e = et.lower()
    if "budget" in e or "bid" in e or "spend_cap" in e:
        return "presupuesto"
    if "optimization" in e or "objective" in e or "billing_event" in e or "conversion" in e:
        return "objetivo"
    if "creative" in e or "create_ad" in e or "delete_ad" in e or "adgroup_spec" in e:
        return "creativo"
    if "status" in e or "pause" in e or "activate" in e or "run" in e:
        return "estado"
    if "targeting" in e or "audience" in e:
        return "segmentacion"
    return "otros"


# Monedas sin decimales (el monto NO viene en centavos).
_ZERO_DEC = {"CLP", "JPY", "KRW", "VND", "ISK", "HUF", "PYG", "UGX", "XAF",
             "XOF", "RWF", "BIF", "DJF", "GNF", "KMF", "XPF"}


def _amt(v, cur):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if cur and cur.upper() not in _ZERO_DEC:
        x = x / 100.0   # Meta guarda en centavos salvo monedas sin decimales
    return x


def _fmt_amt(x):
    if x is None:
        return None
    if x == int(x):
        return f"{int(x):,}".replace(",", ".")
    return f"{x:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _num_in(v):
    if isinstance(v, dict):
        for k in ("amount", "old_value", "new_value", "value", "budget"):
            if isinstance(v.get(k), (int, float, str)):
                return v[k]
        return None
    return v


def _change_detail(j: dict) -> str:
    """Convierte el extra_data de Meta en un detalle legible tipo (10.000 → 12.000 CLP/día)."""
    old, new = j.get("old_value"), j.get("new_value")
    cur = ""
    if isinstance(old, dict):
        cur = old.get("currency") or cur
    if isinstance(new, dict):
        cur = new.get("currency") or cur
    per = ""
    if isinstance(new, dict):
        av = str(new.get("additional_value") or "").strip()
        if av:
            per = "/" + av.replace("por día", "día").replace("por dia", "día")
    oa, na = _amt(_num_in(old), cur), _amt(_num_in(new), cur)
    if oa is not None and na is not None and oa != na:
        return f" ({_fmt_amt(oa)} → {_fmt_amt(na)}{(' ' + cur) if cur else ''}{per})"
    if isinstance(old, (str, int)) and isinstance(new, (str, int)) and str(old) != str(new):
        return f" ({old} → {new})"
    return ""


def fetch_activities(acct: str, token: str, days_hist: int) -> List[dict]:
    """Trae el log de cambios de la cuenta (últimos days_hist días)."""
    until = date.today()
    since = until - timedelta(days=days_hist)
    fields = ("event_type,translated_event_type,extra_data,object_name,"
              "object_type,event_time,actor_name")
    params = {"fields": fields, "since": since.isoformat(),
              "until": until.isoformat(), "limit": 200}
    out: List[dict] = []
    try:
        data = _api_get(f"act_{acct}/activities", params, token)
    except Exception as e:
        print(f"  · {acct}: /activities no disponible ({str(e)[:140]})")
        return out
    page = 0
    while True:
        for a in data.get("data", []):
            et = a.get("event_type") or ""
            el = et.lower()
            if any(d in el for d in _CH_DROP):
                continue
            cat = _change_cat(et)
            if cat == "otros" and not any(k in el for k in _CH_KEEP):
                continue
            when = (a.get("event_time") or "")[:10]
            base = a.get("translated_event_type") or et.replace("_", " ").capitalize()
            obj = a.get("object_name")
            detail = ""
            ed = a.get("extra_data")
            if ed:
                try:
                    j = json.loads(ed) if isinstance(ed, str) else ed
                    if isinstance(j, dict):
                        detail = _change_detail(j)
                except Exception:
                    pass
            label = base + (f" · {obj}" if obj else "") + detail
            out.append({"date": when, "cat": cat, "text": label, "src": "meta"})
        nxt = (data.get("paging") or {}).get("next")
        page += 1
        if not nxt or page > 25:
            break
        try:
            data = _get_url(nxt)
        except Exception:
            break
    return out


def derive_changes(rows: List[List]) -> List[dict]:
    """Deduce encendido/apagado de creativos y pausas de cuenta desde el gasto
    diario por anuncio (rows = [ad_name, date, spend, ...])."""
    from collections import defaultdict
    by_ad = defaultdict(dict)
    acct_day = defaultdict(float)
    alldates = set()
    for r in rows:
        ad = (r[0] or "sin nombre"); d = r[1]
        if not d:
            continue
        try:
            sp = float(r[2] or 0)
        except (TypeError, ValueError):
            sp = 0.0
        alldates.add(d)
        acct_day[d] += sp
        by_ad[ad][d] = by_ad[ad].get(d, 0.0) + sp
    dates = sorted(alldates)
    ev: List[dict] = []
    if not dates:
        return ev
    active = [d for d in dates if acct_day[d] > 0]
    if not active:
        return ev
    # días de cuenta sin gasto dentro del período activo (pausa general)
    span = [d for d in dates if active[0] <= d <= active[-1]]
    run: List[str] = []
    for d in span:
        if acct_day[d] <= 0:
            run.append(d)
        else:
            if len(run) >= 2:
                ev.append({"date": run[0], "cat": "estado",
                           "text": f"Cuenta sin gasto {len(run)} días (posible pausa general)",
                           "src": "auto"})
            run = []
    # encendido/apagado por anuncio (solo con gasto real)
    for ad, dd in by_ad.items():
        adates = sorted([d for d in dd if dd[d] > 0])
        if not adates:
            continue
        aset = set(adates)
        if adates[0] > dates[0]:
            ev.append({"date": adates[0], "cat": "creativo",
                       "text": f"Se encendió/lanzó «{ad}»", "src": "auto"})
        gap: List[str] = []
        for d in [x for x in dates if adates[0] <= x <= adates[-1]]:
            if d not in aset:
                gap.append(d)
            else:
                if len(gap) >= 2:
                    ev.append({"date": gap[0], "cat": "creativo",
                               "text": f"Se pausó «{ad}»", "src": "auto"})
                    ev.append({"date": d, "cat": "creativo",
                               "text": f"Se reactivó «{ad}»", "src": "auto"})
                gap = []
        if adates[-1] < dates[-1]:
            after = [d for d in dates if d > adates[-1] and acct_day[d] > 0]
            if len(after) >= 2:
                ev.append({"date": adates[-1], "cat": "creativo",
                           "text": f"Se apagó «{ad}»", "src": "auto"})
    return ev


def _dedupe_changes(evs: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for e in sorted(evs, key=lambda x: (x.get("date") or ""), reverse=True):
        k = (e.get("date"), e.get("text"))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def write_changes(slug: str, acct: str, token: str, rows: List[List],
                  folder: Path, days_hist: int) -> int:
    """Escribe exports/<slug>/<slug>_changes.json con el historial de cambios."""
    evs = []
    try:
        evs += fetch_activities(acct, token, days_hist)
    except Exception as e:
        print(f"  · {slug}: activities error ({str(e)[:120]})")
    try:
        evs += derive_changes(rows)
    except Exception as e:
        print(f"  · {slug}: derive_changes error ({str(e)[:120]})")
    evs = _dedupe_changes(evs)[:120]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}_changes.json").write_text(
        json.dumps(evs, ensure_ascii=False, indent=1), encoding="utf-8")
    n_meta = sum(1 for e in evs if e.get("src") == "meta")
    print(f"  · {slug}: {len(evs)} cambios en historial ({n_meta} de Meta, {len(evs)-n_meta} deducidos)")
    return len(evs)


def fetch_thumbs(acct: str, token: str, want_names: set) -> Dict[str, str]:
    """Miniatura (thumbnail_url) por nombre de anuncio. Sólo para los anuncios que
    aparecen en la data (activos). Se usa la URL directa de Meta: como el hub se
    regenera cada mañana, la URL se renueva sola y nunca queda vencida."""
    fields = "name,creative{thumbnail_url}"
    # Sólo anuncios ACTIVOS (los que se muestran) -> el query es mucho más liviano
    # y rápido que traer TODO el historial de anuncios de la cuenta.
    filt = json.dumps([{"field": "ad.effective_status", "operator": "IN",
                        "value": ["ACTIVE"]}])
    url = (f"{GRAPH}/act_{acct}/ads?fields={urllib.parse.quote(fields)}"
           f"&filtering={urllib.parse.quote(filt)}"
           f"&limit=100&access_token={urllib.parse.quote(token)}")
    thumbs: Dict[str, str] = {}
    page = 0
    try:
        while url and page < 3:
            data = _get_url(url)
            for ad in data.get("data", []):
                nm = ad.get("name")
                if not nm or nm in thumbs:
                    continue
                if want_names and nm not in want_names:
                    continue
                turl = (ad.get("creative") or {}).get("thumbnail_url")
                if turl:
                    thumbs[nm] = turl
            url = (data.get("paging") or {}).get("next")
            page += 1
    except Exception as e:
        print(f"  · thumbs error ({str(e)[:100]})")
    return thumbs


def fetch_client(client: dict, days: int, token: str, exports_dir: Path) -> Optional[str]:
    acct = str(client.get("ad_account_id") or "").strip().replace("act_", "")
    slug = client["slug"]
    if not acct or acct.upper() in ("", "TODO", "NONE"):
        print(f"· {slug}: sin ad_account_id -> se saltea (usa el CSV que ya esté)")
        return None

    currency = client.get("currency") or _account_currency(acct, token, "ARS")
    until = date.today()
    since = until - timedelta(days=days)
    params = {
        "level": "ad",
        "time_increment": 1,
        "limit": 500,
        "fields": ",".join(INSIGHT_FIELDS),
        "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
        # Igual atribución que el export manual: 7d clic / 1d visualización.
        "action_attribution_windows": json.dumps(["7d_click", "1d_view"]),
    }

    rows: List[List] = []
    is_leads = client.get("objective") == "leads"
    # Primera página vía params; después seguimos el `next` absoluto.
    data = _api_get(f"act_{acct}/insights", params, token)
    page = 0
    while True:
        for r in data.get("data", []):
            actions = r.get("actions")
            avals = r.get("action_values")
            leads = _sum_action(actions, ACTION_MAP["lead"])
            results = _fmt(leads) if is_leads else ""
            rows.append([
                r.get("ad_name", ""),
                r.get("date_start", ""),
                r.get("spend", ""),
                r.get("impressions", ""),
                r.get("reach", ""),
                r.get("frequency", ""),
                r.get("inline_link_clicks", ""),
                _fmt(_sum_action(actions, ACTION_MAP["lpv"])),
                _fmt(_sum_action(actions, ACTION_MAP["atc"])),
                _fmt(_sum_action(actions, ACTION_MAP["ic"])),
                _fmt(_sum_action(actions, ACTION_MAP["purch"])),
                _fmt(_sum_action(avals, ACTION_MAP["purch"])),
                _fmt(_first_value(r.get("video_play_actions"))),
                _fmt(_first_value(r.get("video_thruplay_watched_actions"))),
                results,
            ])
        nxt = (data.get("paging") or {}).get("next")
        page += 1
        if not nxt or page > 50:
            break
        data = _get_url(nxt)

    if not rows:
        print(f"· {slug}: la API no devolvió filas (¿cuenta pausada?) -> no se toca el CSV")
        # El pull SÍ funcionó (la cuenta simplemente no tuvo actividad): se marca
        # como actualizada hoy para no confundir "pausada" con "no se actualizó".
        return {"rows": 0, "through": None, "empty": True}

    folder = exports_dir / slug
    folder.mkdir(parents=True, exist_ok=True)
    # Un solo archivo por marca (reemplaza al anterior; sin días duplicados).
    out = folder / f"{slug}_auto.csv"
    # Limpiar CSV viejos de esa marca para no concatenar ventanas solapadas.
    for old in folder.glob("*.csv"):
        if old.name != out.name:
            old.unlink()
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_headers(currency))
        w.writerows(rows)
    # Último día con datos (col 1 = "Inicio del informe").
    through = max((r[1] for r in rows if r[1]), default=None)
    print(f"✓ {slug}: {len(rows)} filas -> {out}")
    # Historial de cambios (log de Meta + deducción del gasto). Ventana amplia
    # para cubrir desde el arranque de la pauta; no rompe si /activities falla.
    try:
        write_changes(slug, acct, token, rows, folder, days_hist=120)
    except Exception as e:
        print(f"  · {slug}: historial no generado ({str(e)[:120]})")
    # Miniaturas de los creativos activos (para el cuadro de señales).
    try:
        names = set(r[0] for r in rows if r[0])
        th = fetch_thumbs(acct, token, names)
        (folder / f"{slug}_thumbs.json").write_text(
            json.dumps(th, ensure_ascii=False), encoding="utf-8")
        print(f"  · {slug}: {len(th)} miniaturas de creativos")
    except Exception as e:
        print(f"  · {slug}: miniaturas no generadas ({str(e)[:120]})")
    return {"rows": len(rows), "through": through, "empty": False}


def _first_value(items) -> float:
    if not items:
        return 0.0
    try:
        return float(items[0].get("value") or 0)
    except (TypeError, ValueError, IndexError, AttributeError):
        return 0.0


def _fmt(n) -> str:
    if n is None or n == "":
        return ""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return str(n)
    return str(int(f)) if f == int(f) else f"{f:.2f}"


def discover_accounts(token: str) -> List[dict]:
    """Lista TODAS las cuentas publicitarias que la usuaria administra, con su
    gasto de los últimos 90 días (para distinguir clientes activos de cuentas
    dormidas/personales). Devuelve [{id, name, currency, spend}]."""
    out: List[dict] = []
    fields = "name,account_id,currency,insights.date_preset(last_90d){spend}"
    url = (f"{GRAPH}/me/adaccounts?fields={urllib.parse.quote(fields)}"
           f"&limit=200&access_token={urllib.parse.quote(token)}")
    page = 0
    while url and page < 20:
        data = _get_url(url)
        for a in data.get("data", []):
            spend = 0.0
            ins = (a.get("insights") or {}).get("data") or []
            if ins:
                try:
                    spend = float(ins[0].get("spend") or 0)
                except (TypeError, ValueError):
                    spend = 0.0
            out.append({"id": str(a.get("account_id")),
                        "name": a.get("name") or str(a.get("account_id")),
                        "currency": a.get("currency"), "spend": spend})
        url = (data.get("paging") or {}).get("next")
        page += 1
    return out


def _slugify(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:24] or "cuenta"


def _unique_slug(base: str, taken: set) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _append_clients_yaml(path: Path, entries: List[dict]) -> None:
    """Agrega bloques de cliente al final de clients.yaml (preserva lo existente)."""
    chunks = []
    for e in entries:
        nm = str(e["name"]).replace('"', "'")
        chunks.append(
            f"\n  - slug: {e['slug']}\n    name: \"{nm}\"\n    objective: ventas\n"
            f"    obj_code: purchase\n    currency: {e['currency'] or 'ARS'}\n"
            f"    ad_account_id: \"{e['id']}\"\n    start_label: \"auto\"\n"
            f"    margin: 0.5\n    notes: \"Auto-agregado desde Meta ({date.today().isoformat()}).\"\n")
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(chunks))


def refresh_token(token: str) -> str:
    """Renueva el token: lo intercambia por uno nuevo de larga duración (~60 días).

    Necesita META_APP_ID + META_APP_SECRET en el entorno. Un token de larga
    duración re-intercambiado a diario NUNCA vence (siempre vuelve con ~60 días).
    Si además hay un PAT de GitHub (GH_PAT) + GITHUB_REPOSITORY, reescribe el
    secret META_TOKEN con el token fresco -> perpetuo, sin tocar nada nunca más.

    Sin las credenciales de app, no hace nada y sigue con el token tal cual.
    """
    app_id = os.environ.get("META_APP_ID", "").strip()
    app_secret = os.environ.get("META_APP_SECRET", "").strip()
    if not (app_id and app_secret):
        return token
    try:
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": token,
        }
        data = _get_url(f"{GRAPH}/oauth/access_token?" + urllib.parse.urlencode(params))
        new_tok = data.get("access_token")
        if not new_tok:
            print("⚠ refresh: la API no devolvió access_token; sigo con el token actual.")
            return token
        print(f"✓ token renovado (larga duración, ~{int(data.get('expires_in',0))//86400} días).")
        _persist_token_secret(new_tok)
        return new_tok
    except Exception as e:
        print(f"⚠ refresh falló ({e}); sigo con el token actual.")
        return token


def _persist_token_secret(new_tok: str) -> None:
    """Reescribe el secret META_TOKEN en GitHub con `gh` (si hay GH_PAT). Opcional."""
    pat = os.environ.get("GH_PAT", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not (pat and repo):
        return
    import subprocess
    try:
        subprocess.run(
            ["gh", "secret", "set", "META_TOKEN", "--repo", repo, "--body", new_tok],
            env={**os.environ, "GH_TOKEN": pat}, check=True,
            capture_output=True, text=True)
        print("✓ secret META_TOKEN actualizado en GitHub (perpetuo).")
    except Exception as e:
        print(f"⚠ no se pudo reescribir el secret META_TOKEN ({e}); "
              f"el token igual sirve por ~60 días.")


def main():
    ap = argparse.ArgumentParser(description="MAIA · fetch Meta -> exports/<slug>/<slug>_auto.csv")
    ap.add_argument("--days", type=int, default=35, help="ventana de días (default 35)")
    ap.add_argument("--exports", default="exports")
    ap.add_argument("--config", default="config")
    args = ap.parse_args()

    token = os.environ.get("META_TOKEN", "").strip()
    if not token:
        print("META_TOKEN no está seteado -> no se hace fetch (el build usa los CSV existentes).")
        return 0

    # Renovar el token (si hay credenciales de app) para que no venza.
    token = refresh_token(token)

    config_path = Path(args.config) / "clients.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    clients = cfg.get("clients", [])
    ignore_ids = {str(x).replace("act_", "").strip() for x in (cfg.get("ignore_account_ids") or [])}
    configured_ids = {str(c.get("ad_account_id", "")).replace("act_", "").strip() for c in clients}
    taken_slugs = {c.get("slug") for c in clients}

    # Descubrir TODAS las cuentas del portfolio y SUMAR solas las nuevas activas
    # (con gasto, no ya configuradas, no en la lista de ignoradas).
    try:
        accts = discover_accounts(token)
        print(f"\n== Cuentas en tu Meta ({len(accts)}) · ordenadas por gasto 90d ==")
        new_entries = []
        for a in sorted(accts, key=lambda x: -x["spend"]):
            aid = a["id"]
            if aid in configured_ids:
                mark = "✓ en hub"
            elif aid in ignore_ids:
                mark = "· ignorada"
            elif a["spend"] > 0:
                slug = _unique_slug(_slugify(a["name"]), taken_slugs)
                taken_slugs.add(slug)
                new_entries.append({"slug": slug, "name": a["name"],
                                    "currency": a["currency"], "id": aid})
                mark = f"← NUEVA -> se agrega (slug: {slug})"
            else:
                mark = "· sin gasto 90d (se ignora)"
            print(f"  act_{aid:>18}  {a['currency'] or '?':>3}  "
                  f"gasto90d={int(a['spend']):>12,}  {a['name'][:32]:32s}  {mark}")
        if new_entries:
            _append_clients_yaml(config_path, new_entries)
            # Re-leer para que las nuevas se fetcheen en esta misma corrida.
            clients = (yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("clients", [])
            print(f"✓ {len(new_entries)} cuenta(s) nueva(s) agregada(s) a clients.yaml: "
                  + ", ".join(e["slug"] for e in new_entries))
        print("== fin listado ==\n")
    except Exception as e:
        print(f"⚠ no se pudo listar/auto-agregar el portfolio ({e}); sigo con las de config.")

    exports = Path(args.exports)
    today = _today_art()
    status_path = exports / "_status.json"
    status: Dict[str, dict] = {}
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8")) or {}
        except Exception:
            status = {}

    ok, skip = 0, 0
    for c in clients:
        slug = c["slug"]
        try:
            res = fetch_client(c, args.days, token, exports)
            if res is None:            # sin ad_account_id -> no se toca su estado
                skip += 1
                continue
            prev = status.get(slug, {})
            status[slug] = {
                "fetched_at": today,   # se actualizó HOY (aunque la cuenta esté pausada)
                "ok": True,
                "rows": res["rows"],
                "empty": res.get("empty", False),
                "through": res.get("through") or prev.get("through"),
            }
            ok += 1
        except Exception as e:
            # Error real de esa cuenta: se CONSERVA el fetched_at previo, así queda
            # marcada como desactualizada en el hub (no miente con fecha de hoy).
            prev = status.get(slug, {})
            prev.update({"ok": False, "last_error": str(e)[:200], "last_attempt": today})
            status[slug] = prev
            print(f"⚠ {slug}: error de fetch -> se conserva el CSV previo · {e}")
            skip += 1

    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ estado por marca -> {status_path}")
    except Exception as e:
        print(f"⚠ no se pudo escribir _status.json ({e})")

    print(f"\nFetch terminado: {ok} actualizados, {skip} salteados/con error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
