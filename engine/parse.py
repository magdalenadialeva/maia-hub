"""
MAIA · Parser del export de Meta (nivel anuncio).

Lee un export de Ads Manager (.csv o .xlsx) y lo normaliza a filas diarias
por anuncio, con nombres de columna internos estables. El mapeo de columnas
es TOLERANTE: normaliza acentos/mayúsculas y matchea por alias, y se puede
pisar explícitamente desde config/mapping.yaml.

Sin LLM: es puro parseo determinístico.

>>> NOTA DE CALIBRACIÓN <<<
Los alias por defecto cubren los nombres estándar del export en español.
Cuando llegue el CSV real de Vresia, correr:  python -m engine.parse <archivo>
que imprime el diagnóstico de mapeo (qué columna cayó en qué campo y qué
quedó sin mapear) para confirmar o ajustar mapping.yaml en 1 minuto.
"""
from __future__ import annotations
import sys
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd


# ---------------------------------------------------------------------------
# Alias por defecto: campo interno -> lista de substrings (ya normalizados)
# que, si aparecen en el header normalizado, lo asignan a ese campo.
# El PRIMER match gana; el orden dentro de la lista es de más a menos específico.
# ---------------------------------------------------------------------------
DEFAULT_ALIASES: Dict[str, List[str]] = {
    # Identidad
    "ad_name":     ["nombre del anuncio", "anuncio"],
    "adset":       ["nombre del conjunto de anuncios", "conjunto de anuncios"],
    "campaign":    ["nombre de la campana", "campana"],
    # El export a nivel anuncio con desglose diario trae "Inicio del informe"
    # (== "Fin del informe" == el día). No hay columna "Día".
    "date":        ["inicio del informe", "dia", "fecha", "day", "date"],
    "date_end":    ["fin del informe"],
    # Gasto (el header trae la moneda: "Importe gastado (ARS)")
    "spend":       ["importe gastado", "amount spent", "gasto"],
    # Volumen
    "impressions": ["impresiones", "impressions"],
    "reach":       ["alcance", "reach"],
    "frequency":   ["frecuencia", "frequency"],
    "clicks":      ["clics (todos)", "clics todos", "clicks (all)", "all clicks"],
    "link_clicks": ["clics en el enlace", "clics unicos en el enlace", "link clicks"],
    # Video
    "video_3s":    ["reproducciones de video de 3 segundos", "reproducciones de 3 segundos",
                    "3-second video plays", "reproducciones de video de 3"],
    "thruplay":    ["thruplay", "thruplays", "reproducciones de thruplay"],
    "video_25":    ["reproducciones de video hasta el 25", "video plays at 25"],
    "video_50":    ["reproducciones de video hasta el 50", "video plays at 50"],
    "video_75":    ["reproducciones de video hasta el 75", "video plays at 75"],
    "video_100":   ["reproducciones de video hasta el 100", "video plays at 100"],
    # Embudo (acciones/conversiones)
    "lpv":         ["visitas a la pagina de destino", "vistas de la pagina de destino",
                    "landing page views", "visualizaciones de la pagina de destino"],
    "atc":         ["articulos agregados al carrito", "agregar al carrito", "agregado al carrito",
                    "adds to cart", "add to cart"],
    "ic":          ["pagos iniciados", "iniciar pago", "informacion de pago agregada",
                    "checkouts initiated", "initiate checkout"],
    "purchases":   ["compras", "purchases"],
    "revenue":     ["valor de conversion de compras", "valor de conversiones de compras",
                    "purchase conversion value", "valor de conversion"],
    "leads":       ["clientes potenciales", "leads", "prospectos"],
    # "Resultados" = el resultado optimizado de la campaña (leads para lead-gen,
    # compras/pagos para ventas). En build se usa como leads SOLO si el objetivo
    # del cliente es leads; para ventas se ignora (se usa purch/ic directo).
    "results":     ["resultados"],
    "results_ind": ["indicador de resultado"],
    # Métricas ya calculadas por Meta (opcionales; el motor las recalcula)
    "roas_meta":   ["roas", "retorno de la inversion publicitaria", "return on ad spend"],
    "ctr_meta":    ["ctr"],
    "cpc_meta":    ["cpc"],
    "cpm_meta":    ["cpm"],
}

# Campos numéricos (para coerción robusta de formatos "1.234,56" / "1,234.56").
NUMERIC_FIELDS = {
    "spend", "impressions", "reach", "frequency", "clicks", "link_clicks",
    "video_3s", "thruplay", "video_25", "video_50", "video_75", "video_100",
    "lpv", "atc", "ic", "purchases", "revenue", "leads", "results",
    "roas_meta", "ctr_meta", "cpc_meta", "cpm_meta",
}

# Campos que, si faltan, se rellenan con 0 (ausencia = no hubo evento).
FILL_ZERO = NUMERIC_FIELDS - {"roas_meta", "ctr_meta", "cpc_meta", "cpm_meta"}


def _norm(s: str) -> str:
    """minúsculas, sin acentos, colapsa espacios."""
    s = str(s).strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s


