#!/bin/bash
# Double-click to start Statement Sorter with the Python engine.
# First run creates a private Python environment in .venv and installs pdfplumber.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Python (one-off)..."
  python3 -m venv .venv && .venv/bin/pip install --disable-pip-version-check -q -r requirements.txt || {
    echo "Setup failed. Is Python 3 installed? (brew install python)"; read -r -p "Press Enter to close"; exit 1; }
fi
exec .venv/bin/python server.py "$@"
