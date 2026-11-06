"""Remote, narrowly scoped deployment implementation; invoked by the pinned CLI."""

import base64
import fcntl
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path("/home/mads/projects/omi")
CONTAINER = "omi-local-backend-1"
SITE = "/opt/venv/lib/python3.15/site-packages/omi_local"
MODULES = {"configuration", "settings", "transcription", "transcription_filter",
           "recording_transcription_worker", "recording_transcription_client",
           "recording_transcription_import"}
BUSY = {"queued", "running", "processing", "transcribing", "summarizing", "pending"}
DATA = Path("/var/lib/docker/volumes/omi-local_omi-data/_data")
SMOKE = r'''
import asyncio,io,json,wave
import httpx
from omi_local.settings import Settings
from omi_local.transcription_filter import filter_transcription_audio
from omi_local.inference import Inference
settings=Settings(_env_file=None)
assert settings.stt_filter_speech is False
out=io.BytesIO()
with wave.open(out,'wb')as w:
 w.setparams((1,2,16000,0,'NONE','NONE'));w.writeframes(b'\x10\x01'*32000)
data=out.getvalue()
result=filter_transcription_audio(data,chunk_seconds=1,boundary_search_seconds=0,
 activity={'status':'observed','method':'webrtcvad','speech':[[0,.02],[1.98,2]],
           'non_speech':[[.02,1.98]]})
assert result['retained_seconds']==2 and [x[0]for x in result['spans']]==[0,1]
assert filter_transcription_audio(data,enabled=False)['spans']==[(0.0,data)]
async def check():
 def reject(request):raise AssertionError('Smoke check must not call a provider')
 async with httpx.AsyncClient(transport=httpx.MockTransport(reject))as client:
  inference=Inference(settings,client)
  parts,status=await inference._align_chunks([(0,data,'Hi hej','mixed',0)],settings,2)
  assert parts==[] and status['status']=='partial' and status['languages']==['mixed']
asyncio.run(check())
print(json.dumps({'default_filter':False,'original_windows':'passed','mixed_alignment':'passed'}))
'''


