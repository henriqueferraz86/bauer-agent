#!/usr/bin/env sh
set -eu

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" -m playwright install chromium
printf '%s\n' 'Chromium compatível com o Playwright instalado.'
