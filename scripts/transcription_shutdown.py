"""Audited inbox recovery and bounded SIGTERM observation for the pinned app image."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

COMMAND = ["python", "-m", "uvicorn", "omi_local.api:create_app", "--factory", "--workers",
           "1", "--host", "0.0.0.0", "--port", "8787"]
SOURCE_HASHES = {
    "omi_local/api.py": "c95c343c7245b5bc5fac0dc3aa197aa53668052159eb84a59b10393bcc329324",
    "omi_local/profiles.py": "7b83e0fd58ede5909dbff1c702f7352c32c80c9d583eb26dc70e5690cb2d22c5",
    "omi_local/worker.py": "6ea2d604feb20e6ae241343f40e0acf873de9b2be671d03533d390945355d335",
    "omi_local/inbox_jobs.py": "ae1ac7aef7ad6ea83cc608e3b85914a73282675ee178c6414de7679e41162e00",
    "omi_local/inbox_store.py": "1032c227a121dda179026209dc693ab27cf4e9264ce32518996c0c7e272ae398",
    "omi_local/notifications.py":
        "93763a6d0c4c6beb47062d9e85b41095f5592a7834549146815c602aee4c94f3",
    "omi_local/notification_store.py":
        "fb2c9e71ef63973f2fe9a9474e13b062d316ecc2da8b95e35e0f3db1f8cb4edc",
    "uvicorn/server.py": "c1be9d133467a42ab88d7168e408ef5a90257661a901899426214a3d1fc2d026",
    "uvicorn/config.py": "cf91c6ffae283a2b2020da954b4cc4ed47d3246cb062afd09fb77d98e4c5ca87",
    "uvicorn/lifespan/on.py": "8844cf163e921613c33dc75e1b17dae372ca226254ecc2398bb41451ea4170f1",
}
ACTIVE = {"queued", "running", "processing", "transcribing", "summarizing", "pending",
          "working", "sending", "leased"}


class ShutdownPending(RuntimeError):
    """The original process is still draining; never recreate or force-kill it."""


def verify_shutdown_runtime(current: dict, output, container: str) -> None:
    if (current["Config"].get("Cmd") != COMMAND or current["Config"].get("Entrypoint")
            or current.get("HostConfig", {}).get("Init")
            or any(item.startswith("UVICORN_") for item in current["Config"].get("Env", []))):
        raise RuntimeError("The audited Python PID1 shutdown contract changed")
    code = ("import pathlib,hashlib,json,uvicorn;root=pathlib.Path(uvicorn.__file__).parent.parent;"
            "print(json.dumps({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in "
            + repr(sorted(SOURCE_HASHES)) + "}))")
    if json.loads(output("docker", "exec", container, "python", "-c", code)) != SOURCE_HASHES:
        raise RuntimeError("The audited shutdown or inbox recovery source changed")


def require_restartable(snapshot: dict) -> None:
    for tables in snapshot.values():
        for table, states in tables.items():
            for state in ACTIVE:
                if states.get(state, 0) and not (
                    table == "inbox_jobs" and state in {"queued", "running"}
                ):
                    raise RuntimeError("Durable work in unaudited table " + table)


def wait_for_shutdown(inspect, identifier: str, attempts: int = 60) -> dict:
    for attempt in range(attempts):
        current = inspect()
        if current.get("Id") != identifier:
            raise RuntimeError("Container identity changed during graceful shutdown")
        if type(current["State"].get("Running")) is not bool:
            raise RuntimeError("Container running state is unknown during graceful shutdown")
        if not current["State"]["Running"]:
            return current
        if attempt < attempts - 1:
            time.sleep(1)
    raise ShutdownPending("App is still draining; no force kill or replacement performed")


def inbox_snapshot(data: Path, expected: dict | None = None) -> dict:
    result = {}
    for path in data.rglob("*.sqlite3"):
        key = str(path.relative_to(data))
        if expected is not None and key not in expected:
            continue
        with sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_jobs'").fetchone():
                continue
            if expected is None:
                rows = db.execute(
                    "SELECT id,state FROM inbox_jobs WHERE state IN ('queued','running')",
                )
                jobs = dict(rows)
            else:
                jobs = {}
                keys = list(expected[key]["jobs"])
                for offset in range(0, len(keys), 500):
                    batch = keys[offset:offset + 500]
                    jobs.update(db.execute("SELECT id,state FROM inbox_jobs WHERE id IN ("
                                           + ",".join("?" for _ in batch) + ")", batch))
                if jobs.keys() != expected[key]["jobs"].keys():
                    raise RuntimeError("Durable inbox job identities were not preserved")
        result[key] = {"jobs": jobs, "ids_sha256": hashlib.sha256(
            json.dumps(sorted(jobs)).encode()).hexdigest()}
    if expected is not None and result.keys() != expected.keys():
        raise RuntimeError("Durable inbox database was not preserved")
    return result


def merge_inbox_snapshots(before: dict, stopped: dict) -> dict:
    result = {key: {"jobs": dict(value["jobs"])} for key, value in before.items()}
    for key, value in stopped.items():
        result.setdefault(key, {"jobs": {}})["jobs"].update(value["jobs"])
    for value in result.values():
        value["ids_sha256"] = hashlib.sha256(json.dumps(sorted(value["jobs"])).encode()).hexdigest()
    return result
