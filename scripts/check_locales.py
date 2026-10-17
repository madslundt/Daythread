"""Check source coverage, shared presentation copy, and translation catalog integrity."""

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from localization_server import messages as server_messages
from localization_swift import inventory as swift_inventory
from localization_swift import literals as swift_literals

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/web/src"
IOS = ROOT / "src/ios/DaythreadApp"


def format_tokens(text: str) -> Counter:
    return Counter(re.findall(r"%(?:[0-9]+\$)?(?:\.[0-9]+)?(?:ll|l)?[@difus]", text))


def web_catalogs() -> dict[str, dict[str, str]]:
    catalogs = {
        path.stem: json.loads(path.read_text()) for path in (WEB / "locales").glob("*.json")
    }
    source = catalogs["en"]
    placeholders = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
    for language, catalog in catalogs.items():
        assert set(catalog) == set(source), (
            f"{language}: missing web keys {sorted(set(source) - set(catalog))}; "
            f"extra keys {sorted(set(catalog) - set(source))}"
        )
        assert all(isinstance(value, str) and value for value in catalog.values()), language
        for key, value in catalog.items():
            assert set(placeholders.findall(key)) == set(placeholders.findall(value)), (
                f"{language}: placeholders differ for {key}"
            )
    used = set()
    pattern = re.compile(r'\b(?:t|source)\(("(?:\\.|[^"\\])*")(?=\s*[,\)])')
    for path in (*WEB.glob("*.tsx"), *WEB.glob("*.ts")):
        if path.name.endswith((".test.tsx", ".test.ts")):
            continue
        used.update(json.loads(match.group(1)) for match in pattern.finditer(path.read_text()))
    missing = used - set(source)
    assert not missing, f"Missing web keys: {sorted(missing)}"
    report = subprocess.run(
        ["node", str(ROOT / "scripts/localization_web.mjs"), "--inventory"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    uncovered = [entry for entry in json.loads(report.stdout) if not entry["localized"]]
    assert not uncovered, "Web copy bypasses localization:\n" + "\n".join(
        f"{entry['file']}:{entry['line']}: {entry['key']}" for entry in uncovered
    )
    return catalogs


def ios_catalogs(name: str = "Localizable.strings") -> dict[str, dict[str, str]]:
    catalogs = {}
    entry = re.compile(r'^("(?:\\.|[^"\\])*") = ("(?:\\.|[^"\\])*");$')
    for path in IOS.glob(f"*.lproj/{name}"):
        strings = {}
        for line in path.read_text().splitlines():
            if line.startswith("/*") or not line:
                continue
            match = entry.fullmatch(line)
            assert match, f"Invalid line in {path}: {line}"
            key, value = (json.loads(group) for group in match.groups())
            assert key not in strings, f"Duplicate key: {key}"
            strings[key] = value
        catalogs[path.parent.stem] = strings
    source = catalogs["en"]
    for language, catalog in catalogs.items():
        assert set(catalog) == set(source), (
            f"{language}: missing iOS keys {sorted(set(source) - set(catalog))}; "
            f"extra keys {sorted(set(catalog) - set(source))}"
        )
        assert all(value for value in catalog.values()), language
        for key, value in catalog.items():
            assert format_tokens(key) == format_tokens(value), (
                f"{language}: native format specifiers differ for {key}"
            )
            tokens = r"\{([A-Za-z][A-Za-z0-9_]*)\}"
            assert set(re.findall(tokens, key)) == set(re.findall(tokens, value)), (
                f"{language}: iOS placeholders differ for {key}"
            )
    return catalogs


def native_source_coverage(catalog: dict[str, str]) -> None:
    exemptions = json.loads((ROOT / "config/localization-exemptions.json").read_text())["ios"]
    uncovered = [entry for entry in swift_inventory(ROOT, exemptions) if not entry["localized"]]
    assert not uncovered, "Native copy bypasses localization:\n" + "\n".join(
        f"{entry['file']}:{entry['line']}: {entry['key']}" for entry in uncovered
    )
    used = set()
    for folder in [IOS, ROOT / "src/ios/Sources/OmiCore"]:
        for path in folder.glob("*.swift"):
            if path.name in {"Localization.swift"}:
                continue
            text = path.read_text()
            for entry in swift_literals(text):
                before = text[max(0, entry["start"] - 80) : entry["start"]]
                if re.search(r"(?:L10n|AppLocalization)\.(?:text|source)\(\s*$", before):
                    used.add(entry["key"])
    assert not used - set(catalog), f"Missing native keys: {sorted(used - set(catalog))}"


def shared_source_coverage(web: dict, ios: dict) -> None:
    required = server_messages(ROOT)
    labels = json.loads((ROOT / "config/interface-labels.json").read_text())
    required.update("status." + code for code in labels)
    for name, catalogs in [("web", web), ("iOS", ios)]:
        for code, label in labels.items():
            assert catalogs["en"].get("status." + code) == label, (
                f"{name}: English status label differs for {code}"
            )
        for language, catalog in catalogs.items():
            assert not required - set(catalog), (
                f"{name}/{language}: missing server or status labels: "
                f"{sorted(required - set(catalog))}"
            )


if __name__ == "__main__":
    web = web_catalogs()
    ios = ios_catalogs()
    info = ios_catalogs("InfoPlist.strings")
    native_source_coverage(ios["en"])
    shared_source_coverage(web, ios)
    assert {"en", "da"} <= set(web) and {"en", "da"} <= set(ios)
    assert set(ios) == set(info), "Each iOS locale needs permission descriptions"
    print(
        f"Web: {len(web['en'])} keys; iOS: {len(ios['en'])} UI "
        f"and {len(info['en'])} permission keys. Catalogs match."
    )
