"""Verify the exact local runtime and model before exposing pinned provenance."""

import hashlib
import platform
from pathlib import Path

MODEL_ID = "nvidia/Nemotron-3-Diarization"
MODEL_REVISION = "f667ed73aee57d40cc39428eb768b4fd87a0a29e"
MODEL_FILE = "Nemotron-3-Diarization.q8_0.gguf"
MODEL_SHA256 = "08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1"
RUNTIME_VERSION = "0.3.0"
RUNTIME_COMMIT = "20999cc921d2b14aa58708f61756a8b2e787ba20"
PLATFORMS = {
    "Darwin:arm64": {
        "archive": "nemo-speech-0.3.0-macos-aarch64-cpu.tar.gz",
        "archive_sha256": "2c3a7a719b778daeb14f147e011891f16e4b2ac62388d8b8d8b365cc2eebdf15",
        "binary_sha256": "11976a265dd137b65227436e9372a8d0822650482089403d51a3de59fb8e73a7",
    },
    "Linux:x86_64": {
        "archive": "nemo-speech-0.3.0-linux-x86_64-cpu.tar.gz",
        "archive_sha256": "255f12b722ab15b2a38a51cf125d56e3fa97058b528f7e895b39d6a473deb06e",
        "binary_sha256": "b38ff5adbe5faba96dea1482e1ba9f9c048d3da07f38b1ca2b092a06c968de61",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_installation(
    binary: Path, model: Path, provenance: Path, platform_key: str | None = None,
) -> dict | None:
    """Return provenance only when manifest and actual files match pinned hashes."""
    platform_key = platform_key or f"{platform.system()}:{platform.machine()}"
    pinned = PLATFORMS.get(platform_key)
    if pinned is None or not binary.is_file() or not model.is_file() or not provenance.is_file():
        return None
    if not binary.stat().st_mode & 0o111:
        return None
    try:
        values = {}
        for line in provenance.read_text().splitlines():
            key, separator, value = line.partition(": ")
            if not separator or key in values:
                return None
            values[key] = value
        if values != {
            "Runtime": f"NVIDIA/NeMo-Speech.cpp v{RUNTIME_VERSION}",
            "Runtime source commit": RUNTIME_COMMIT,
            "Runtime license": "Apache-2.0",
            "Runtime archive": pinned["archive"],
            "Runtime archive SHA-256": pinned["archive_sha256"],
            "Runtime executable SHA-256": pinned["binary_sha256"],
            "Model": MODEL_ID,
            "Model revision": MODEL_REVISION,
            "Model license": "openmdw-1.1",
            "Model file": MODEL_FILE,
            "Model SHA-256": MODEL_SHA256,
        }:
            return None
        if model.name != MODEL_FILE or sha256(model) != MODEL_SHA256:
            return None
        if sha256(binary) != pinned["binary_sha256"]:
            return None
        return {"model": MODEL_ID, "model_revision": MODEL_REVISION,
                "runtime": "NeMo-Speech.cpp", "runtime_version": RUNTIME_VERSION}
    except (OSError, UnicodeError):
        return None
