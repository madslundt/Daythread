"""Model-specific embedding contracts and formatting, independent of inference."""

import re
from typing import TYPE_CHECKING

from .embedding_chunking import input_fits, tokenizer_for

if TYPE_CHECKING:
    from .settings import Settings


def validate_input_contract(settings: Settings) -> None:
    if not settings.embedding_enabled:
        return
    try:
        manifest = settings.embedding_manifest
        if any(not input_fits(settings.embedding_prefix(query=query) + "🧠", manifest)
               for query in (False, True)):
            raise ValueError("Embedding prefix exhausts input budget")
    except (OSError, ImportError, ValueError) as exc:
        raise ValueError("Invalid local embedding tokenizer or input budget") from exc


def embedding_manifest(settings: Settings) -> dict | None:
    if not settings.embedding_enabled:
        return None
    return {
        "model": settings.embedding_model,
        "revision": settings.embedding_revision,
        "dimension": settings.embedding_dimension,
        "metric": "cosine-float32-v1",
        "splitter": settings.embedding_chunking_strategy,
        "chunking_strategy": settings.embedding_chunking_strategy,
        "passage_chars": settings.embedding_passage_chars,
        "target_tokens": settings.embedding_target_tokens,
        "max_tokens": settings.embedding_max_tokens,
        "overlap_tokens": settings.embedding_overlap_tokens,
        "max_input_tokens": settings.embedding_max_input_tokens,
        "max_input_chars": settings.embedding_max_input_chars,
        "special_tokens": settings.embedding_special_tokens,
        "tokenizer": settings.embedding_tokenizer,
        "tokenizer_revision": settings.embedding_tokenizer_revision,
        "tokenizer_fingerprint": tokenizer_for({
            "tokenizer": settings.embedding_tokenizer,
            "tokenizer_revision": settings.embedding_tokenizer_revision,
        })[1],
        "query_prefix": settings.embedding_prefix(query=True),
        "document_prefix": settings.embedding_prefix(query=False),
        "send_dimensions": settings.embedding_send_dimensions,
        # A different endpoint may resolve the same alias to a different model.
        "endpoint": settings.embedding_base_url,
    }


def embedding_prefix(settings: Settings, *, query: bool) -> str:
    explicit = settings.embedding_query_prefix if query else settings.embedding_document_prefix
    if explicit:
        return explicit
    style = settings.embedding_query_format
    if style == "auto":
        name = settings.embedding_model.lower()
        style = "qwen3" if "qwen3-embedding" in name else (
            "e5" if re.search(r"(?:^|[/_-])e5(?:[-_]|$)", name) else "plain"
        )
    if style == "qwen3" and query:
        return f"Instruct: {settings.embedding_query_instruction}\nQuery:"
    if style == "e5":
        return "query: " if query else "passage: "
    return ""


def embedding_route_settings(settings: Settings, language: str | None = None) -> Settings:
    override = None
    if language:
        language = language.lower()
        override = settings.embedding_routes.get(language) or settings.embedding_routes.get(
            language.split("-")[0]
        )
        if override is None:
            override = next((value for name, value in sorted(settings.embedding_routes.items())
                             if name.split("-")[0] == language.split("-")[0]), None)
    if override is None:
        return settings
    return settings.model_copy(update={
        "embedding_model": override.model or settings.embedding_model,
        "embedding_base_url": override.base_url or settings.embedding_base_url,
        "embedding_token": (
            override.token if override.token is not None else settings.embedding_token
        ),
        "embedding_revision": override.revision or settings.embedding_revision,
        "embedding_dimension": override.dimension or settings.embedding_dimension,
        "embedding_query_prefix": (
            settings.embedding_query_prefix if override.query_prefix is None
            else override.query_prefix
        ),
        "embedding_document_prefix": (
            settings.embedding_document_prefix if override.document_prefix is None
            else override.document_prefix
        ),
    })
