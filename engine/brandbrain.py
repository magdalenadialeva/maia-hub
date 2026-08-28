"""
MAIA · Volcado al Brand Brain (filas para la Base Histórica). Sin LLM.

Genera CSVs listos para pegar/importar en el Sheet del Brand Brain, según el
esquema del Manual (BASE: 1 diario · 2 por creativo · 3 semanal+veredicto) y
un resumen cross-cliente para el Tablero de Agencia.

Ojo: el motor produce los NÚMEROS. El "por qué" (racional + hipótesis del
log de cambios, T9/T11) lo agrega quien analiza; no se inventa acá.

Salida (brandbrain/):
  <slug>_diario.csv     · una fila por día
  <slug>_creativos.csv  · una fila por anuncio + veredicto
  <slug>_semanal.csv    · una fila resumen del período + cuello + veredicto
  tablero_agencia.csv   · una fila por cliente (para el Tablero 00)
"""
from __future__ import annotations
import csv
from pathlib import Path
from typing import List
import yaml

from .parse import read_export
from .config import target_roas_from_margin, DEFAULT_THRESHOLDS
from .metrics import _sum_rows, compute_metrics, build_funnel, STEP_LABELS
from .signals import creative_signal, rank_creatives
from .report import _prep_rows


def _rows_for_client(exports_dir: Path, slug: str, mapping_yaml: Path) -> List[dict]:
    folder = exports_dir / slug
    files = sorted(folder.glob("*.csv")) + sorted(folder.glob("*.xlsx")) if folder.exists() else []
    rows = []
    for f in files:
        df, _ = read_export(f, mapping_yaml)
        rows.extend(df.to_dict(orient="records"))
    return rows


def _write_csv(path: Path, header: List[str], rows: List[list]):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def build_brandbrain(exports_dir: Path, out_dir: Path, config_dir: Path, verbose=True):
    mapping_yaml = config_dir / "mapping.yaml"
    clients = yaml.safe_load((config_dir / "clients.yaml").read_text(encoding="utf-8"))["clients"]
    out_dir.mkdir(parents=True, exist_ok=True)
    tablero = []

    for client in clients:
        slug = client["slug"]
        objective = client.get("objective", "ventas")
        cur = client.get("currency") or "ARS"
        th = DEFAULT_THRESHOLDS.merged(client.get("overrides"))
        target_roas = target_roas_from_margin(client.get("margin"))
        rows = _prep_rows(_rows_for_client(exports_dir, slug, mapping_yaml), objective)
        if not rows:
            continue
        dates = sorted({r["date"] for r in rows if r.get("date")})
        period = f"{dates[0]}..{dates[-1]}" if dates else ""

        # --- 1 · diario ---
        by_day = {}
        for r in rows:
            d = r.get("date")
            if not d:
                continue
            by_day.setdefault(d, []).append(r)
        diario = []
        for d in sorted(by_day):
            t = _sum_rows(by_day[d])
            mm = compute_metrics(t, is_video=True)
            diario.append([d, mm["spend"], mm["impressions"], int(t["link_clicks"]),
                           int(t["lpv"]), int(t["atc"]), int(t["ic"]),
                           mm["purchases"], mm["revenue"], mm["roas"] or "",
                           mm["leads"], mm["cpm"] or "", mm["ctr"] or ""])
        _write_csv(out_dir / f"{slug}_diario.csv",
                   ["fecha", "gasto", "impresiones", "clics_enlace", "lpv", "atc",
                    "pagos_iniciados", "compras", "valor_compras", "roas",
                    "leads", "cpm", "ctr_%"], diario)

        # --- 2 · por creativo + veredicto ---
        ads = {}
        for r in rows:
            ads.setdefault(r.get("ad_name", "—"), []).append(r)
        sigs = [creative_signal({"ad_name": a}, ar, objective, target_roas, th)
                for a, ar in ads.items()]
        sigs = rank_creatives(sigs, objective)
        creativos = []
        for s in sigs:
            mm = s["metrics"]
            creativos.append([s["ad_name"], s["format"], mm["spend"], mm["impressions"],
                              mm["ctr"], mm["cpc"], mm["hook_rate"] or "", mm["hold_rate"] or "",
                              mm["roas"] or "", mm["cpa"] or "", mm["purchases"],
                              mm["cpl"] or "", mm["leads"],
                              s["verdict_meta"]["label"],
                              STEP_LABELS.get(s["breaks_at"], "") if s["breaks_at"] else "",
                              " · ".join(s["motives"][:2])])
        _write_csv(out_dir / f"{slug}_creativos.csv",
                   ["anuncio", "formato", "gasto", "impresiones", "ctr_%", "cpc",
                    "hook_%", "hold_%", "roas", "cpa", "compras", "cpl", "leads",
                    "veredicto", "se_rompe_en", "motivo"], creativos)

        # --- 3 · semanal + veredicto ---
        t = _sum_rows(rows)
        mm = compute_metrics(t, is_video=True)
        funnel = build_funnel(t, objective)
        counts = {}
        for s in sigs:
            counts[s["verdict"]] = counts.get(s["verdict"], 0) + 1
        resumen_verd = f"escalar:{counts.get('escalar',0)} iterar:{counts.get('iterar',0)} matar:{counts.get('matar',0)} pocaData:{counts.get('poca_data',0)}"
        result = mm["purchases"] if objective == "ventas" else mm["leads"]
        semanal = [[slug, client.get("name", slug), period, cur, mm["spend"],
                    result, mm["roas"] or "", mm["cpa"] or "", mm["cpl"] or "",
                    STEP_LABELS.get(funnel["bottleneck_step"], "") if funnel["bottleneck_step"] else "",
                    funnel["diagnosis"] or "", resumen_verd]]
        _write_csv(out_dir / f"{slug}_semanal.csv",
                   ["slug", "cliente", "periodo", "moneda", "gasto", "resultado",
                    "roas", "cpa", "cpl", "cuello_embudo", "diagnostico", "veredictos"], semanal)

        tablero.append(semanal[0])

    _write_csv(out_dir / "tablero_agencia.csv",
               ["slug", "cliente", "periodo", "moneda", "gasto", "resultado",
                "roas", "cpa", "cpl", "cuello_embudo", "diagnostico", "veredictos"], tablero)
    if verbose:
        print(f"✓ brandbrain/  ->  {len(tablero)} clientes + tablero_agencia.csv")
    return tablero


if __name__ == "__main__":
    build_brandbrain(Path("exports"), Path("brandbrain"), Path("config"))
