"""Physical regression: leave Omi on its charger and launch Daythread before running.

Run with: python3 src/ios/Tests/Lifecycle/check_device_charging.py --device <UDID>
Reads capture metadata only; does not download recorded audio.
"""

import argparse
import json
import plistlib
import subprocess
import tempfile
import time
from pathlib import Path


def snapshot(device: str, directory: Path) -> tuple[bool, list[tuple], dict[str, int]]:
    def receive(source: str, filename: str) -> Path:
        destination = directory / filename
        subprocess.run(
            [
                "xcrun",
                "devicectl",
                "device",
                "copy",
                "from",
                "--device",
                device,
                "--domain-type",
                "appDataContainer",
                "--domain-identifier",
                "com.daythread.app",
                "--source",
                source,
                "--destination",
                str(destination),
                "--quiet",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return destination

    preferences = plistlib.loads(
        receive("Library/Preferences/com.daythread.app.plist", "preferences.plist").read_bytes()
    )
    index = json.loads(
        receive("Library/Application Support/Capture/index.json", "index.json").read_text()
    )
    audio = sorted(
        (item["id"], item["capturedAt"], item.get("endedAt"))
        for item in index
        if item["kind"] == "audio"
    )
    assert preferences.get("omiPauseWhileCharging", True), "Charging stop option is disabled"
    assert preferences.get("recordingSchedule.v1"), "No saved recording policy to verify"
    schedule = json.loads(preferences["recordingSchedule.v1"])
    source = schedule["manualSource"] if schedule["manualOverride"] else schedule["source"]
    assert source == "omi" and schedule["enabled"], (
        "Start Omi capture before checking charging automation"
    )

    def info(kind: str, *extra: str) -> dict:
        result = subprocess.run(
            [
                "xcrun",
                "devicectl",
                "device",
                "info",
                kind,
                "--device",
                device,
                *extra,
                "--json-output",
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)["result"]

    processes = info("processes")["runningProcesses"]
    assert any(
        process.get("executable", "").endswith("/Daythread.app/Daythread") for process in processes
    ), "Daythread is not running"
    files = info(
        "files",
        "--domain-type",
        "appDataContainer",
        "--domain-identifier",
        "com.daythread.app",
        "--subdirectory",
        "Library/Application Support/Capture",
    )["files"]
    sizes = {
        file["relativePath"]: file["metadata"]["size"]
        for file in files
        if file["name"].endswith((".frames", ".wav"))
    }
    return preferences.get("omiChargingPaused", False), audio, sizes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--seconds", type=float, default=8)
    args = parser.parse_args()
    assert args.seconds >= 2, "Observe for at least two seconds"
    with tempfile.TemporaryDirectory(prefix="daythread-charging-check-") as temporary:
        directory = Path(temporary)
        held, before, before_sizes = snapshot(args.device, directory)
        assert held, "Omi is on the charger but the charging hold is inactive"
        time.sleep(args.seconds)
        held, after, after_sizes = snapshot(args.device, directory)
        assert held, "Charging hold was released while Omi remained on the charger"
        # Upload acknowledgments may prune old entries; only new/extended audio is a failure.
        previous = {item[0]: item[1:] for item in before}
        assert all(
            name in before_sizes and size == before_sizes[name]
            for name, size in after_sizes.items()
        ), "Omi audio files grew while charging"
        assert all(item[0] in previous and item[1:] == previous[item[0]] for item in after), (
            "Omi continued saving audio while charging"
        )
    print("PASS: charging stop active; no new or extended audio during observation")


if __name__ == "__main__":
    main()
