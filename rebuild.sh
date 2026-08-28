#!/usr/bin/env bash
# MAIA · un solo comando para regenerar todo desde los exports.
# Uso:  ./rebuild.sh
set -e
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
python3 -m engine.build
echo ""
echo "Listo. Revisá:"
echo "  · site/data.js       -> lo que lee el hub (subilo al repo)"
echo "  · reports/*.html     -> reportes por cliente (abrir y Descargar PDF)"
echo "  · brandbrain/*.csv   -> filas para el Brand Brain"