def _coerce_number(x) -> float:
    if pd.isna(x):
        return float("nan")
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s == "" or s == "-":
        return float("nan")
    # quitar símbolos de moneda / % / espacios
    s = re.sub(r"[^\d,.\-]", "", s)
    if s in ("", "-", ".", ","):
        return float("nan")
    # Detectar separador decimal: si hay ambos, el último es el decimal.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):      # formato 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:                                 # formato 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        # coma sola: decimal si hay <=2 dígitos después, si no miles
        if re.search(r",\d{1,2}$", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _load_aliases(mapping_yaml: Optional[Path]) -> Dict[str, List[str]]:
    aliases = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    if mapping_yaml and mapping_yaml.exists():
        import yaml
        cfg = yaml.safe_load(mapping_yaml.read_text(encoding="utf-8")) or {}
        for field_, extra in (cfg.get("aliases") or {}).items():
            lst = [ _norm(a) for a in (extra or []) ]
            aliases.setdefault(field_, [])
            # los alias del usuario tienen prioridad (van primero)
            aliases[field_] = lst + aliases[field_]
    return aliases


def _detect_currency(columns: List[str]) -> Optional[str]:
    for c in columns:
        m = re.search(r"importe gastado \(([a-z]{3})\)", _norm(c))
        if m:
            return m.group(1).upper()
    return None


def map_columns(columns: List[str], aliases: Dict[str, List[str]]) -> Tuple[Dict[str, str], List[str]]:
    """Devuelve (mapa campo_interno->columna_original, columnas_sin_usar)."""
    norm_cols = {c: _norm(c) for c in columns}
    field_to_col: Dict[str, str] = {}
    used = set()
    for field_, subs in aliases.items():
        for sub in subs:
            # match por el alias más específico primero
            candidates = [c for c, nc in norm_cols.items()
                          if c not in used and sub in nc]
            if candidates:
                # preferir el header más corto (menos ruido / exacto)
                best = min(candidates, key=lambda c: len(norm_cols[c]))
                field_to_col[field_] = best
                used.add(best)
                break
    unmapped = [c for c in columns if c not in used]
    return field_to_col, unmapped


def read_export(path: str | Path, mapping_yaml: Optional[str | Path] = None) -> Tuple[pd.DataFrame, dict]:
    """
    Lee el export y devuelve (df_normalizado, meta).
    df_normalizado tiene columnas internas estables (ver DEFAULT_ALIASES).
    meta trae: {currency, mapping, unmapped, n_rows, has_daily, source}.
    """
    path = Path(path)
    aliases = _load_aliases(Path(mapping_yaml) if mapping_yaml else None)

    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path, dtype=str)
    else:
        # sep=None + engine=python autodetecta ; o ,
        raw = pd.read_csv(path, dtype=str, sep=None, engine="python")

    raw.columns = [str(c).strip() for c in raw.columns]
    currency = _detect_currency(list(raw.columns))
    field_to_col, unmapped = map_columns(list(raw.columns), aliases)

    # Construir df normalizado
    out = pd.DataFrame()
    for field_, col in field_to_col.items():
        out[field_] = raw[col]

    for f in NUMERIC_FIELDS:
        if f in out.columns:
            out[f] = out[f].map(_coerce_number)

    for f in FILL_ZERO:
        if f in out.columns:
            out[f] = out[f].fillna(0.0)

    # Fecha
    has_daily = "date" in out.columns
    if has_daily:
        # Meta exporta ISO (YYYY-MM-DD). Fallback a dayfirst para DD/MM/YYYY.
        d = pd.to_datetime(out["date"], errors="coerce", format="ISO8601")
        if d.isna().all():
            d = pd.to_datetime(out["date"], errors="coerce", dayfirst=True)
        out["date"] = d.dt.date.astype(str)

    # Identidad mínima
    if "ad_name" not in out.columns:
        out["ad_name"] = raw.index.map(lambda i: f"anuncio_{i}")

    meta = {
        "currency": currency,
        "mapping": field_to_col,
        "unmapped": unmapped,
        "n_rows": int(len(out)),
        "has_daily": has_daily,
        "source": path.name,
    }
    return out, meta


def diagnose(path: str | Path, mapping_yaml: Optional[str | Path] = None) -> str:
    """Imprime el diagnóstico de mapeo para calibrar contra un export real."""
    df, meta = read_export(path, mapping_yaml)
    lines = [f"== Diagnóstico de mapeo: {meta['source']} ==",
             f"Moneda detectada: {meta['currency']}",
             f"Filas: {meta['n_rows']} · Desglose diario: {'sí' if meta['has_daily'] else 'no'}",
             "", "Columnas mapeadas:"]
    for f, col in meta["mapping"].items():
        lines.append(f"  {f:14s} <- {col}")
    lines.append("")
    lines.append("Columnas SIN mapear (revisar si alguna es importante):")
    for c in meta["unmapped"]:
        lines.append(f"  · {c}")
    # chequeo de campos críticos
    crit = ["spend", "impressions", "purchases", "revenue", "lpv", "atc", "ic",
            "video_3s", "thruplay", "link_clicks"]
    missing = [c for c in crit if c not in meta["mapping"]]
    lines.append("")
    if missing:
        lines.append("⚠ Faltan campos críticos: " + ", ".join(missing))
        lines.append("  -> agregá el alias correcto en config/mapping.yaml")
    else:
        lines.append("✓ Todos los campos críticos mapeados.")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("uso: python -m engine.parse <export.csv|.xlsx> [mapping.yaml]")
        sys.exit(1)
    mp = sys.argv[2] if len(sys.argv) > 2 else "config/mapping.yaml"
    print(diagnose(sys.argv[1], mp))
