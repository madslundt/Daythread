"""Validate generated topic navigation and repair only the failed excerpt."""

import json
import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    start_quote: str = Field(min_length=1, max_length=300)


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    sections: list[Topic] = Field(min_length=1, max_length=24)
    continues_previous: bool = False


def topic_offsets(result: Analysis, excerpt: str) -> list[int]:
    offsets, previous = [], -1
    for index, topic in enumerate(result.sections):
        start = excerpt.find(topic.start_quote)
        if index == 0:
            # The opening boundary is known. Include any introductory text even
            # when the model anchors its first subject slightly later.
            if start < 0:
                raise ValueError("First topic quote must occur in the transcript")
        elif start <= previous or excerpt.count(topic.start_quote) != 1:
            raise ValueError("Topic boundary must identify a unique ordered transcript span")
        offsets.append(0 if index == 0 else start)
        previous = start
    return offsets


def repair_reason(error: ValueError) -> str:
    if not isinstance(error, ValidationError):
        return str(error)
    allowed = {"title", "sections", "start_quote", "continues_previous"}
    # Include schema paths and error codes, never the rejected response or its values.
    return json.dumps([{
        "field": [part if isinstance(part, int) or part in allowed else "field"
                  for part in item["loc"]],
        "error": item["type"],
    } for item in error.errors(include_input=False, include_context=False, include_url=False)])


async def generate_analysis(model, task: str, excerpt: str) -> tuple[Analysis, list[int]]:
    request = task
    for attempt in range(2):
        if hasattr(model, "generate_structured_json"):
            raw = await model.generate_structured_json(
                request, excerpt, Analysis.model_json_schema(),
            )
        else:
            raw = await model.generate_json(request, excerpt)
        try:
            result = Analysis.model_validate_json(raw)
            return result, topic_offsets(result, excerpt)
        except ValueError as error:
            if attempt:
                raise
            logger.warning("Recording topic response requires repair (%s)", repair_reason(error))
            request = task + (
                "\nRepair the previous response for this excerpt. Validation failed: "
                + repair_reason(error)
                + ". Return the complete JSON object, with no extra fields or Markdown. "
                "Every title must contain 1–160 characters, sections must contain 1–24 items, "
                "and each start_quote must contain 1–300 characters copied exactly from the "
                "excerpt. The first quote must begin at the start of the excerpt; later quotes "
                "must be unique and in transcript order. Keep the fewest useful topics."
            )
    raise AssertionError("Topic repair attempts exhausted")
