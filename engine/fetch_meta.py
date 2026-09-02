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
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

API_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{API_VERSION}"

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
    "video_3_sec_watched_actions",
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
                _fmt(_first_value(r.get("video_3_sec_watched_actions"))),
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
        return None

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
    print(f"✓ {slug}: {len(rows)} filas -> {out}")
    return str(out)


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

    clients = yaml.safe_load((Path(args.config) / "clients.yaml").read_text(encoding="utf-8"))["clients"]
    exports = Path(args.exports)
    ok, skip = 0, 0
    for c in clients:
        try:
            res = fetch_client(c, args.days, token, exports)
            ok += 1 if res else 0
            skip += 0 if res else 1
        except Exception as e:
            print(f"⚠ {c['slug']}: error de fetch -> se conserva el CSV previo · {e}")
            skip += 1
    print(f"\nFetch terminado: {ok} actualizados, {skip} salteados/con error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
