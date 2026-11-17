#!/usr/bin/env bash
# Run the full test suite (Python unit + integration tests, JS syntax check).
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -v
if command -v node >/dev/null 2>&1; then
  node --check static/app.js && echo "app.js syntax OK"
fi
