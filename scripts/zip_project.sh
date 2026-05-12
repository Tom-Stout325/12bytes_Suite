#!/usr/bin/env bash

PROJECT_NAME="Suite"
OUTPUT="${PROJECT_NAME}.zip"

zip -r "$OUTPUT" . \
  -x "venv/*" \
  -x ".git/*" \
  -x "__pycache__/*" \
  -x "*.pyc" \
  -x ".DS_Store" \
  -x "__MACOSX/*" \
  -x "staticfiles/*"

echo "Created $OUTPUT"