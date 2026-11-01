"""Compile the actual source-disclosure predicate without launching an iOS app."""

import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[4]
source = (root / "src/ios/DaythreadApp/SourceActionsView.swift").read_text()
start = source.index("static func isRingCopy(")
opening = source.index("{", start)
depth, end = 1, opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]

code = r'''
import Foundation
enum JSONValue: Equatable {
    case object([String: JSONValue]), string(String), number(Double), bool(Bool), null
    subscript(_ key: String) -> JSONValue {
        if case .object(let value) = self { return value[key] ?? .null }
        return .null
    }
    var text: String { if case .string(let value) = self { return value }; return "" }
}
struct SourceProvenanceView { __METHOD__ }
func source(_ metadata: [String:JSONValue], kind: String = "manual_upload") -> JSONValue {
    .object(["source_kind": .string(kind), "source_metadata": .object(metadata)])
}
let valid: [String:JSONValue] = ["provenance_kind": .string("omi_ring"),
    "timestamp_precision_seconds": .number(1), "device_copy_retained": .bool(true)]
precondition(SourceProvenanceView.isRingCopy(source(valid)))
precondition(!SourceProvenanceView.isRingCopy(source(valid, kind: "omi_api")))
precondition(!SourceProvenanceView.isRingCopy(source([:])))
for key in valid.keys {
    var missing = valid; missing.removeValue(forKey: key)
    precondition(!SourceProvenanceView.isRingCopy(source(missing)))
}
var wrong = valid; wrong["timestamp_precision_seconds"] = .string("1")
precondition(!SourceProvenanceView.isRingCopy(source(wrong)))
wrong = valid; wrong["device_copy_retained"] = .bool(false)
precondition(!SourceProvenanceView.isRingCopy(source(wrong)))
print("Ring source provenance predicate passed")
'''.replace("__METHOD__", method)

with tempfile.TemporaryDirectory(prefix="daythread-ring-provenance-") as directory:
    path = Path(directory)
    (path / "main.swift").write_text(code)
    subprocess.run(["swiftc", str(path / "main.swift"), "-o", str(path / "check")], check=True)
    subprocess.run([str(path / "check")], check=True)
