"""Per-installation interface language changes presentation, never generated answers."""

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from omi_local.api import create_app
from omi_local.notifications import NotificationService
from omi_local.postgres import PostgresConnection, _statements
from omi_local.settings import Settings
from omi_local.store import Store
from omi_local.wearable_actions import wearable_notice

TOKEN = "ab" * 32
NOUNS = {"en": ("question", "questions"), "da": ("spørgsmål", "spørgsmål"),
         "de": ("Frage", "Fragen"), "es": ("pregunta", "preguntas"),
         "fr": ("question", "questions"), "nb": ("spørsmål", "spørsmål"),
         "sv": ("fråga", "frågor")}
EMPTY = {"en": "No question found", "da": "Intet spørgsmål fundet",
         "de": "Keine Frage gefunden", "es": "No se encontró ninguna pregunta",
         "fr": "Aucune question trouvée", "nb": "Ingen spørsmål funnet",
         "sv": "Ingen fråga hittades"}
ANSWER = "Keep literal 2 + 2 = 4 * 1.\nI asked 3 questions.\n**Do not translate this answer.**"


def publish(service, count, *, metadata=False, answer=ANSWER):
    title, body = wearable_notice({"action": "answer", "question_count": count,
                                   "question": "Recent audio summary" if count else None,
                                   "answer": answer, "text": answer})
    target = {"job_id": str(uuid4()), "kind": "wearable_action"}
    if metadata:
        target.update(action="answer", question_count=count)
    return service.publish("wearable_action", title, body, target, str(uuid4()))


@pytest.mark.parametrize("language", list(NOUNS))
@pytest.mark.parametrize("count", [0, 1, 3])
def test_apns_count_uses_installation_language_and_preserves_question_answer(
    tmp_path, language, count,
):
    async def run():
        service = NotificationService(Store(tmp_path), Settings(data_dir=tmp_path, _env_file=None))
        service.store.register_notification_device("phone", TOKEN, "sandbox", False)
        canonical = publish(service, count)
        delivery = service.store.pending_notifications()[0] | {"interface_language": language}
        payloads = []

        def receive(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200)

        service.sender.authorization = lambda: "test-token"
        await service.sender.client.aclose()
        service.sender.client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
        try:
            assert await service.sender.send(delivery, "default") == (200, "")
            event = payloads[0]["notification"]
            expected_title = "Recent audio summary" if count else EMPTY[language]
            noun = NOUNS[language][count != 1]
            assert event["title"] == expected_title
            assert event["body"] == f"{count} {noun}\n{ANSWER}"
            assert payloads[0]["aps"]["alert"] == {
                "title": event["title"], "body": event["body"],
            }
            assert service.store.notifications_after(0)["notifications"] == [canonical]
        finally:
            await service.close()

    asyncio.run(run())


@pytest.mark.parametrize("language", list(NOUNS))
def test_registration_and_feed_use_app_language_without_rewriting_stored_events(tmp_path, language):
    config = Settings(data_dir=tmp_path, worker_enabled=False,
                      background_processing_enabled=False, _env_file=None)
    with TestClient(create_app(config)) as client:
        path = "/v1/notifications/devices/phone"
        registration = {"token": TOKEN, "environment": "sandbox", "active": False,
                        "interface_language": language}
        assert client.put(path, json=registration).status_code == 200
        service = client.app.state.profiles.runtimes["default"].memory.notifications
        canonical = publish(service, 3, metadata=True)
        delivery = service.store.pending_notifications()[0]
        assert delivery["interface_language"] == language
        response = client.get("/v1/notifications", params={"interface_language": language})
        assert response.status_code == 200
        event = response.json()["notifications"][0]
        assert event["body"] == f"3 {NOUNS[language][1]}\n{ANSWER}"
        assert event["title"] == canonical["title"]
        assert event["target"] == canonical["target"] and event["id"] == canonical["id"]
        assert client.get("/v1/notifications").json()["notifications"] == [canonical]
        # Preference changes on the same installation take effect without a new token.
        registration["interface_language"] = "da"
        assert client.put(path, json=registration).status_code == 200
        assert service.store.pending_notifications()[0]["interface_language"] == "da"
        assert client.put(path, json=registration | {
            "interface_language": "system",
        }).status_code == 422
        assert client.get("/v1/notifications?interface_language=xx").status_code == 422


