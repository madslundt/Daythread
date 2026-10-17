"""Spoken-language coverage regressions from transcript topic passages."""

import pytest
from fastapi.testclient import TestClient
from test_translation import DANISH, ENGLISH, source, translator

from omi_local.api import create_app
from omi_local.settings import Settings
from omi_local.translation import transcript_in_language

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
    (DANISH * 10 + " " + ENGLISH, "da"),
    (ENGLISH * 10 + " " + DANISH, "en"),
])
def test_context_does_not_hide_a_clear_foreign_sentence(text, language):
    assert not transcript_in_language(text, language)
