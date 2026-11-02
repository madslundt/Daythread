#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 PROVISIONED_ASSET_DIRECTORY [IMAGE_TAG]" >&2
  exit 2
fi

assets=$1
image=${2:-daythread-nemotron-diarization:0.3.0}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname "$script_dir")
runtime_archive=nemo-speech-0.3.0-linux-x86_64-cpu.tar.gz
runtime_sha=255f12b722ab15b2a38a51cf125d56e3fa97058b528f7e895b39d6a473deb06e
model_file=Nemotron-3-Diarization.q8_0.gguf
model_sha=08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1

if [ "$(uname -s):$(uname -m)" != "Linux:x86_64" ]; then
  echo "The container image is pinned for Linux x86_64." >&2
  exit 1
fi
if [ ! -f "$assets/$runtime_archive" ] || [ ! -f "$assets/$model_file" ]; then
  echo "Provisioned Linux runtime archive and model are required." >&2
  exit 1
fi

verify_sha() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$1" "$2" | sha256sum -c -
  else
    printf '%s  %s\n' "$1" "$2" | shasum -a 256 -c -
  fi
}

verify_sha "$runtime_sha" "$assets/$runtime_archive"
verify_sha "$model_sha" "$assets/$model_file"
temporary=$(mktemp -d "${TMPDIR:-/tmp}/daythread-nemotron-image.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM
mkdir "$temporary/unpacked"
tar -xzf "$assets/$runtime_archive" -C "$temporary/unpacked"
cp -R "$temporary/unpacked/nemo-speech-0.3.0-linux-x86_64-cpu" "$temporary/"
cp "$assets/$model_file" "$temporary/"
cp "$assets/PROVENANCE.txt" "$temporary/"
cp "$repo_root/Dockerfile.nemotron" "$temporary/"
cp "$script_dir/requirements-nemotron.txt" "$temporary/"
cp "$script_dir/nemotron_diarization_server.py" "$temporary/"
cp "$script_dir/nemotron_provenance.py" "$temporary/"
docker build --file "$temporary/Dockerfile.nemotron" --tag "$image" "$temporary"
