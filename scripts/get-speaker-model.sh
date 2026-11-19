#!/usr/bin/env bash
# Download the speaker-embedding model used to keep speaker numbers stable across chunks.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models
f=models/nemo_en_titanet_small.onnx
[[ -s $f ]] && { echo "already present: $f"; exit 0; }
curl -fL -o "$f" https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx
echo "saved $f"
