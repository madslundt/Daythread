"""Private CAS installation and rollback of one qualified bootstrap STT profile."""

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

from transcription_qualification import PIN_SETTINGS

FOUNDATION_STATUS = "qualified_transcription_foundation"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def settings_path(info: dict, data: Path, expected: dict) -> tuple[Path, dict]:
    env = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
    directory = PurePosixPath(env.get("OMI_DATA_DIR", "data"))
    if not directory.is_absolute():
        raise RuntimeError("Bootstrap archive directory must be explicitly absolute")
    matches = [item for item in info.get("Mounts", [])
               if directory.is_relative_to(PurePosixPath(item["Destination"]))]
    if not matches:
        raise RuntimeError("Bootstrap archive is not in an inspected data mount")
    mount = max(matches, key=lambda item: len(item["Destination"]))
    if Path(mount["Source"]).resolve() != data.resolve():
        raise RuntimeError("Bootstrap archive mount differs from the audited data volume")
    relative = directory.relative_to(PurePosixPath(mount["Destination"]))
    path = data / str(relative) / ".private-settings.json"
    if (path.is_symlink() or not path.is_file()
            or not path.resolve().is_relative_to(data.resolve())
            or str(path.relative_to(data)) != expected["relative_path"]
            or sha(path.read_bytes()) != expected["sha256"]):
        raise RuntimeError("Private settings target or CAS baseline changed")
    return path, env


def atomic_settings(path: Path, raw: bytes, uid: int, gid: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".transcription-settings-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            os.fchown(output.fileno(), uid, gid)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def stage_profile(qualification: dict, config: dict, release: Path, info: dict,
                  data: Path) -> dict | None:
    if qualification["status"] != FOUNDATION_STATUS:
        return None
    target, env = settings_path(info, data, qualification["settings_target"])
    raw = target.read_bytes()
    if sha(raw) != qualification["settings_target"]["sha256"]:
        raise RuntimeError("Private settings CAS changed during staging")
    if stat.S_IMODE(target.stat().st_mode) != 0o600:
        raise RuntimeError("Private settings must retain mode 0600")
    value = json.loads(raw)
    overrides = value.get("overrides")
    if not isinstance(overrides, dict):
        raise RuntimeError("Private settings overrides are invalid")
    endpoint = overrides.get("recording_transcription_base_url",
                             env.get("OMI_RECORDING_TRANSCRIPTION_BASE_URL", ""))
    expected_url = qualification["profile"]["recording_transcription_base_url"]
    if not isinstance(endpoint, str) or endpoint.rstrip("/") != expected_url:
        raise RuntimeError("Recording role credential must stay on its existing gateway URL")
    role_key = overrides.get("recording_transcription_api_key",
                             env.get("OMI_RECORDING_TRANSCRIPTION_API_KEY"))
    if not isinstance(role_key, str) or not role_key:
        raise RuntimeError("Existing dedicated private recording role key is required")
    # Only this allowlisted profile changes. Preserve all other settings and credentials.
    value["overrides"] = overrides | qualification["profile"] | {"stt_api_key": role_key}
    for key in {"stt_expected_producer", *PIN_SETTINGS.values()}:
        value["overrides"].pop(key, None)
    updated = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    parent = release / ".transcription-profile"
    parent.mkdir(mode=0o700)
    staged = parent / "settings.json"
    with open(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        output.write(updated)
    owner = target.stat()
    environment = config["services"]["backend"].setdefault("environment", {})
    if not isinstance(environment, dict):
        raise RuntimeError("Expected explicit Compose environment mapping")
    changes = qualification["profile"] | {
        "stt_expected_producer": qualification["producer"],
        **{setting: qualification["producer"][name] for name, setting in PIN_SETTINGS.items()},
    }
    for key, item in changes.items():
        environment["OMI_" + key.upper()] = (
            item if isinstance(item, str) else json.dumps(item, separators=(",", ":"))
        )
    return {"relative_path": str(target.relative_to(data)), "before_sha256": sha(raw),
            "after_sha256": sha(updated), "uid": owner.st_uid, "gid": owner.st_gid,
            "credential_policy": "reuse_existing_recording_role_key",
            "secret_values_returned": False}


def profile_file(data: Path, change: dict) -> Path:
    path = data / change["relative_path"]
    if path.is_symlink() or not path.resolve().is_relative_to(data.resolve()):
        raise RuntimeError("Private settings target changed")
    return path


def apply_profile(data: Path, release: Path, saved: Path, change: dict | None) -> None:
    if change is None:
        return
    target = profile_file(data, change)
    before = (saved / change["relative_path"]).read_bytes()
    after = (release / ".transcription-profile/settings.json").read_bytes()
    if (sha(target.read_bytes()) != change["before_sha256"]
            or sha(before) != change["before_sha256"] or sha(after) != change["after_sha256"]):
        raise RuntimeError("Private settings CAS changed before profile installation")
    atomic_settings(target, after, change["uid"], change["gid"])


def restore_profile(data: Path, saved: Path, change: dict | None,
                    context: tuple[Path, dict] | None = None) -> None:
    if context:
        base, manifest = context
        if (manifest.get("env_sha256")
                and sha((base / ".env").read_bytes()) != manifest["env_sha256"]):
            raise RuntimeError("Rollback release environment changed; preserve newer configuration")
        for name, expected in manifest.get("compose_hashes", {}).items():
            if sha((base / "config" / name).read_bytes()) != expected:
                raise RuntimeError("Rollback Compose context changed; preserve newer configuration")
    if change is None:
        return
    target = profile_file(data, change)
    current = sha(target.read_bytes())
    if current == change["before_sha256"]:
        return
    if current != change["after_sha256"]:
        raise RuntimeError("Private settings changed after installation; preserve newer edits")
    before = (saved / change["relative_path"]).read_bytes()
    if sha(before) != change["before_sha256"]:
        raise RuntimeError("Private settings rollback backup changed")
    atomic_settings(target, before, change["uid"], change["gid"])


def verify_profile(qualification: dict, output, container: str) -> None:
    if qualification["status"] != FOUNDATION_STATUS:
        return
    code = """
import json,sys
from omi_local.settings import Settings
from omi_local.configuration import Configuration
initial=Settings(_env_file=None)
active=Configuration(initial).startup(initial)
profile,producer,pin_settings=json.loads(sys.argv[1])
valid=(all(getattr(active,key)==value for key,value in profile.items())
       and active.stt_expected_producer==producer
       and all(getattr(active,setting)==producer[name] for name,setting in pin_settings.items())
       and active.stt_api_key and active.recording_transcription_api_key
       and active.stt_api_key.get_secret_value()
           ==active.recording_transcription_api_key.get_secret_value())
if not valid:raise RuntimeError('Installed foundation profile differs')
print(json.dumps({'profile_matches':True,'producer_matches':True,'credential_reused':True}))
"""
    proof = json.loads(output("docker", "exec", container, "python", "-c", code, json.dumps([
        qualification["profile"], qualification["producer"], PIN_SETTINGS,
    ])))
    if proof != {"profile_matches": True, "producer_matches": True, "credential_reused": True}:
        raise RuntimeError("Installed foundation profile verification failed")
