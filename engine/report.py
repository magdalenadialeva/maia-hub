"""
MAIA · Reporte semanal al cliente (HTML con botón Descargar PDF). Sin LLM.

Un archivo HTML autocontenido por cliente en reports/<slug>.html:
  - KPIs del período (gasto, ROAS/CPA o CPL, compras/leads, CPM, CTR)
  - Embudo con el paso "cuello de botella" y su diagnóstico
  - Top creativos con veredicto (escalar/iterar/matar/poca data)
  - Bloque editable "Conclusiones / Próximos pasos" (contenteditable)
  - Botón "Descargar PDF" (window.print → guardar como PDF)

La marca de agua de números usa separador de miles local. Los textos de
conclusiones son editables por quien arma el reporte antes de exportar.
"""
from __future__ import annotations
import html as _html
from pathlib import Path
from typing import List
import yaml

from .parse import read_export
from .config import target_roas_from_margin, DEFAULT_THRESHOLDS
from .metrics import _sum_rows, compute_metrics, build_funnel, STEP_LABELS
from .signals import creative_signal, rank_creatives


def _prep_rows(rows: List[dict], objective: str) -> List[dict]:
    """Para lead-gen, leads = results (columna Resultados de Meta)."""
    if objective != "leads":
        return rows
    out = []
    for r in rows:
        r = dict(r)
        r["leads"] = r.get("results") or 0
        out.append(r)
    return out


def _fmt(n, cur=None, dec=0):
    if n is None:
        return "—"
    s = f"{n:,.{dec}f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"{cur} {s}" if cur else s


def _client_report_html(client: dict, rows: List[dict]) -> str:
    slug = client["slug"]
    name = client.get("name", slug)
    cur = client.get("currency") or "ARS"
    objective = client.get("objective", "ventas")
    th = DEFAULT_THRESHOLDS.merged(client.get("overrides"))
    target_roas = target_roas_from_margin(client.get("margin"))
    rows = _prep_rows(rows, objective)

    dates = sorted({r["date"] for r in rows if r.get("date")})
    period = f"{dates[0]} → {dates[-1]}" if dates else "—"
    totals = _sum_rows(rows)
    m = compute_metrics(totals, is_video=True)
    funnel = build_funnel(totals, objective)

    # señales por creativo
    ads = {}
    for r in rows:
        ads.setdefault(r.get("ad_name", "—"), []).append(r)
    sigs = [creative_signal({"ad_name": a}, ar, objective, target_roas, th)
            for a, ar in ads.items()]
    sigs = rank_creatives(sigs, objective)

    # KPI tiles
    if objective == "ventas":
        kpis = [("Gasto", _fmt(m["spend"], cur)), ("ROAS", m["roas"] or "—"),
                ("Compras", m["purchases"]), ("CPA", _fmt(m["cpa"], cur)),
                ("CPM", _fmt(m["cpm"], cur)), ("CTR", f'{m["ctr"]}%' if m["ctr"] else "—")]
    else:
        kpis = [("Gasto", _fmt(m["spend"], cur)), ("Leads", m["leads"]),
                ("CPL", _fmt(m["cpl"], cur)), ("CPM", _fmt(m["cpm"], cur)),
                ("CTR", f'{m["ctr"]}%' if m["ctr"] else "—"), ("Frecuencia", m["frequency"] or "—")]

    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-v">{v}</div><div class="kpi-l">{k}</div></div>'
        for k, v in kpis)

    # Funnel
    frows = []
    for n in funnel["steps"]:
        conv = n.get("conv_to_next_pct")
        bench = n.get("healthy_pct")
        cls = "bad" if (conv is not None and bench and conv < bench * 0.7) else ""
        bn = " · CUELLO" if n["step"] == funnel["bottleneck_step"] else ""
        conv_txt = (f'{conv}% al siguiente' + (f' (sano ≈{bench}%)' if bench else '')) if conv is not None else ''
        frows.append(
            f'<tr class="{cls}"><td>{_html.escape(n["label"])}{bn}</td>'
            f'<td class="num">{_fmt(n["value"])}</td><td class="conv">{conv_txt}</td></tr>')
    funnel_diag = _html.escape(funnel["diagnosis"] or "")

    # Creativos
    crows = []
    for s in sigs[:12]:
        vm = s["verdict_meta"]
        mm = s["metrics"]
        perf = (f'ROAS {mm["roas"] or "—"} · CPA {_fmt(mm["cpa"], cur)}'
                if objective == "ventas" else
                f'CPL {_fmt(mm["cpl"], cur)} · {mm["leads"]} leads')
        vid = (f'Hook {mm["hook_rate"]}% · Hold {mm["hold_rate"]}% · '
               if s["format"] == "video" and mm["hook_rate"] is not None else "")
        motives = _html.escape(" · ".join(s["motives"][:2]))
        crows.append(
            f'<tr><td>{_html.escape(str(s["ad_name"]))}</td>'
            f'<td><span class="pill" style="background:{vm["color"]}">{vm["emoji"]} {vm["label"]}</span></td>'
            f'<td>{vid}CTR {mm["ctr"]}% · {perf}</td>'
            f'<td class="mot">{motives}</td></tr>')

    return _TEMPLATE.format(
        name=_html.escape(name), slug=slug, cur=cur, period=period,
        obj=("Ventas" if objective == "ventas" else "Clientes potenciales"),
        target=target_roas, kpis=kpi_html,
        funnel_rows="".join(frows), funnel_diag=funnel_diag,
        creative_rows="".join(crows),
        notes_hint=_html.escape(client.get("notes", "")),
    )


