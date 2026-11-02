"""Translate notification interface copy at delivery, preserving generated content."""

import re
from typing import Literal

InterfaceLanguage = Literal["en", "da", "de", "es", "fr", "nb", "sv"]

# These nouns and existing activity labels match the native interface catalogs.
QUESTION_NOUNS = {
    "en": ("question", "questions"), "da": ("spørgsmål", "spørgsmål"),
    "de": ("Frage", "Fragen"), "es": ("pregunta", "preguntas"),
    "fr": ("question", "questions"), "nb": ("spørsmål", "spørsmål"),
    "sv": ("fråga", "frågor"),
}
NO_QUESTION = {
    "en": "No question found", "da": "Intet spørgsmål fundet", "de": "Keine Frage gefunden",
    "es": "No se encontró ninguna pregunta", "fr": "Aucune question trouvée",
    "nb": "Ingen spørsmål funnet", "sv": "Ingen fråga hittades",
}
NO_QUESTION_BODY = {
    "en": "No clear question was found in the recent audio.",
    "da": "Der blev ikke fundet et tydeligt spørgsmål i den seneste lyd.",
    "de": "In der letzten Audioaufnahme wurde keine klare Frage gefunden.",
    "es": "No se encontró una pregunta clara en el audio reciente.",
    "fr": "Aucune question claire n’a été trouvée dans l’audio récent.",
    "nb": "Det ble ikke funnet noe tydelig spørsmål i den siste lyden.",
    "sv": "Ingen tydlig fråga hittades i det senaste ljudet.",
}
NO_SPEECH = {
    "en": "No transcribed speech is available in this window.",
    "da": "Der er ingen transskriberet tale i dette tidsrum.",
    "de": "Für diesen Zeitraum ist keine transkribierte Sprache verfügbar.",
    "es": "No hay voz transcrita disponible en este intervalo.",
    "fr": "Aucune parole transcrite n’est disponible pour cette période.",
    "nb": "Ingen transkribert tale er tilgjengelig i dette tidsrommet.",
    "sv": "Det finns inget transkriberat tal i det här tidsintervallet.",
}
ACTIVITY_TITLES = {
    "summarize": {
        "en": "Recent audio summary", "da": "Resumé af nylig lyd",
        "de": "Zusammenfassung der letzten Audioaufnahme", "es": "Resumen del audio reciente",
        "fr": "Résumé de l’audio récent", "nb": "Sammendrag av nylig lyd",
        "sv": "Sammanfattning av senaste ljudet",
    },
    "translate": {
        "en": "Recent audio translation", "da": "Oversættelse af nylig lyd",
        "de": "Übersetzung der letzten Audioaufnahme", "es": "Traducción del audio reciente",
        "fr": "Traduction de l’audio récent", "nb": "Oversettelse av nylig lyd",
        "sv": "Översättning av senaste ljudet",
    },
}


CAPTURE_HEALTH_TITLES = {
    "en": "Capture needs a check", "da": "Tjek optagelsen",
    "de": "Aufnahme überprüfen", "es": "Revisa la grabación",
    "fr": "Vérifiez la capture", "nb": "Sjekk opptaket", "sv": "Kontrollera inspelningen",
}
CAPTURE_HEALTH_STALE = {
    "en": "The phone has stopped reporting capture progress. Open Capture to check the phone, "
          "pendant connection and upload queue. Current capture is unknown.",
    "da": "Telefonen rapporterer ikke længere om optagelsen. Åbn Optag for at tjekke telefonen, "
          "forbindelsen til vedhænget og uploadkøen. Den aktuelle optagelse er ukendt.",
    "de": "Das Telefon meldet keinen Aufnahmefortschritt mehr. Öffnen Sie Aufnahme und prüfen Sie "
          "Telefon, Verbindung zum Anhänger und Upload-Warteschlange. "
          "Die aktuelle Aufnahme ist unbekannt.",
    "es": "El teléfono ha dejado de informar del progreso. Abre Grabación para revisar "
          "el teléfono, la conexión del colgante y la cola de subidas. "
          "Se desconoce el estado actual de la grabación.",
    "fr": "Le téléphone ne signale plus la progression. Ouvrez Capture pour vérifier le téléphone, "
          "la connexion du pendentif et la file d’envoi. L’état actuel de la capture est inconnu.",
    "nb": "Telefonen rapporterer ikke lenger fremdrift. Åpne Opptak for å sjekke telefonen, "
          "tilkoblingen til anhenget og opplastingskøen. Gjeldende opptaksstatus er ukjent.",
    "sv": "Telefonen rapporterar inte längre förloppet. Öppna Inspelning för att kontrollera "
          "telefonen, "
          "anslutningen till hänget och uppladdningskön. Aktuell inspelningsstatus är okänd.",
}
CAPTURE_HEALTH_STALLED = {
    "en": "The phone reports no recent capture progress. Open Capture to check the microphone, "
          "pendant connection and available phone storage.",
    "da": "Telefonen rapporterer ingen nylig fremdrift. Åbn Optag for at tjekke mikrofonen, "
          "forbindelsen til vedhænget og ledig plads på telefonen.",
    "de": "Das Telefon meldet keinen aktuellen Aufnahmefortschritt. Öffnen Sie Aufnahme und prüfen "
          "Sie Mikrofon, Verbindung zum Anhänger und freien Telefonspeicher.",
    "es": "El teléfono no informa de progreso reciente. Abre Grabación para revisar el micrófono, "
          "la conexión del colgante y el espacio disponible en el teléfono.",
    "fr": "Le téléphone ne signale aucune progression récente. Ouvrez Capture pour vérifier le "
          "microphone, la connexion du pendentif et l’espace disponible sur le téléphone.",
    "nb": "Telefonen rapporterer ingen nylig fremdrift. Åpne Opptak for å sjekke mikrofonen, "
          "tilkoblingen til anhenget og ledig lagringsplass på telefonen.",
    "sv": "Telefonen rapporterar inget aktuellt förlopp. Öppna Inspelning för att kontrollera "
          "mikrofonen, anslutningen till hänget och ledigt lagringsutrymme på telefonen.",
}


