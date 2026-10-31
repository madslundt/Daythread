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


def present_notification(event: dict, interface_language: str = "en") -> dict:
    language = interface_language if interface_language in QUESTION_NOUNS else "en"
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