def test_legacy_four_column_registration_migrates_and_language_cleanup_survives_restart(tmp_path):
    # Simulate the exact table used before interface-language registration existed.
    with sqlite3.connect(tmp_path / "omi.sqlite3") as db:
        db.execute("CREATE TABLE notification_devices (id TEXT PRIMARY KEY,token TEXT NOT NULL "
                   "UNIQUE,environment TEXT NOT NULL,active_until REAL NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO notification_devices VALUES(?,?,?,?)", ("old", TOKEN, "sandbox", 0))
    store = Store(tmp_path)
    store.publish_notification("wearable_action", "Question?", "3 questions\nAnswer", {}, "one")
    assert store.pending_notifications()[0]["interface_language"] == "en"
    store.register_notification_device("old", TOKEN, "sandbox", False, interface_language="da")
    reopened = Store(tmp_path)
    assert reopened.pending_notifications()[0]["interface_language"] == "da"
    reopened.unregister_notification_device("old")
    with reopened.connect() as db:
        assert not db.execute("SELECT * FROM notification_device_languages").fetchall()


def test_other_notifications_and_uncounted_generated_body_are_not_translated(tmp_path):
    store = Store(tmp_path)
    canonical = store.publish_notification("recap", "No question found", "3 questions\nAnswer",
                                           {}, "one")
    other = store.publish_notification("wearable_action", "Recent audio summary", ANSWER,
                                       {"action": "answer"}, "two")
    assert store.notifications_after(0, interface_language="da")["notifications"] == [
        canonical, other,
    ]


def test_one_canonical_event_delivers_in_each_installations_language(tmp_path):
    async def run():
        service = NotificationService(Store(tmp_path), Settings(data_dir=tmp_path, _env_file=None))
        for identity, token, language in (("danish", TOKEN, "da"), ("english", "cd" * 32, "en")):
            service.store.register_notification_device(identity, token, "sandbox", False,
                                                       interface_language=language)
        canonical = publish(service, 3, metadata=True)
        payloads = {}

        def receive(request):
            payload = json.loads(request.content)
            payloads[payload["device_id"]] = payload
            return httpx.Response(200)

        service.sender.authorization = lambda: "test-token"
        await service.sender.client.aclose()
        service.sender.client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
        try:
            deliveries = service.store.pending_notifications()
            assert len(deliveries) == 2
            await asyncio.gather(*(
                service.sender.send(delivery, "default") for delivery in deliveries
            ))
            for identity, noun in (("danish", "spørgsmål"), ("english", "questions")):
                payload = payloads[identity]
                event = payload["notification"]
                assert event["title"] == canonical["title"] == "Recent audio summary"
                assert event["body"] == f"3 {noun}\n{ANSWER}"
                assert event == service.store.notifications_after(
                    0, interface_language="da" if identity == "danish" else "en",
                )["notifications"][0]
                assert payload["aps"]["alert"] == {"title": event["title"], "body": event["body"]}
            assert service.store.notifications_after(0)["notifications"] == [canonical]
        finally:
            await service.close()

    asyncio.run(run())


@pytest.mark.parametrize("language", list(NOUNS))
def test_marked_activity_copy_matches_catalog_and_generated_body_stays_literal(tmp_path, language):
    from omi_local.notification_localization import NO_QUESTION_BODY

    catalog = json.loads((Path(__file__).parents[1] / "src/web/src/locales"
                          / (language + ".json")).read_text())
    store = Store(tmp_path)
    for action, title in (("summarize", "Recent audio summary"),
                          ("translate", "Recent audio translation")):
        store.publish_notification("wearable_action", title, ANSWER,
                                   {"action": action}, action)
    store.publish_notification("wearable_action", "No question found",
                               "0 questions\n" + NO_QUESTION_BODY["en"],
                               {"action": "answer", "question_count": 0}, "empty")
    page = store.notifications_after(0, interface_language=language)["notifications"]
    assert page[0]["title"] == catalog["Recent audio summary"] and page[0]["body"] == ANSWER
    assert page[1]["title"] == catalog["Recent audio translation"] and page[1]["body"] == ANSWER
    assert page[2]["title"] == EMPTY[language]
    assert page[2]["body"] == "0 " + NOUNS[language][1] + "\n" + NO_QUESTION_BODY[language]


def test_language_migration_uses_existing_postgres_adapter_without_altering_device_columns():
    from omi_local.notification_store import SCHEMA

    statements = []
    native = SimpleNamespace(execute=lambda sql, *_: statements.append(sql))
    adapter = PostgresConnection(SimpleNamespace(tables={}, lock_key=1), native)
    language_schema = "\n".join(statement + ";" for statement in _statements(SCHEMA)
                                if "notification_device_languages" in statement)
    adapter.executescript(language_schema)
    compiled = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS notification_device_languages" in compiled
    assert 'CREATE TRIGGER "notification_device_language_removed" AFTER DELETE' in compiled
    assert "DELETE FROM notification_device_languages WHERE device_id=OLD.id" in compiled
    assert "ALTER TABLE notification_devices" not in compiled
