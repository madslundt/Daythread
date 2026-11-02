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

mkdir -p "$(dirname "$destination")"
temporary=$(mktemp -d "${TMPDIR:-/tmp}/daythread-nemotron.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM

verify_sha() {
  if command -v shasum >/dev/null 2>&1; then
    printf '%s  %s\n' "$1" "$2" | shasum -a 256 -c -
  else
    printf '%s  %s\n' "$1" "$2" | sha256sum -c -
  fi
}

case "$(uname -s):$(uname -m)" in
  Darwin:arm64)
    runtime_archive="nemo-speech-0.3.0-macos-aarch64-cpu.tar.gz"
    runtime_sha="2c3a7a719b778daeb14f147e011891f16e4b2ac62388d8b8d8b365cc2eebdf15"
    runtime_binary_sha="11976a265dd137b65227436e9372a8d0822650482089403d51a3de59fb8e73a7"
    runtime_directory="nemo-speech-0.3.0-macos-aarch64-cpu"
    ;;
  Linux:x86_64)
    runtime_archive="nemo-speech-0.3.0-linux-x86_64-cpu.tar.gz"
    runtime_sha="255f12b722ab15b2a38a51cf125d56e3fa97058b528f7e895b39d6a473deb06e"
    runtime_binary_sha="b38ff5adbe5faba96dea1482e1ba9f9c048d3da07f38b1ca2b092a06c968de61"
    runtime_directory="nemo-speech-0.3.0-linux-x86_64-cpu"
    ;;
  *)
    echo "Supported staging targets: macOS arm64 CPU, Linux x86_64 CPU." >&2
    exit 1
    ;;
esac
runtime_url="https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.3.0/$runtime_archive"
model_url="https://huggingface.co/nvidia/Nemotron-3-Diarization/resolve/f667ed73aee57d40cc39428eb768b4fd87a0a29e/Nemotron-3-Diarization.q8_0.gguf"
model_sha="08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1"

curl --fail --location --retry 2 "$runtime_url" -o "$temporary/runtime.tar.gz"
verify_sha "$runtime_sha" "$temporary/runtime.tar.gz"
curl --fail --location --retry 2 "$model_url" -o "$temporary/Nemotron-3-Diarization.q8_0.gguf"
verify_sha "$model_sha" "$temporary/Nemotron-3-Diarization.q8_0.gguf"

mkdir -p "$temporary/unpacked"
tar -xzf "$temporary/runtime.tar.gz" -C "$temporary/unpacked"
if [ ! -f "$temporary/unpacked/$runtime_directory/bin/nemo-speech" ]; then
  echo "Pinned runtime archive has an unexpected layout." >&2
  exit 1
fi
verify_sha "$runtime_binary_sha" "$temporary/unpacked/$runtime_directory/bin/nemo-speech"
mkdir -p "$destination"
cp -R "$temporary/unpacked/$runtime_directory" "$destination/"
cp "$temporary/Nemotron-3-Diarization.q8_0.gguf" "$destination/"
cp "$temporary/runtime.tar.gz" "$destination/$runtime_archive"
cat > "$destination/PROVENANCE.txt" <<EOF
Runtime: NVIDIA/NeMo-Speech.cpp v0.3.0
Runtime source commit: 20999cc921d2b14aa58708f61756a8b2e787ba20
Runtime license: Apache-2.0
Runtime archive: $runtime_archive
Runtime archive SHA-256: $runtime_sha
Runtime executable SHA-256: $runtime_binary_sha
Model: nvidia/Nemotron-3-Diarization
Model revision: f667ed73aee57d40cc39428eb768b4fd87a0a29e
Model license: openmdw-1.1
Model file: Nemotron-3-Diarization.q8_0.gguf
Model SHA-256: $model_sha
EOF
printf 'OMI_NEMO_SPEECH_BIN=%s\nOMI_NEMO_DIARIZATION_MODEL=%s\n' \
  "$destination/$runtime_directory/bin/nemo-speech" \
  "$destination/Nemotron-3-Diarization.q8_0.gguf"