CHAT_READY = {
    "en": "Chat reply ready", "da": "Chatsvar klar", "de": "Chatantwort bereit",
    "es": "Respuesta de chat lista", "fr": "Réponse au chat prête",
    "nb": "Chatsvar klart", "sv": "Chattsvar klart",
}
CHAT_READY_BODY = {
    "en": "Your reply is saved. Open the chat to read it.",
    "da": "Dit svar er gemt. Åbn chatten for at læse det.",
    "de": "Ihre Antwort wurde gespeichert. Öffnen Sie den Chat, um sie zu lesen.",
    "es": "Tu respuesta está guardada. Abre el chat para leerla.",
    "fr": "Votre réponse est enregistrée. Ouvrez le chat pour la lire.",
    "nb": "Svaret ditt er lagret. Åpne chatten for å lese det.",
    "sv": "Ditt svar har sparats. Öppna chatten för att läsa det.",
}
CHAT_FAILED = {
    "en": "Chat could not finish", "da": "Chatten kunne ikke afsluttes",
    "de": "Chat konnte nicht abgeschlossen werden", "es": "No se pudo completar el chat",
    "fr": "Le chat n’a pas pu aboutir", "nb": "Chatten kunne ikke fullføres",
    "sv": "Chatten kunde inte slutföras",
}
CHAT_FAILED_BODY = {
    "en": "Your question is saved. Open the chat to try again.",
    "da": "Dit spørgsmål er gemt. Åbn chatten for at prøve igen.",
    "de": "Ihre Frage wurde gespeichert. Öffnen Sie den Chat, um es erneut zu versuchen.",
    "es": "Tu pregunta está guardada. Abre el chat para intentarlo de nuevo.",
    "fr": "Votre question est enregistrée. Ouvrez le chat pour réessayer.",
    "nb": "Spørsmålet ditt er lagret. Åpne chatten for å prøve igjen.",
    "sv": "Din fråga har sparats. Öppna chatten för att försöka igen.",
}


def present_notification(event: dict, interface_language: str = "en") -> dict:
    language = interface_language if interface_language in QUESTION_NOUNS else "en"
    if event["kind"] == "chat":
        ready = event["title"] == CHAT_READY["en"]
        return event | {"title": (CHAT_READY if ready else CHAT_FAILED)[language],
                        "body": (CHAT_READY_BODY if ready else CHAT_FAILED_BODY)[language]}
    if event["kind"] == "capture_health":
        bodies = (CAPTURE_HEALTH_STALE if event["target"].get("issue") == "report_stale"
                  else CAPTURE_HEALTH_STALLED)
        return event | {"title": CAPTURE_HEALTH_TITLES[language], "body": bodies[language]}
    if event["kind"] != "wearable_action" or language == "en":
        return event
    target = event["target"]
    first, separator, answer = event["body"].partition("\n")
    # Old saved events did not carry action/count metadata. Their count is a
    # complete UI-only first line, never a phrase inside a model's answer.
    count_line = re.fullmatch(r"(0|[1-9][0-9]{0,3}) (question|questions)", first)
    if count_line and separator and target.get("action") in {None, "answer"}:
        count = int(count_line[1])
        if count > 1000 or count_line[2] != ("question" if count == 1 else "questions"):
            return event
        if "question_count" in target and target["question_count"] != count:
            return event
        noun = QUESTION_NOUNS[language][count != 1]
        title = event["title"]
        if count == 0:
            if title == NO_QUESTION["en"]:
                title = NO_QUESTION[language]
            if answer == NO_QUESTION_BODY["en"]:
                answer = NO_QUESTION_BODY[language]
            elif answer == NO_SPEECH["en"]:
                answer = NO_SPEECH[language]
        return event | {"title": title, "body": f"{count} {noun}\n{answer}"}
    action = target.get("action")
    titles = ACTIVITY_TITLES.get(action)
    if titles and event["title"] == titles["en"]:
        body = NO_SPEECH[language] if event["body"] == NO_SPEECH["en"] else event["body"]
        return event | {"title": titles[language], "body": body}
    return event
