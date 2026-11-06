"""A short Danish conversation must not inherit a longer English meeting's language."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omi_local.inference import InferenceError
from omi_local.recap_languages import normalize_source_languages

ENGLISH = "We discussed the release schedule and agreed to review the application next week. "
DANISH = "Vi talte om markedet og aftalte at undersøge kapaciteten før næste møde. "


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Translator:
    settings = SimpleNamespace(summary_language="auto")

    def __init__(self):
        self.calls = []
        self.requests = 0

    async def translate(self, text, language):
        raise AssertionError("Do not translate isolated punctuation/citation fragments")

    def for_task(self, task):
        return self

    async def generate_json(self, task, evidence):
        payload = json.loads(evidence)
        self.requests += 1
        assert payload["language"] == "da"
        replacements = {
            "Tablet market and capacity": "Tabletmarked og kapacitet",
            "Market strategy": "Markedsstrategi",
            "Capacity planning": "Kapacitetsplanlægning",
            "We discussed the product market and agreed to review our strategy.":
                "Vi drøftede produktmarkedet og aftalte at gennemgå vores strategi.",
            "We discussed the available capacity and agreed to review our production plans.":
                "Vi drøftede den tilgængelige kapacitet og aftalte "
                "at gennemgå produktionsplanerne.",
        }
        output = []
        for block in payload["blocks"]:
            text = block["text"]
            self.calls.append((text, "da"))
            for before, after in replacements.items():
                text = text.replace(before, after)
            output.append({"id": block["id"], "text": text})
        return json.dumps({"blocks": output})


@pytest.mark.anyio
async def test_auto_uses_each_topics_cited_originals_and_preserves_citations_and_hierarchy():
    english, danish = str(uuid4()), str(uuid4())
    sources = [{"id": english, "text": ENGLISH * 80}, {"id": danish, "text": DANISH * 4}]
    meeting = f"## Analysis tool review\nThe release is planned for next week. [{english}]\n\n"
    summary = (meeting + "## Tablet market and capacity\n\n### Market strategy\n"
               f"We discussed the product market and agreed to review our strategy. [{danish}]\n\n"
               "### Capacity planning\n"
               "We discussed the available capacity and agreed to review our production plans. "
               f"[{danish}]")
    inference = Translator()
    result, trace = await normalize_source_languages(inference, summary, sources)
    assert result.startswith(meeting)
    assert "## Tabletmarked og kapacitet" in result
    assert "### Markedsstrategi" in result and "### Kapacitetsplanlægning" in result
    assert "Vi drøftede produktmarkedet" in result
    assert result.count(f"[{danish}]") == 2 and result.count(f"[{english}]") == 1
    assert len(inference.calls) == 5
    assert inference.requests == trace["model_calls"] == 1
    assert len(trace["translated_sections"]) == 3


@pytest.mark.anyio
async def test_explicit_summary_language_does_not_trigger_auto_corrections():
    inference = Translator()
    inference.settings = SimpleNamespace(summary_language="en")
    summary = "## Market strategy\nAn English summary."
    result, trace = await normalize_source_languages(
        inference, summary, [{"id": str(uuid4()), "text": DANISH}],
    )
    assert result == summary and not inference.calls
    assert trace["translated_sections"] == []


@pytest.mark.anyio
async def test_auto_never_uses_an_uncited_danish_recording_to_translate_an_english_topic():
    key = str(uuid4())
    summary = f"## Analysis tool review\nThe release is planned for next week. [{key}]"
    inference = Translator()
    result, _ = await normalize_source_languages(inference, summary, [
        {"id": key, "text": ENGLISH}, {"id": str(uuid4()), "text": DANISH * 100},
    ])
    assert result == summary and not inference.calls


@pytest.mark.anyio
async def test_one_parent_can_contain_topics_from_different_source_languages():
    english, danish = str(uuid4()), str(uuid4())
    english_part = ("## Analysis tool review\n\n### Release details\n"
                    f"The release is planned for next week. [{english}]\n\n")
    text = (english_part + "### Market strategy\n"
            f"We discussed the product market and agreed to review our strategy. [{danish}]")
    inference = Translator()
    result, trace = await normalize_source_languages(inference, text, [
        {"id": english, "text": ENGLISH * 80}, {"id": danish, "text": DANISH * 4},
    ])
    assert result.startswith(english_part)
    assert "### Markedsstrategi" in result and "Vi drøftede produktmarkedet" in result
    assert len(trace["translated_sections"]) == 1


@pytest.mark.anyio
async def test_auto_rejects_translation_request_metadata_in_generated_headings():
    class EchoTranslator(Translator):
        async def translate(self, text, language):
            return json.dumps({"task": DANISH * 10, "evidence": "Tabletmarked og kapacitet"})

        def for_task(self, task):
            return self

        async def generate_json(self, task, evidence):
            rows = json.loads(evidence)["blocks"]
            return json.dumps({"blocks": [
                {"id": row["id"], "text": json.dumps({
                    "task": DANISH * 10, "evidence": "Tabletmarked og kapacitet",
                })} for row in rows
            ]})

    key = str(uuid4())
    text = ("## Tablet market and capacity\n"
            f"We discussed the product market and agreed to review our strategy. [{key}]")
    with pytest.raises(InferenceError):
        await normalize_source_languages(EchoTranslator(), text, [{"id": key, "text": DANISH * 4}])


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["missing", "duplicate", "literal", "newline", "language"])
async def test_structured_translation_rejects_bad_blocks_with_one_bounded_repair(fault):
    class InvalidTranslator(Translator):
        async def generate_json(self, task, evidence):
            value = json.loads(await super().generate_json(task, evidence))
            if fault == "missing":
                value["blocks"].pop()
            elif fault == "duplicate":
                value["blocks"][1]["id"] = value["blocks"][0]["id"]
            elif fault == "literal":
                value["blocks"][1]["text"] = "Dansk tekst uden den krævede kilde."
            elif fault == "newline":
                value["blocks"][0]["text"] += "\n## Ekstra emne"
            else:
                value["blocks"][1]["text"] = json.loads(evidence)["blocks"][1]["text"]
            return json.dumps(value)

    key = str(uuid4())
    summary = ("## Market strategy\n"
               f"We discussed the product market and agreed to review our strategy. [{key}]")
    model = InvalidTranslator()
    with pytest.raises(InferenceError):
        await normalize_source_languages(model, summary, [{"id": key, "text": DANISH * 4}])
    assert model.requests == 2


@pytest.mark.anyio
async def test_translate_complete_paragraphs_instead_of_punctuation_between_citations():
    key = str(uuid4())
    summary = ("## Market strategy\n"
               f"We discussed the product market and agreed to review our strategy. [{key}]. "
               "We discussed the available capacity and agreed to review our production plans. "
               f"[{key}].")
    model = Translator()
    result, _ = await normalize_source_languages(model, summary, [{"id": key, "text": DANISH * 4}])
    assert len(model.calls) == 2  # One heading and one complete paragraph in the same request.
    assert f"[{key}]. Vi drøftede" in result and result.endswith(f"[{key}].")
    assert all(str(key) not in text and text != "." for text, _language in model.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("parent", [False, True])
async def test_translation_rejects_an_english_heading_even_when_its_prose_is_danish(parent):
    class UntranslatedHeading(Translator):
        async def generate_json(self, task, evidence):
            result = json.loads(await super().generate_json(task, evidence))
            result["blocks"][0]["text"] = "Tablet market and capacity"
            return json.dumps(result)

    key = str(uuid4())
    text = ("## Tablet market and capacity\n" + ("\n### Market strategy\n" if parent else "")
            + f"We discussed the product market and agreed to review our strategy. [{key}]")
    model = UntranslatedHeading()
    with pytest.raises(InferenceError):
        await normalize_source_languages(model, text, [{"id": key, "text": DANISH * 4}])
    assert model.requests == 2


@pytest.mark.anyio
@pytest.mark.parametrize("heading", ["NNCD.SAR", "Microsoft Azure", "Tabletmarked og kapacitet"])
async def test_translation_allows_proper_names_and_ambiguous_short_danish_headings(heading):
    key = str(uuid4())
    summary = (f"## {heading}\n"
               f"We discussed the product market and agreed to review our strategy. [{key}]")
    model = Translator()
    result, trace = await normalize_source_languages(model, summary, [
        {"id": key, "text": DANISH * 4},
    ])
    assert result.startswith(f"## {heading}\n")
    assert "Vi drøftede produktmarkedet" in result
    assert trace["model_calls"] == model.requests == 1


@pytest.mark.anyio
async def test_json_generation_avoids_provider_schema_when_both_routes_are_available():
    class JsonCompatibleTranslator(Translator):
        async def generate_structured_json(self, task, evidence, schema):
            pytest.fail("Do not invoke provider grammar when JSON generation is available")

    key = str(uuid4())
    summary = ("## Market strategy\n"
               f"We discussed the product market and agreed to review our strategy. [{key}]")
    model = JsonCompatibleTranslator()
    result, trace = await normalize_source_languages(model, summary, [
        {"id": key, "text": DANISH * 4},
    ])
    assert "###" not in result and "## Markedsstrategi" in result
    assert trace["model_calls"] == model.requests == 1
