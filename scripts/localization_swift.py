"""Lex Swift literals and interpolation without treating nested expressions as prose."""

import re
from pathlib import Path


def decode_segment(text: str) -> str:
    escapes = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", '"': '"', "\\": "\\"}
    return re.sub(
        r'\\(u\{[0-9a-fA-F]+\}|[nrt0"\\])',
        lambda m: chr(int(m[1][2:-1], 16)) if m[1].startswith("u{") else escapes[m[1]],
        text,
    )


def literals(source: str) -> list[dict]:
    result = []
    size = len(source)

    def code(index: int, closing: str | None = None) -> int:
        while index < size:
            if closing and source[index] == closing:
                return index + 1
            if source.startswith("//", index):
                end = source.find("\n", index)
                index = size if end < 0 else end + 1
            elif source.startswith("/*", index):
                depth = 1
                index += 2
                while index < size and depth:
                    if source.startswith("/*", index):
                        depth += 1
                        index += 2
                    elif source.startswith("*/", index):
                        depth -= 1
                        index += 2
                    else:
                        index += 1
            elif source[index] == '"' or (
                source[index] == "#" and re.match(r'#+"', source[index:])
            ):
                index = string(index)
            elif source[index] in "([{":
                index = code(index + 1, {"(": ")", "[": "]", "{": "}"}[source[index]])
            else:
                index += 1
        return index

    def string(start: int) -> int:
        index = start
        while source[index] == "#":
            index += 1
        hashes = source[start:index]
        delimiter = '"""' if source.startswith('"""', index) else '"'
        index += len(delimiter)
        end_marker = delimiter + hashes
        escape = "\\" + hashes
        key, segment, expressions = "", "", []
        while index < size:
            if source.startswith(end_marker, index):
                key += segment if hashes else decode_segment(segment)
                end = index + len(end_marker)
                result.append(
                    {
                        "start": start,
                        "end": end,
                        "raw": source[start:end],
                        "key": key,
                        "expressions": expressions,
                        "raw_string": bool(hashes),
                        "line": source.count("\n", 0, start) + 1,
                    }
                )
                return end
            if source.startswith(escape + "(", index):
                key += segment if hashes else decode_segment(segment)
                segment = ""
                expression_start = index + len(escape) + 1
                expression_end = code(expression_start, ")")
                key += "{value" + str(len(expressions)) + "}"
                expressions.append(source[expression_start : expression_end - 1])
                index = expression_end
            elif source.startswith(escape, index):
                # Escaped delimiters and backslashes cannot end a literal.
                segment += source[index : index + len(escape) + 1]
                index += len(escape) + 1
            else:
                segment += source[index]
                index += 1
        return size

    code(0)
    return sorted(result, key=lambda item: item["start"])


def natural_text(text: str) -> bool:
    return bool(
        re.search(r"[A-Za-z]", text)
        and (
            re.search(r"[A-Za-z] [A-Za-z{]| [a-z]+|[A-Za-z][!?…]$|[a-zA-Z]\.\s*$", text)
            or re.search(r"\b[A-Z][a-z]{2,}\b", text)
        )
    )


def inventory(root: Path, exemptions: dict) -> list[dict]:
    result = []
    paths = sorted((root / "src/ios/DaythreadApp").glob("*.swift"))
    paths += sorted((root / "src/ios/Sources/OmiCore").glob("*.swift"))
    for path in paths:
        if path.name in {"Localization.swift"}:
            continue
        source = path.read_text()
        relative = str(path.relative_to(root))
        for item in literals(source):
            key = item["key"]
            before = source[max(0, item["start"] - 120) : item["start"]]
            display_literal = bool(
                re.search(
                    r"(?:Text|Button|Label|Section|TextField|SecureField|Toggle|Picker|"
                    r"ProgressView|LabeledContent|navigationTitle|accessibilityLabel)\(\s*$",
                    before,
                )
            )
            if not natural_text(key) and not (
                display_literal and re.search(r"[A-Za-z]", re.sub(r"\{\w+\}", "", key))
            ):
                continue
            after = source[item["end"] : item["end"] + 20]
            if re.search(r"(?:==|!=)\s*$", before) or re.match(r"\s*(?:==|!=)", after):
                continue  # Protocol comparisons.
            if re.match(r"\s*:", after) and before.rstrip().endswith(("[", ",")):
                continue  # Dictionary keys.
            if exemptions.get(key) or exemptions.get(relative + "::" + key):
                continue
            localized = bool(re.search(r"(?:L10n|AppLocalization)\.(?:text|source)\(\s*$", before))
            if re.search(r"String\(localized:\s*$", before):
                localized = True
            result.append({**item, "file": relative, "localized": localized})
    return result