def run(*args: str, **kwargs):
    kwargs.setdefault("timeout", 180)
    return subprocess.run(args, check=True, **kwargs)


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True, timeout=60).strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: dict) -> None:
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def checkpoint(path: Path, receipt: dict, phase: str) -> None:
    receipt["phase"] = phase
    temporary = path.with_suffix(".tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def inspect() -> dict:
    return json.loads(output("docker", "inspect", CONTAINER))[0]


def installed(command: list[str]) -> dict:
    code = ("import pathlib,hashlib,json;root=pathlib.Path(" + repr(SITE) + ");"
            "print(json.dumps({n:hashlib.sha256((root/(n+'.py')).read_bytes()).hexdigest()"
            "for n in " + repr(sorted(MODULES)) + "}))")
    return json.loads(output(*command, "python", "-c", code))


def baseline(payload: dict) -> Path:
    base = ROOT / "releases" / payload["base_release"]
    current = inspect()
    if ((ROOT / "current").resolve() != base or current["Image"] != payload["base_image"]
            or current["Config"]["Image"] != "omi-local:" + payload["base_release"]
            or current["State"]["Status"] != "running"):
        raise RuntimeError("Current release/image changed; no deployment action performed")
    for name, expected in payload["compose_hashes"].items():
        if digest(base / "config" / name) != expected:
            raise RuntimeError("Baseline compose configuration changed")
    if installed(["docker", "exec", CONTAINER]) != payload["baseline_hashes"]:
        raise RuntimeError("Installed baseline modules changed")
    return base


def compose(release: Path, *args: str, image: str) -> None:
    files = ["compose.yaml", "compose.home-core.yaml", "compose.release.json"]
    command = ["docker", "compose", "-p", "omi-local"]
    for name in files:
        command.extend(["-f", str(release / "config" / name)])
    # Pin the running/rollback image even if an old release still uses a mutable tag.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as override:
        json.dump({"services": {"backend": {"image": image}}}, override)
        override.flush()
        run(*command, "-f", override.name, *args,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def stage(payload: dict) -> dict:
    base = baseline(payload)
    release = ROOT / "releases" / payload["release"]
    if release.exists():
        raise RuntimeError("Release already exists; keep its evidence and review before retrying")
    shutil.copytree(base, release, symlinks=True)
    context = release / ".transcription-build"
    context.mkdir(mode=0o700)
    dockerfile = f"FROM {payload['base_image']}\nUSER root\n"
    for name, encoded in payload["files"].items():
        raw = base64.b64decode(encoded, validate=True)
        if hashlib.sha256(raw).hexdigest() != payload["file_hashes"][name]:
            raise ValueError("Invalid source hash")
        (context / (name + ".py")).write_bytes(raw)
        (release / "src/omi_local" / (name + ".py")).write_bytes(raw)
        dockerfile += f"COPY --chown=10001:10001 {name}.py {SITE}/{name}.py\n"
    dockerfile += ('LABEL org.daythread.transcription-overlay="' + payload["overlay_sha256"]
                   + '"\nUSER omi\n')
    (context / "Dockerfile").write_text(dockerfile)
    tag = "omi-local:" + payload["release"]
    # BuildKit treats a bare local config digest as a registry tag. The installed legacy
    # builder supports FROM <exact local image ID>; fail rather than use a mutable tag.
    run("docker", "build", "--network=none", "--pull=false", "-t", tag, str(context),
        env=os.environ | {"DOCKER_BUILDKIT": "0"})
    image = output("docker", "image", "inspect", "--format", "{{.Id}}", tag)
    isolated = ["docker", "run", "--rm", "--network", "none", "--read-only",
                "--tmpfs", "/tmp:size=16m", image]
    if installed(isolated) != payload["file_hashes"]:
        raise RuntimeError("Built image does not contain the reviewed source")
    smoke = json.loads(output(*isolated, "python", "-c", SMOKE))
    config_path = release / "config/compose.release.json"
    config = json.loads(config_path.read_text())
    config["services"]["backend"]["image"] = image
    config["services"]["backend"]["build"] = {
        "context": "../.transcription-build", "dockerfile": "Dockerfile",
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    manifest = {k: v for k, v in payload.items() if k not in ("files", "command")}
    manifest.update(image_id=image, smoke=smoke, compose_sha256=digest(config_path))
    save(release / "config/transcription-overlay.json", manifest)
    return {"status": "staged", "release": str(release), "image_id": image, "smoke": smoke}


def queue_snapshot() -> dict:
    result = {}
    for path in DATA.rglob("*.sqlite3"):
        tables = {}
        with sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True) as db:
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                quoted = '"' + name.replace('"', '""') + '"'
                if "state" in {row[1] for row in db.execute("PRAGMA table_info(" + quoted + ")")}:
                    tables[name] = dict(db.execute("SELECT state,count(*) FROM " + quoted
                                                   + " GROUP BY state"))
        result[str(path.relative_to(DATA))] = tables
    if not result:
        raise RuntimeError("No durable database found; cannot inspect worker jobs")
    return result


def require_idle(snapshot: dict, admission: dict) -> None:
    if type(admission.get("active_requests")) is not int or admission["active_requests"] != 0:
        raise RuntimeError("Gateway has active or unknown requests; retry after it drains")
    for tables in snapshot.values():
        for table, states in tables.items():
            if any(states.get(state, 0) for state in BUSY):
                raise RuntimeError("Durable work is pending/active in " + table + "; retry later")


def admission() -> dict:
    code = ("import json,ssl,urllib.request;"
            "c=ssl.create_default_context(cafile='/etc/omi/gateway-root.crt');"
            "r=urllib.request.urlopen('https://ai.home.arpa:8443/llm/status',context=c,timeout=5);"
            "d=json.load(r);print(json.dumps({k:d.get(k)for k in "
            "('active_requests','available','state')}))")
    return json.loads(output("docker", "exec", CONTAINER, "python", "-c", code))


def backup(destination: Path) -> dict:
    destination.mkdir(mode=0o700, exist_ok=True)
    hashes = {}
    for source in DATA.rglob("*.sqlite3"):
        target = destination / source.relative_to(DATA)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True) as before:
            with sqlite3.connect(target) as saved:
                before.backup(saved)
                if saved.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Database backup verification failed")
        os.chmod(target, 0o600)
        hashes[str(source.relative_to(DATA))] = digest(target)
    for source in DATA.rglob(".private-settings.json"):
        target = destination / source.relative_to(DATA)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        os.chmod(target, 0o600)
        hashes[str(source.relative_to(DATA))] = digest(target)
    return hashes


def ready(expected_image: str, expected_hashes: dict) -> None:
    for attempt in range(45):
        current = inspect()
        if current["State"].get("Health", {}).get("Status") == "healthy":
            if current["Image"] != expected_image:
                raise RuntimeError("Unexpected running image")
            if installed(["docker", "exec", CONTAINER]) != expected_hashes:
                raise RuntimeError("Installed source verification failed")
            return
        if attempt < 44:
            time.sleep(1)
    raise RuntimeError("Backend did not become healthy")


def rollback(receipt: dict, manifest: dict, path: Path) -> None:
    """Recover code/configuration only; interrupted/newer user data is never restored."""
    handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        checkpoint(path, receipt, "rolling_back")
        base, release = Path(receipt["base_release"]), Path(receipt["release"])
        if (ROOT / "current").resolve() not in (base, release):
            raise RuntimeError("Current pointer belongs to another release; refusing recovery")
        try:
            current = inspect()
        except subprocess.CalledProcessError:
            current = None
        if current and current["Image"] not in (receipt["base_image"], receipt["image_id"]):
            raise RuntimeError("Running image belongs to another release; refusing recovery")
        compose(base, "up", "-d", "--no-deps", "--no-build", "backend",
                image=receipt["base_image"])
        ready(receipt["base_image"], manifest["baseline_hashes"])
        if (ROOT / "current").resolve() == release:
            temporary = ROOT / ("current.recover-" + release.name)
            temporary.symlink_to(base)
            temporary.replace(ROOT / "current")
        receipt["status"] = "rolled_back"
        checkpoint(path, receipt, "rolled_back")
    except BaseException as error:
        receipt.update(status="rollback_failed", rollback_error=type(error).__name__)
        checkpoint(path, receipt, "recovery_required")
        raise
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def recover(payload: dict) -> dict:
    release = ROOT / "releases" / payload["release"]
    manifest = json.loads((release / "config/transcription-overlay.json").read_text())
    if any(manifest[key] != payload[key] for key in ("base_image", "base_release")):
        raise RuntimeError("Recovery baseline differs; review the retained artifact")
    path = ROOT / "backups" / payload["release"] / "deployment.json"
    receipt = json.loads(path.read_text())
    if (receipt["image_id"] != manifest["image_id"]
            or receipt["base_image"] != payload["base_image"]
            or receipt["base_release"] != str(ROOT / "releases" / payload["base_release"])
            or receipt["release"] != str(release)):
        raise RuntimeError("Recovery receipt identity differs")
    if receipt["status"] in ("deployed", "rolled_back"):
        raise RuntimeError("Transaction is complete; inspect before attempting another rollback")
    rollback(receipt, manifest, path)
    return {"status": "rolled_back", "receipt": str(path)}


def deploy(payload: dict) -> dict:
    base = baseline(payload)
    release = ROOT / "releases" / payload["release"]
    manifest = json.loads((release / "config/transcription-overlay.json").read_text())
    for key in ("overlay_sha256", "file_hashes", "helper_hashes", "base_image", "base_release"):
        if manifest[key] != payload[key]:
            raise RuntimeError("Staged artifact differs from reviewed source")
    if digest(release / "config/compose.release.json") != manifest["compose_sha256"]:
        raise RuntimeError("Staged compose configuration changed")
    image = manifest["image_id"]
    actual = installed(["docker", "run", "--rm", "--network", "none", "--read-only", image])
    if actual != payload["file_hashes"]:
        raise RuntimeError("Staged image source changed")
    before = queue_snapshot()
    gateway = admission()
    require_idle(before, gateway)
    saved = ROOT / "backups" / payload["release"]
    if saved.exists():
        raise RuntimeError("A deployment receipt already exists; inspect before retrying")
    previous = ROOT / "previous.transcription"
    temporary = ROOT / ("current." + payload["release"])
    if any(path.exists() or path.is_symlink() for path in (previous, temporary)):
        raise RuntimeError("Deployment symlink already exists; inspect before replacing it")
    receipt = {"base_release": str(base), "base_image": payload["base_image"],
               "release": str(release), "image_id": image,
               "source_revision": payload["source_revision"],
               "qualification": payload["qualification"], "before_jobs": before,
               "gateway_admission": gateway, "rollback_config": str(base / "config"),
               "overlay_sha256": payload["overlay_sha256"], "status": "in_progress"}
    # Recheck after the isolated image check and before stopping the real service.
    baseline(payload)
    require_idle(queue_snapshot(), admission())
    saved.mkdir(mode=0o700)
    path = saved / "deployment.json"
    checkpoint(path, receipt, "prepared")
    try:
        checkpoint(path, receipt, "stopping")
        run("docker", "stop", "--time", "60", CONTAINER,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        stopped = inspect()
        if stopped["State"]["ExitCode"] != 0 or stopped["State"].get("OOMKilled"):
            raise RuntimeError("Backend did not stop gracefully")
        receipt["stopped_jobs"] = queue_snapshot()
        require_idle(receipt["stopped_jobs"], gateway)
        checkpoint(path, receipt, "backing_up")
        receipt["backup_hashes"] = backup(saved)
        checkpoint(path, receipt, "starting")
        compose(release, "up", "-d", "--no-deps", "--no-build", "backend", image=image)
        checkpoint(path, receipt, "verifying")
        ready(image, payload["file_hashes"])
        for name, expected in receipt["backup_hashes"].items():
            if name.endswith(".private-settings.json") and digest(DATA / name) != expected:
                raise RuntimeError("Saved settings changed during restart")
        receipt["after_jobs"] = queue_snapshot()
        if (ROOT / "current").resolve() != base:
            raise RuntimeError("Current pointer changed; preserving conflicting release")
        checkpoint(path, receipt, "switching_pointer")
        previous.symlink_to(base)
        temporary.symlink_to(release)
        temporary.replace(ROOT / "current")
        receipt["status"] = "deployed"
        checkpoint(path, receipt, "complete")
    except BaseException as error:
        receipt["failure"] = type(error).__name__
        rollback(receipt, manifest, path)
        raise
    return {"status": "deployed", "release": str(release), "image_id": image,
            "receipt": str(saved / "deployment.json")}


@contextmanager
def deployment_lock():
    lock = os.open(ROOT / ".transcription-deployment.lock",
                   os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(lock).st_uid != os.geteuid():
            raise RuntimeError("Deployment lock has an unexpected owner")
        os.fchmod(lock, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(lock)


def main() -> None:
    def interrupted(signum, _frame):
        raise InterruptedError("Deployment interrupted by signal " + str(signum))

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    payload = json.load(sys.stdin)
    if set(payload["files"]) != MODULES or set(payload["file_hashes"]) != MODULES:
        raise ValueError("Only the seven reviewed modules may be copied")
    with deployment_lock():
        if payload["command"] == "stage":
            result = stage(payload)
        elif (payload["command"] == "deploy"
              and (payload.get("qualification") or {}).get("status") == "qualified"):
            result = deploy(payload)
        elif payload["command"] == "recover":
            result = recover(payload)
        else:
            raise ValueError("Unsupported or unqualified deployment")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
