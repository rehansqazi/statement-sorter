#!/bin/bash
# Double-click to start Statement Sorter with the Python engine.
# First run creates a private Python environment in .venv and installs pdfplumber.
cd "$(dirname "$0")" || exit 1
if ! .venv/bin/python -c "import pdfplumber" 2>/dev/null; then
  echo "Setting up Python (one-off)..."
  python3 -m venv .venv && .venv/bin/pip install --disable-pip-version-check -q -r requirements.txt || {
    echo "Setup failed. Is Python 3 installed and are you online? (brew install python)"; read -r -p "Press Enter to close"; exit 1; }
fi
exec .venv/bin/python server.py "$@"
