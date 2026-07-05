#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-build.txt
python3 build_desktop.py --clean

echo
echo "macOS app created at: dist/ProtocolPdfDiff.app"
