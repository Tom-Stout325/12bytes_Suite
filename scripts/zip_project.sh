#!/usr/bin/env bash

set -e

PROJECT_NAME="Suites"
OUTPUT="${PROJECT_NAME}.zip"

# Remove the previous archive so it isn't included in the new one.
rm -f "$OUTPUT"

zip -r "$OUTPUT" . \
  -x "venv/*" \
  -x ".venv/*" \
  -x ".git/*" \
  -x ".idea/*" \
  -x ".vscode/*" \
  -x ".codex/*" \
  -x "*/__pycache__/*" \
  -x "*.pyc" \
  -x "*.pyo" \
  -x ".DS_Store" \
  -x "*/.DS_Store" \
  -x "__MACOSX/*" \
  -x "staticfiles/*" \
  -x "media/*" \
  -x "*.sqlite3" \
  -x "*.db" \
  -x "*.log" \
  -x ".env" \
  -x ".env.*" \
  -x "*.zip"

echo "Created $OUTPUT"