_TEMPLATE = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MAIA · Reporte {name}</title>
<style>
:root{{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--bg:#f8fafc;--card:#fff;--accent:#4f46e5}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:32px 24px 64px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;
border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:24px}}
h1{{margin:0;font-size:26px}} .sub{{color:var(--mut);font-size:13px;margin-top:4px}}
.btn{{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 16px;
font-weight:600;cursor:pointer}} .btn:hover{{opacity:.9}}
h2{{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);
margin:28px 0 12px}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}
.kpi-v{{font-size:22px;font-weight:700}} .kpi-l{{color:var(--mut);font-size:12px;margin-top:2px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:12px;overflow:hidden;font-size:14px}}
td,th{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
tr:last-child td{{border-bottom:0}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.conv{{color:var(--mut);font-size:13px}} tr.bad td{{background:#fef2f2}}
.pill{{color:#fff;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}}
.mot{{color:var(--mut);font-size:13px}}
.diag{{background:#eef2ff;border:1px solid #c7d2fe;border-radius:10px;padding:12px 14px;
margin-top:12px;font-size:14px}}
.edit{{background:#fffbeb;border:1px dashed #fcd34d;border-radius:10px;padding:14px;min-height:80px}}
.edit:focus{{outline:2px solid var(--accent)}}
.foot{{color:var(--mut);font-size:12px;margin-top:32px;text-align:center}}
@media print{{.btn{{display:none}} body{{background:#fff}} .edit{{border-color:#e2e8f0;background:#fff}}}}
</style></head><body><div class="wrap">
<header><div><h1>{name}</h1>
<div class="sub">Reporte de performance · {period} · Objetivo: {obj} · Moneda: {cur} · ROAS objetivo ≈ {target}</div></div>
<button class="btn" onclick="window.print()">Descargar PDF</button></header>

<h2>Resultados del período</h2>
<div class="kpis">{kpis}</div>

<h2>Embudo · dónde se rompe</h2>
<table><tbody>{funnel_rows}</tbody></table>
<div class="diag">🔎 {funnel_diag}</div>

<h2>Creativos · veredicto</h2>
<table><thead><tr><th>Anuncio</th><th>Veredicto</th><th>Señales</th><th>Por qué</th></tr></thead>
<tbody>{creative_rows}</tbody></table>

<h2>Conclusiones y próximos pasos</h2>
<div class="edit" contenteditable="true">Escribí acá las conclusiones y los próximos pasos para {name}. (Contexto: {notes_hint})</div>

<div class="foot">MAIA · Generado automáticamente desde el export de Meta · editá las conclusiones antes de exportar.</div>
</div></body></html>"""


def build_reports(exports_dir: Path, out_dir: Path, config_dir: Path, verbose=True):
    mapping_yaml = config_dir / "mapping.yaml"
    clients = yaml.safe_load((config_dir / "clients.yaml").read_text(encoding="utf-8"))["clients"]
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for client in clients:
        folder = exports_dir / client["slug"]
        files = sorted(folder.glob("*.csv")) + sorted(folder.glob("*.xlsx")) if folder.exists() else []
        if not files:
            continue
        rows = []
        for f in files:
            df, _ = read_export(f, mapping_yaml)
            rows.extend(df.to_dict(orient="records"))
        html_str = _client_report_html(client, rows)
        p = out_dir / f"{client['slug']}.html"
        p.write_text(html_str, encoding="utf-8")
        done.append(client["slug"])
    if verbose:
        print(f"✓ reports/  ->  {', '.join(done)}")
    return done


if __name__ == "__main__":
    build_reports(Path("exports"), Path("reports"), Path("config"))
