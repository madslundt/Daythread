"""Check that each interface translation catalog has the same keys."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/web/src"
IOS = ROOT / "src/ios/DaythreadApp"


def web_catalogs() -> dict[str, dict[str, str]]:
    catalogs = {
        path.stem: json.loads(path.read_text())
        for path in (WEB / "locales").glob("*.json")
    }
    source = catalogs["en"]
    placeholders = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
    for language, catalog in catalogs.items():
        assert set(catalog) == set(source), f"{language}: missing or extra web keys"
        assert all(isinstance(value, str) and value for value in catalog.values()), language
        for key, value in catalog.items():
            assert set(placeholders.findall(key)) == set(placeholders.findall(value)), (
                f"{language}: placeholders differ for {key}"
            )
    used = set()
    pattern = re.compile(r'\bt\(("(?:\\.|[^"\\])*")(?=\s*[,\)])')
    for path in (*WEB.glob("*.tsx"), *WEB.glob("*.ts")):
        if path.name.endswith((".test.tsx", ".test.ts")):
            continue
        used.update(json.loads(match.group(1)) for match in pattern.finditer(path.read_text()))
    missing = used - set(source)
    assert not missing, f"Missing web keys: {sorted(missing)}"
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
        assert set(catalog) == set(source), f"{language}: missing or extra iOS keys"
        assert all(value for value in catalog.values()), language
    return catalogs


if __name__ == "__main__":
    web = web_catalogs()
    ios = ios_catalogs()
    info = ios_catalogs("InfoPlist.strings")
    assert {"en", "da"} <= set(web) and {"en", "da"} <= set(ios)
    assert set(ios) == set(info), "Each iOS locale needs permission descriptions"
    print(
        f"Web: {len(web['en'])} keys; iOS: {len(ios['en'])} UI "
        f"and {len(info['en'])} permission keys. Catalogs match."
    )
