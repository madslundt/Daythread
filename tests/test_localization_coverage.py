"""Keep source coverage aware of interpolation, comments, and protocol values."""

from scripts.localization_swift import inventory, literals


def test_swift_interpolation_handles_nested_strings_and_preserves_source_keys():
    source = r"""// Text("Not interface copy")
    Text(L10n.text("Saved \(record["title"].text) · \(count) recordings"))
    let expression = #"^[A-Z][a-z]+$"#
    """
    found = literals(source)
    assert any(item["key"] == "Saved {value0} · {value1} recordings" for item in found)
    message = next(item for item in found if item["key"].startswith("Saved"))
    assert message["expressions"] == ['record["title"].text', "count"]
    assert not any(item["key"] == "Not interface copy" for item in found)


def test_native_coverage_detects_unwrapped_copy_and_allows_protocol_exemptions(tmp_path):
    folder = tmp_path / "src/ios/DaythreadApp"
    folder.mkdir(parents=True)
    (folder / "Example.swift").write_text("""
    Text("Missing translation")
    Label("waiting", systemImage: "clock")
    Text(L10n.text("Saved recordings"))
    let header = "Content-Type"
    if kind == "User action" { }
    """)
    report = inventory(tmp_path, {"Content-Type": "HTTP header"})
    assert {item["key"] for item in report if not item["localized"]} == {
        "Missing translation",
        "waiting",
    }
    assert {item["key"] for item in report if item["localized"]} == {"Saved recordings"}
