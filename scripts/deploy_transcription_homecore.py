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

BASE_RELEASE = "20261106-parrot-settings-001ce3b355d5"
BASE_IMAGE = "sha256:b7b3337407352af28655f5cd2cc5de16805be76290fc984b467250a076994d48"
BASE_HASHES = {
    "configuration": "6e335d2e3f4a27edaa92e7758c8dc30e439647e0ba6df7b7a815417e438481e1",
    "settings": "149317c64766d610bf6e95471ec3ef35883dfb209dfbb5610f93da2ba2190e9a",
    "transcription": "d1d5e11194e40b0ef2f08f1b61013f2405092858dbd1950246c15fff837a5af6",
    "transcription_filter": "2a22e5cb673608646b01a8550215cf181f868f55f38259d623606f343039aae6",
    "recording_transcription_worker":
        "daacbcd09be0dad768bce8b6b3e3a7abe48469d5de71c4a04c2dd79ba334a0ab",
    "recording_transcription_client":
        "7029301ca33dbc34bc2e54e1dcebd6d70d4e0810e17c9c1492c9f83f2785165e",
    "recording_transcription_import":
        "2f5abf59f84f18d2c00e7de94522c70a7b2a24dd22a7eb1bb4e87c1229b406a4",
}
COMPOSE_HASHES = {
    "compose.yaml": "6004405b5bfff9a3d1c25c647ae452af5f6470e1b2b5fd1151e1b873739f35df",
    "compose.home-core.yaml": "2258c151ca8589c3daf5935572004e710a8b21665d373216178a6280f5090598",
    "compose.release.json": "313f30956f2493f15884274ad54474cce4a70f31151911267c49f65f46129c1d",
}
FILES = [f"src/omi_local/{name}.py" for name in BASE_HASHES]
HELPERS = ["scripts/deploy_transcription_homecore.py", "scripts/transcription_deployment.py"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def qualification(path: Path) -> dict:
    if path.stat().st_size > 1_000_000:
        raise ValueError("Qualification receipt is too large")
    raw = path.read_bytes()
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get("status") != "qualified"
            or not re.fullmatch(r"[a-f0-9]{40}", value.get("source_revision", ""))
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", value.get("image_id", ""))):
        raise ValueError("A completed, identified STT qualification receipt is required")
    return {key: value[key] for key in ("status", "source_revision", "image_id")} | {
        "receipt_sha256": sha256(raw),
    }


def payload(root: Path, command: str, receipt: Path | None = None) -> dict:
    files = {name: (root / name).read_bytes() for name in FILES}
    hashes = {Path(name).stem: sha256(value) for name, value in files.items()}
    helpers = {name: sha256((root / name).read_bytes()) for name in HELPERS}
    digest = sha256(json.dumps({"modules": hashes, "helpers": helpers}, sort_keys=True).encode())
    revision = git(root, "rev-parse", "HEAD")
    if command == "deploy":
        if receipt is None:
            raise ValueError("--qualification is required for deployment")
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
    remote = (root / "scripts/transcription_deployment.py").read_text()
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", args.host,
         "sudo -n python3 -c " + shlex.quote(remote)],
        input=json.dumps(data), text=True, check=True,
    )


if __name__ == "__main__":
    main()
