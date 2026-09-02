"""
MAIA · Orquestador del build (sin LLM).

Lee todos los exports en exports/<slug>/*.csv|xlsx, los cruza con
config/clients.yaml y escribe:
  - site/data.js    -> window.DATA_EXT = {...}   (lo que consume el hub)
  - site/data.json  -> el mismo objeto en JSON    (para otros consumidores)

El hub ya calcula ROAS/embudo/señales en el browser desde las filas crudas,
así que el motor solo tiene que producir el DATA correcto. La lógica de
análisis (reporte PDF + volcado al Brand Brain) vive en engine/report.py y
engine/brandbrain.py y también parte de estos mismos exports.

Uso:
    python -m engine.build
    python -m engine.build --exports exports --site site --config config
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple
import yaml

from .parse import read_export
from .hubdata import build_hub_client


def load_clients(config_dir: Path) -> List[dict]:
    cfg = yaml.safe_load((config_dir / "clients.yaml").read_text(encoding="utf-8"))
    return cfg.get("clients", [])


def load_client_rows(exports_dir: Path, slug: str, mapping_yaml: Path) -> Tuple[list, Optional[str], List[str]]:
    folder = exports_dir / slug
    files = []
    if folder.exists():
        files = sorted(p for p in folder.iterdir()
                       if p.suffix.lower() in (".csv", ".xlsx", ".xls"))
    rows: list = []
    currency = None
    for f in files:
        df, meta = read_export(f, mapping_yaml)
        currency = currency or meta["currency"]
        rows.extend(df.to_dict(orient="records"))
    return rows, currency, [f.name for f in files]


def _client_totals(hub: dict) -> dict:
    """Suma rápida para validación/diagnóstico."""
    f = hub["f"]
    idx = {name: 2 + i for i, name in enumerate(f)}
    tot = {name: 0.0 for name in f}
    for r in hub["rows"]:
        for name in f:
            tot[name] += r[idx[name]]
    roas = (tot["pval"] / tot["spend"]) if tot["spend"] else 0
    return {"spend": round(tot["spend"]), "purch": int(tot["purch"]),
            "revenue": round(tot["pval"]), "roas": round(roas, 2),
            "leads": int(tot["leads"]), "ic": int(tot["ic"]), "atc": int(tot["atc"])}


def _load_status(exports_dir: Path) -> dict:
    p = exports_dir / "_status.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}


def build_data(exports_dir: Path, site_dir: Path, config_dir: Path, verbose=True) -> dict:
    mapping_yaml = config_dir / "mapping.yaml"
    clients = load_clients(config_dir)
    status = _load_status(exports_dir)
    DATA = {}
    diagnostics = []
    for client in clients:
        slug = client["slug"]
        rows, currency, files = load_client_rows(exports_dir, slug, mapping_yaml)
        if not rows:
            diagnostics.append(f"· {slug}: sin export (se omite)")
            continue
        hub = build_hub_client(client, rows, currency)
        # Última actualización de ESTA marca: fecha real del último pull desde Meta
        # (la escribe engine.fetch_meta). Si aún no hay estado, cae al último día
        # con datos, así el hub siempre muestra algo razonable.
        st = status.get(slug) or {}
        hub["upd"] = st.get("fetched_at") or (hub["dates"][-1] if hub["dates"] else None)
        hub["through"] = st.get("through") or (hub["dates"][-1] if hub["dates"] else None)
        # Salud del último intento de actualización. Si el pull de HOY falló pero se
        # conserva el CSV previo, fetched_at queda con la fecha vieja buena y el hub
        # debe avisar con ⚠ que la ÚLTIMA corrida no trajo datos (aunque la fecha se
        # vea reciente). Sin esto, una marca con error de Meta se mostraba "al día".
        hub["ok"] = bool(st.get("ok", True))       # ¿anduvo el último intento?
        hub["empty"] = bool(st.get("empty", False))
        if not hub["ok"]:
            hub["err"] = st.get("last_error")       # motivo (texto de Meta)
            hub["attempt"] = st.get("last_attempt")  # cuándo se intentó y falló
        # Historial de cambios (lo escribe engine.fetch_meta).
        ch_path = exports_dir / slug / f"{slug}_changes.json"
        changes = []
        if ch_path.exists():
            try:
                changes = json.loads(ch_path.read_text(encoding="utf-8")) or []
            except Exception:
                changes = []
        hub["changes"] = changes
        # Miniaturas de creativos (nombre -> thumbnail_url), lo escribe fetch_meta.
        th_path = exports_dir / slug / f"{slug}_thumbs.json"
        thumbs = {}
        if th_path.exists():
            try:
                thumbs = json.loads(th_path.read_text(encoding="utf-8")) or {}
            except Exception:
                thumbs = {}
        hub["thumbs"] = thumbs
        DATA[slug] = hub
        t = _client_totals(hub)
        diagnostics.append(
            f"· {client['slug']:7s} {hub['cur']} gasto={t['spend']:>12,} "
            f"compras={t['purch']:>3} ROAS={t['roas']:>4} leads={t['leads']:>4} "
            f"| {len(hub['ads'])} anuncios, {len(hub['dates'])} días, {len(hub['rows'])} filas")

    site_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(DATA, ensure_ascii=False, separators=(",", ":"))
    try:
        from zoneinfo import ZoneInfo
        built = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).strftime("%Y-%m-%d")
    except Exception:
        built = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (site_dir / "data.js").write_text(
        "/* Generado por engine.build — NO editar a mano. */\n"
        "window.DATA_EXT = " + payload + ";\n"
        "window.DATA_BUILT = " + json.dumps(built) + ";\n", encoding="utf-8")
    (site_dir / "data.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "DATA": DATA}, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        sz = (site_dir / "data.js").stat().st_size / 1024
        print(f"✓ site/data.js  ({sz:.0f} KB)  ·  site/data.json")
        print("\n".join(diagnostics))
    return DATA


def main():
    ap = argparse.ArgumentParser(description="MAIA · build data.js/json + reportes + brand brain")
    ap.add_argument("--exports", default="exports")
    ap.add_argument("--site", default="site")
    ap.add_argument("--config", default="config")
    ap.add_argument("--reports", default="reports")
    ap.add_argument("--brandbrain", default="brandbrain")
    ap.add_argument("--only-data", action="store_true",
                    help="solo regenerar site/data.js (lo que necesita el hub)")
    args = ap.parse_args()
    exports, config = Path(args.exports), Path(args.config)
    build_data(exports, Path(args.site), config)
    if not args.only_data:
        from .report import build_reports
        from .brandbrain import build_brandbrain
        build_reports(exports, Path(args.reports), config)
        build_brandbrain(exports, Path(args.brandbrain), config)


if __name__ == "__main__":
    main()
