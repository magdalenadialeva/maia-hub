"""
MAIA · Desenganche de datos del hub (sin LLM).

Toma el index.html ORIGINAL del hub (el artifact / la versión de Netlify, un
solo archivo con el diseño, las imágenes PROPIMG y toda la lógica de render) y
produce site/index.html que, en lugar de tener el objeto DATA embebido, lo lee
de site/data.js  (window.DATA_EXT), generado por engine.build.

Qué hace, exactamente:
  1. Encuentra la asignación `const DATA = { ... }` dentro del <script> principal.
  2. Reemplaza el objeto literal por `(window.DATA_EXT || {})`.
  3. Inserta `<script src="data.js"></script>` justo antes de ese <script>,
     para que window.DATA_EXT exista de forma sincrónica antes de renderizar.
Todo lo demás (CSS, markup, imágenes PROPIMG, funciones de render, selector de
período, señales) queda BYTE POR BYTE igual. No se reconstruye nada.

Uso:
    python -m engine.patch_hub <hub_original.html> [site/index.html]
"""
from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Tuple


def _match_object(s: str, open_pos: int) -> int:
    """
    Dado que s[open_pos] == '{', devuelve el índice del '}' que lo cierra,
    ignorando llaves dentro de strings ('...', "...", `...`) y comentarios.
    """
    depth = 0
    i = open_pos
    n = len(s)
    quote = None          # comilla actual si estamos dentro de un string
    while i < n:
        c = s[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        # fuera de string
        if c in "'\"`":
            quote = c
        elif c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i)
            i = n if j == -1 else j
            continue
        elif c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no se encontró el '}' de cierre de DATA")


def patch(html: str) -> Tuple[str, dict]:
    # 1) localizar `const|let|var DATA = {`
    m = re.search(r"(const|let|var)\s+DATA\s*=\s*\{", html)
    if not m:
        raise ValueError("No se encontró la asignación de DATA en el HTML. "
                         "¿Es el hub correcto? (se esperaba `const DATA = {…}`)")
    brace_pos = html.index("{", m.start())
    close_pos = _match_object(html, brace_pos)
    data_len = close_pos - brace_pos + 1

    # 2) reemplazar el objeto por window.DATA_EXT
    new_html = html[:brace_pos] + "(window.DATA_EXT || {})" + html[close_pos + 1:]

    # 3) insertar <script src="data.js"> antes del <script> que contiene DATA.
    #    Buscamos el <script ...> que abre el bloque donde vivía DATA.
    data_assign_pos = new_html.index(m.group(0)[: m.group(0).index("DATA")] + "DATA")
    script_open = new_html.rfind("<script", 0, data_assign_pos)
    if script_open == -1:
        raise ValueError("No se encontró el <script> que contiene DATA.")
    loader = '<script src="data.js"></script>\n'
    if 'src="data.js"' not in new_html:
        new_html = new_html[:script_open] + loader + new_html[script_open:]

    # 4) asegurar <meta charset="utf-8"> al principio (el hub original dependía
    #    del header del server; así los acentos renderizan en cualquier host).
    charset_added = False
    if not re.search(r'<meta[^>]*charset', new_html, re.I):
        new_html = '<meta charset="utf-8">\n' + new_html
        charset_added = True

    info = {
        "data_bytes_removed": data_len,
        "loader_injected": 'src="data.js"' in new_html,
        "charset_added": charset_added,
        "out_bytes": len(new_html),
        "in_bytes": len(html),
    }
    return new_html, info


def main():
    if len(sys.argv) < 2:
        print("uso: python -m engine.patch_hub <hub_original.html> [site/index.html]")
        sys.exit(1)
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("site/index.html")
    html = src.read_text(encoding="utf-8")
    new_html, info = patch(html)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(new_html, encoding="utf-8")
    print(f"✓ {out}")
    print(f"  DATA embebido removido: {info['data_bytes_removed']/1024:.0f} KB")
    print(f"  loader data.js inyectado: {info['loader_injected']}")
    print(f"  tamaño: {info['in_bytes']/1024:.0f} KB -> {info['out_bytes']/1024:.0f} KB "
          f"(imágenes PROPIMG y diseño intactos)")


if __name__ == "__main__":
    main()
