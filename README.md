# MAIA · Sistema del Hub (auto-actualizable, sin tokens)

El hub (Centro de Operaciones MAIA) ahora funciona con **datos separados del diseño**:

- **`site/index.html`** = la vista (el diseño pulido, las imágenes, toda la lógica). **No se toca a mano.**
- **`site/data.js`** = los números. Lo **reescribe un motor en Python** que lee el export de Meta y calcula todo. **Sin conversación con Claude = sin gastar tokens.**

Actualizar los números = subir el CSV → el sitio se regenera y se publica solo, en el mismo link.

---

## Cómo se usa cada semana (2 pasos)

1. Guardá el export de Meta (a nivel anuncio, con desglose por día) en la carpeta del cliente:
   `exports/<cliente>/loquesea.csv`  (ej. `exports/vresia/2026-09-01.csv`).
2. Subí el cambio al repo (con GitHub Desktop: *Commit* → *Push*, o arrastrando el archivo en la web de GitHub).

Eso es todo. GitHub corre el motor, regenera `data.js`, y republica el hub en tu link. En ~1–2 minutos el hub muestra los números nuevos.

> ¿Preferís hacerlo en tu compu antes de subir? Corré `./rebuild.sh` (o `python -m engine.build`) y fijate `site/data.js`, `reports/` y `brandbrain/`.

---

## Qué genera el motor (`python -m engine.build`)

| Salida | Qué es |
|---|---|
| `site/data.js` | Los datos que lee el hub (formato compacto por cliente). |
| `site/data.json` | Lo mismo en JSON, por si otra herramienta lo necesita. |
| `reports/<cliente>.html` | Reporte semanal para el cliente. Botón **Descargar PDF** y conclusiones editables. |
| `brandbrain/<cliente>_*.csv` | Filas para el Brand Brain: `_diario`, `_creativos` (con veredicto), `_semanal`. |
| `brandbrain/tablero_agencia.csv` | Una fila por cliente para el Tablero de Agencia. |

Todo el cálculo (ROAS, CPA/CPL, embudo con cuello de botella, señales por creativo, veredictos escalar/iterar/matar/poca data) es **Python puro, por reglas** — sin LLM. Los umbrales y benchmarks viven en `engine/config.py` y se pisan por cliente en `config/clients.yaml`.

---

## Estructura del repo

```
maia-hub/
├─ exports/            ← dejás los CSV acá, una carpeta por cliente
│  ├─ vresia/…csv
│  └─ …
├─ engine/             ← el motor Python (no hace falta tocarlo)
│  ├─ parse.py         ·  lee el export y mapea columnas
│  ├─ hubdata.py       ·  arma el formato que consume el hub
│  ├─ metrics.py       ·  ROAS/CPA/embudo/significancia
│  ├─ signals.py       ·  veredictos por creativo (reglas)
│  ├─ report.py        ·  reporte al cliente (PDF)
│  ├─ brandbrain.py    ·  filas para el Brand Brain
│  ├─ patch_hub.py     ·  desengancha el DATA del index.html (se corre 1 vez)
│  └─ build.py         ·  orquesta todo
├─ config/
│  ├─ clients.yaml     ← registro de clientes (nombre, moneda, objetivo, margen)
│  └─ mapping.yaml     ← alias de columnas (calibrado; se toca solo si Meta cambia headers)
├─ site/
│  ├─ index.html       ← la vista (se genera 1 vez con patch_hub, después queda fijo)
│  └─ data.js          ← generado por el motor
├─ reports/            ← generado
├─ brandbrain/         ← generado
├─ .github/workflows/build.yml   ← auto-deploy en cada subida de CSV
├─ requirements.txt
└─ rebuild.sh
```

---

## Puesta en marcha (una sola vez)

1. **Generar `site/index.html`** a partir del hub actual (desengancha el DATA embebido):
   ```
   python -m engine.patch_hub <hub_original.html> site/index.html
   ```
   (`<hub_original.html>` es el archivo del hub bueno — el del artifact / el que está en Netlify.) Esto conserva **todo** el diseño y las imágenes; solo cambia de dónde salen los números.

2. **Crear el repo en GitHub** y subir esta carpeta.

3. **Activar GitHub Pages**: en el repo → *Settings* → *Pages* → *Build and deployment* → Source: **GitHub Actions**. Tu link queda `https://<usuario>.github.io/<repo>/`.

4. Listo: cada vez que subas un CSV a `exports/`, el hub se regenera y se publica en ese link.

> **Privacidad:** GitHub Pages gratis necesita repo **público** (los CSV quedarían visibles, igual que hoy el hub es un link público). Si querés repo privado con link propio, la alternativa equivalente es **Netlify conectado a un repo privado** (mismo `build.yml` traducido a un `netlify.toml`; te lo dejo armado si vas por ahí).

---

## Calibración del parser (ya hecha, por si Meta cambia)

El mapeo de columnas está calibrado y **validado contra los 5 exports reales** (Piera, Mossy, Alba, Paz, Vresia): los totales coinciden exactamente con el Estado de Clientes. Si algún día Meta cambia los nombres de columna, corré:

```
python -m engine.parse exports/vresia/<archivo>.csv config/mapping.yaml
```

Te imprime qué columna cayó en qué campo y qué quedó sin mapear. Si algo falta, agregás el alias en `config/mapping.yaml` (sin tocar código).

Detalles del export que espera: **nivel anuncio**, **desglose por día** (columna *Inicio del informe*), con columnas de video (*Reproducciones de video de 3 segundos*, *ThruPlays*) y de embudo (*Visitas a la página de destino*, *Artículos agregados al carrito*, *Pagos iniciados*, *Compras*, *Valor de conversión de compras*). Para lead-gen, los leads salen de *Resultados*.

---

## Migrar a full hands-free (después, sin rehacer nada)

Hoy es **semi-automático** (subís el CSV → se publica solo). Para que sea **100% solo** (un robot lee el mail de Meta de los lunes y sube el CSV), no se rehace nada: se **descomenta** el bloque `schedule` en `.github/workflows/build.yml` y se conecta el disparador que deja el CSV en `exports/`. El resto del sistema queda igual.
