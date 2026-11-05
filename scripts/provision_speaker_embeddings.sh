#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 DESTINATION" >&2
  exit 2
fi
destination=$1
if [ -e "$destination" ]; then
  echo "Destination already exists: $destination" >&2
  exit 1
fi

model=nemo_en_titanet_small.onnx
checksum=ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e
url="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/$model"
temporary=$(mktemp -d "${TMPDIR:-/tmp}/daythread-speaker-embedding.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
curl --fail --location --retry 2 "$url" -o "$temporary/$model"
if command -v shasum >/dev/null 2>&1; then
  printf '%s  %s\n' "$checksum" "$temporary/$model" | shasum -a 256 -c -
else
  printf '%s  %s\n' "$checksum" "$temporary/$model" | sha256sum -c -
fi
mkdir -p "$destination"
cp "$temporary/$model" "$destination/"
cat > "$destination/SPEAKER-PROVENANCE.txt" <<EOF
Model: NVIDIA NeMo TitaNet-Small, sherpa-onnx ONNX export
Copyright: NVIDIA Corporation
Model source: https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/titanet_small
Model license: NeMo Toolkit license (Apache-2.0)
ONNX export source: https://github.com/k2-fsa/sherpa-onnx/tree/master/scripts/nemo/speaker-verification
Model download: $url
Model SHA-256: $checksum
Runtime: sherpa-onnx 2.0.1 (Apache-2.0), CPU
EOF
printf 'OMI_SPEAKER_EMBEDDING_MODEL=%s\nOMI_SPEAKER_EMBEDDING_SHA256=%s\n' \
  "$destination/$model" "$checksum"
