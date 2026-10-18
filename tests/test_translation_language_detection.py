"""Spoken-language coverage regressions from transcript topic passages."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_translation import DANISH, ENGLISH, source, translator

from omi_local.api import create_app
from omi_local.settings import Settings
from omi_local.translation import (
    analyze_transcript_languages,
    language_profiles,
    recommended_translation_language,
    transcript_in_language,
)

SPOKEN_PASSAGES = [
    (
        "cocoa", "da",
        (
            'Okay det her det er en øh test hvor vi skal snakke om øh kakao. Så jeg har købt en '
            'jeg ved faktisk ikke lige helt du husk hvad den hedder. Det er ikke sådan en SEW men '
            'det er sådan en mælkeskummer der kan tage kakao og så kan det øh så tager jeg også '
            'noget mørk chokolade og så kommer det ind og så kan det ligesom stå der og skumme op '
            'og så kan jeg drikke det. Og lige nu så prøver jeg det med chili ned og det kan ikke '
            'anbefales '
        ).strip(),
    ),
    (
        "recorder", "da",
        (
            'I det andet jeg gerne vil til op det er at jeg har bestilt sådan en OMI som sørger '
            'for at optage lyd hele dagen når jeg er på arbejde når jeg er hjemme og så videre og '
            'derved så kan jeg have en app som kan give mig et recap af dagen. Det kan give mig '
            'recap summary af et møde eller andre ting men det kan også lave rewind så hvad skete '
            'der for 10 minutter siden hvad skete der før mødetiden og så videre '
        ).strip(),
    ),
    (
        "roadster", "en",
        (
            "Let's turn it to English and talk about Teslaen they announce the new roadster which "
            'seems a bit crazy at first because its topspeed is 600 kilometers an hour and it can '
            "fly it can it can drive on a wall it can drive on the roof it can yeah it's it's it's "
            'pretty amazing and zero to 60 miles per hour Approximately 100 kilometers per hour is '
            "less than one second. So that's pretty insane. Øh ja så I tænkte that was it. Then we "
            "had these subjects. Let's see how it goes "
        ).strip(),
    ),
]


@pytest.mark.parametrize("name, language, text", SPOKEN_PASSAGES,
                         ids=[item[0] for item in SPOKEN_PASSAGES])
@pytest.mark.parametrize("scoped", [False, True])
def test_spoken_passages_hide_the_language_already_spoken(tmp_path, name, language, text, scoped):
    settings = Settings(data_dir=tmp_path, translation_languages=["en", "da"],
                        worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        prefix = (ENGLISH if language == "da" else DANISH) + " 🚗 " if scoped else ""
        key, path = source(client, prefix + text)
        params = {}
        if scoped:
            version = client.app.state.memory.store.get_audio(key)["transcript_version"]
            params = {"start_offset": len(prefix), "end_offset": len(prefix + text),
                      "transcript_version": version}
        calls = translator(client)
        result = client.get(path, params=params)
        assert result.status_code == 200
        assert result.json()["redundant_languages"] == [language], name
        assert calls == []
        same_language = client.post(path, params=params | {"language": language})
        assert same_language.status_code == 200
        assert same_language.json()["already_in_target_language"] is True
        assert calls == []


@pytest.mark.parametrize("text, language", [
    (DANISH * 3 + " " + ENGLISH, "da"),
    (ENGLISH * 10 + " " + DANISH, "en"),
])
def test_context_does_not_hide_a_clear_foreign_sentence(text, language):
    assert not transcript_in_language(text, language)


@pytest.mark.parametrize("text, dominant", [
    (DANISH, "da"), (ENGLISH, "en"),
    (DANISH * 3 + " " + ENGLISH, "da"),
    (ENGLISH * 3 + " " + DANISH, "en"),
])
def test_analysis_finds_dominant_spoken_language(text, dominant):
    analysis = analyze_transcript_languages(text)
    assert analysis["dominant_language"] == dominant
    assert analysis["dominant_language_share"] == analysis["language_shares"][dominant]
    if " " + ENGLISH in text or " " + DANISH in text:
        assert set(analysis["language_shares"]) == {"da", "en"}
        assert 0.5 < analysis["dominant_language_share"] < 0.9
    else:
        assert analysis["dominant_language_share"] == 1
    assert sum(analysis["language_shares"].values()) == pytest.approx(1)


@pytest.mark.parametrize("danish_parts, english_parts, share, redundant", [
    (9, 1, 0.9, True), (8, 1, 8 / 9, False), (10, 1, 10 / 11, True),
])
def test_letter_coverage_uses_exact_ninety_percent_boundary(
    monkeypatch, danish_parts, english_parts, share, redundant,
):
    # Equal-length sentences isolate coverage arithmetic from detector probabilities.
    class Detector:
        def append(self, text):
            self.language = "da" if "dansk" in text else "en"

        def get_probabilities(self):
            return [SimpleNamespace(lang=self.language, prob=1)]

    monkeypatch.setattr("omi_local.translation.language_profiles", lambda: SimpleNamespace(
        create=Detector,
    ))
    text = "\n".join(["dansk taler meget."] * danish_parts
                     + ["words speak today."] * english_parts)
    analysis = analyze_transcript_languages(text)
    assert analysis["dominant_language"] == "da"
    assert analysis["dominant_language_share"] == share
    assert transcript_in_language(text, "da-DK") is redundant
    assert recommended_translation_language(analysis, ["en", "da"]) == (
        None if redundant else "da"
    )


@pytest.mark.parametrize("text", ["", "123?!", "John James Smith", "qzxv prstq"])
def test_uncertain_text_has_no_dominant_language_or_translation_recommendation(text):
    analysis = analyze_transcript_languages(text)
    assert analysis == {"dominant_language": None, "dominant_language_share": 0,
                        "language_shares": {}}
    assert recommended_translation_language(analysis, ["en", "da"]) is None


def test_unknown_letters_do_not_create_a_second_detected_language():
    analysis = analyze_transcript_languages(DANISH + "\nJohn James Smith")
    assert analysis["dominant_language"] == "da"
    assert analysis["dominant_language_share"] < 0.9
    assert set(analysis["language_shares"]) == {"da"}
    assert recommended_translation_language(analysis, ["en", "da"]) is None


def test_small_known_fragments_amid_unknown_text_do_not_recommend_translation():
    text = DANISH + "\n" + ENGLISH + "\n" + "John James Smith\n" * 100
    analysis = analyze_transcript_languages(text)
    assert set(analysis["language_shares"]) == {"da", "en"}
    assert sum(analysis["language_shares"].values()) < 0.2
    assert recommended_translation_language(analysis, ["en", "da"]) is None


@pytest.mark.parametrize("reverse", [False, True])
def test_long_varied_recordings_sample_languages_across_the_whole_recording(monkeypatch, reverse):
    factory, calls = language_profiles(), []

    def create():
        calls.append(None)
        return factory.create()

    monkeypatch.setattr("omi_local.translation.language_profiles", lambda: SimpleNamespace(
        create=create,
    ))
    # Hundreds of unique paragraph contexts used to exhaust the 256-classification
    # budget before reaching the English portion at the end of the recording.
    danish = "\n".join(DANISH + " Det gælder opgaven " + chr(97 + index // 26)
                       + chr(97 + index % 26) + "." for index in range(600))
    english = (ENGLISH + "\n") * 600
    text = english + danish if reverse else danish + english
    analysis = analyze_transcript_languages(text)
    assert analysis["dominant_language"] == "da"
    assert {"da", "en"} <= set(analysis["language_shares"])
    expected = sum(map(str.isalpha, danish)) / sum(map(str.isalpha, text))
    assert analysis["dominant_language_share"] == pytest.approx(expected, abs=0.08)
    assert recommended_translation_language(analysis, ["en", "da"]) == "da"
    assert 0 < len(calls) <= 256


@pytest.mark.parametrize("languages, default, recommended", [
    (["en", "da"], "da", "da"), (["en", "da-dk"], "da-dk", "da-dk"),
    (["en", "sv"], "en", None), ([], "", None),
])
def test_mixed_transcript_recommends_only_configured_dominant_target(
    tmp_path, languages, default, recommended,
):
    settings = Settings(data_dir=tmp_path, translation_languages=languages,
                        worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        text = DANISH * 3 + "\n" + ENGLISH
        key, path = source(client, text)
        calls = translator(client)
        result = client.get(path).json()
        assert result["language"] == default
        assert result["recommended_language"] == recommended
        assert result["available_languages"] == languages
        assert result["language_analysis"]["dominant_language"] == "da"
        assert result["language_analysis"]["dominant_language_share"] < 0.9
        assert calls == []
        assert client.get(f"/v1/transcripts/{key}").json()["text"] == text
        if languages:
            explicit = client.get(path, params={"language": "en"}).json()
            assert explicit["language"] == "en"
        if recommended:
            generated = client.post(path).json()
            assert generated["recommended_language"] == recommended
            assert generated["language_analysis"] == result["language_analysis"]
            assert calls == [(text, recommended)]


def test_scoped_language_analysis_uses_selected_passage(tmp_path):
    settings = Settings(data_dir=tmp_path, translation_languages=["en", "da"],
                        worker_enabled=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        prefix = ENGLISH * 10 + "\n"
        selected = DANISH * 3 + "\n" + ENGLISH
        key, path = source(client, prefix + selected)
        version = client.app.state.memory.store.get_audio(key)["transcript_version"]
        result = client.get(path, params={"start_offset": len(prefix),
                                          "end_offset": len(prefix + selected),
                                          "transcript_version": version}).json()
        assert result["language_analysis"] == analyze_transcript_languages(selected)
        assert result["recommended_language"] == "da"
