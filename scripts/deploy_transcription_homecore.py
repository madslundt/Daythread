#!/usr/bin/env python3
"""Build or deploy the reviewed transcription overlay on the pinned home-core release."""

import argparse
import base64
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path

from transcription_deployment import validate_qualification

BASE_RELEASE = "20261106-assistant-context-a42957dd7359"
BASE_IMAGE = "sha256:90e333fc0b326b8197db5692bcdb827c135e846cb74ae6d1652cedaab159073a"
BASE_HASHES = {
    "configuration": "31b4f703606dde5af358ffbac21601d7c7839f5665f4d3fd6af3f05dffc8627b",
    "settings": "613174e00f2366d41094c94156378bc27496d65692f435b46390aa7d609bcb1e",
    "transcription": "cfa62108d2b4e4debba2b9b54891c1b9c3c90f45fe60044bdaee634bead928f9",
    "transcription_filter": "95495cef901345f7eee2c5990f8cca2eafccb8d98b525233b11ffb0ea05de44d",
    "recording_transcription_worker":
        "40cfee544f8781d30213b79d3534698046187e59ce8849046fc78e29fcd01a7d",
    "recording_transcription_client":
        "d5fd7accff4a33475b4b6634b7aef5b29165f65380f43ec858d36e10b5d51578",
    "recording_transcription_import":
        "20ce134eb343e80c54f79fcd7445c46e551feb8add3547166e377547b8b8a900",
    "source_matching": "e7672eb0ab3878695710f493800a4e9c82c064fff8ac3debd2b38d6111108ecb",
    "recording_transcription_store":
        "c74ac2262f9a85ef6665e3b307f58aba6a148064b22f7c88697391f15f03dae7",
    "stt_producer": None,
}
COMPOSE_HASHES = {
    "compose.yaml": "6004405b5bfff9a3d1c25c647ae452af5f6470e1b2b5fd1151e1b873739f35df",
    "compose.home-core.yaml": "2258c151ca8589c3daf5935572004e710a8b21665d373216178a6280f5090598",
    "compose.release.json": "5ba444537a3ea57cfb07c79867bcf6bd9a5063490ca9d3afbd42ad44876e741a",
}
FILES = [f"src/omi_local/{name}.py" for name in BASE_HASHES]
HELPERS = ["scripts/deploy_transcription_homecore.py", "scripts/transcription_deployment.py",
           "scripts/transcription_shutdown.py"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def qualification(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Qualification receipt is too large")
    return validate_qualification(json.loads(raw)) | {
        "receipt_sha256": sha256(raw),
    }


def payload(root: Path, command: str, receipt: Path | None = None) -> dict:
    files = {name: (root / name).read_bytes() for name in FILES}
    hashes = {Path(name).stem: sha256(value) for name, value in files.items()}
    helpers = {name: sha256((root / name).read_bytes()) for name in HELPERS}
    digest = sha256(json.dumps({"modules": hashes, "helpers": helpers}, sort_keys=True).encode())
    revision = git(root, "rev-parse", "HEAD")
    if command in {"stage", "deploy"} and receipt is None:
        raise ValueError("--qualification is required for staging and deployment")
    if command == "deploy":
        if git(root, "diff", "HEAD", "--", *FILES, *HELPERS):
            raise ValueError("Commit the reviewed source and helpers before deployment")
        for name in HELPERS:
            git(root, "ls-files", "--error-unmatch", name)
        for name in FILES + HELPERS:
            committed = subprocess.check_output(["git", "show", revision + ":" + name], cwd=root)
            expected = hashes[Path(name).stem] if name in FILES else helpers[name]
            if sha256(committed) != expected:
                raise ValueError("Source changed while preparing the artifact; review and retry")
        published = {line.split()[0] for line in git(root, "ls-remote", "origin").splitlines()}
        if revision not in published:
            raise ValueError("Publish this source revision before deployment")
    return {
        "command": command, "base_release": BASE_RELEASE, "base_image": BASE_IMAGE,
        "baseline_hashes": BASE_HASHES, "compose_hashes": COMPOSE_HASHES,
        "release": "20261106-transcription-" + digest[:12], "overlay_sha256": digest,
        "source_revision": revision, "file_hashes": hashes, "helper_hashes": helpers,
        "files": {Path(name).stem: base64.b64encode(data).decode() for name, data in files.items()},
        "qualification": qualification(receipt) if receipt else None,
    }


def remote_program(root: Path) -> str:
    # Bundle the reviewed helper in memory; no remote imports from mutable working copies.
    source = (root / "scripts/transcription_shutdown.py").read_text()
    return ("import sys,types\n_shutdown=types.ModuleType('transcription_shutdown')\n"
            + "exec(" + repr(source) + ",_shutdown.__dict__)\n"
            + "sys.modules['transcription_shutdown']=_shutdown\n"
            + (root / "scripts/transcription_deployment.py").read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "stage", "deploy", "recover"])
    parser.add_argument("--host", default="home-core")
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--release", help="Exact interrupted release, required for recover")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        data = payload(root, args.command, args.qualification)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    if args.command == "recover":
        if not re.fullmatch(r"20261106-transcription-[a-f0-9]{12}", args.release or ""):
            parser.error("recover requires --release with the interrupted release name")
        data["release"] = args.release
    elif args.release:
        parser.error("--release is only valid for recover")
    if args.command == "plan":
        print(json.dumps({k: v for k, v in data.items() if k != "files"}, indent=2))
        return
    remote = remote_program(root)
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", args.host,
         "sudo -n python3 -c " + shlex.quote(remote)],
        input=json.dumps(data), text=True, check=True,
    )


if __name__ == "__main__":
    main()
