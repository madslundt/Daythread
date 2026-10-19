import asyncio
import json
from calendar import monthrange
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from anyio import CancelScope
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import AwareDatetime, ValidationError

from . import conversation_prep
from .archive_export import install_archive_export
from .audio import render_window
from .audio_forwarding import RedactForwardingToken, forwarding_routes
from .blob_storage import storage_from_settings
from .configuration import RESTART_FIELDS, Configuration
from .conversation_tags import conversation_tag_routes
from .decision_api import decision_routes
from .domain import (
    ButtonEvent,
    CaptureGap,
    CaptureState,
    ChatRequest,
    ChatScopeConflict,
    ChatScopeNotFound,
    EvidenceChanged,
    Feedback,
    FeedbackEvent,
    HintPolicy,
    PhoneObservation,
    RewindRequest,
    TranscriptCorrection,
    stamp,
    utc_now,
    wav_duration,
)
from .generation_jobs import generation_routes
from .importance import router as importance_router
from .inbox_api import inbox_router
from .inference import InferenceError
from .inference_errors import InferenceDisabled
from .label_api import router as label_router
from .label_models import LabelError
from .location_zones import location_zone_routes
from .locations import CaptureLocation
from .model_connections import model_connection_routes
from .notification_api import notification_routes
from .omi_api import ImportRequest, OmiAPIError
from .prep_models import PrepGenerate, PrepResult, PrepSearch, PrepSearchResult, PrepValidate
from .profiles import DEFAULT_PROFILE, NewProfile, Profiles, selected_profile
from .recap_api import recap_routes
from .recording_groups import recording_groups
from .recording_locations import recording_location_routes
from .recording_uploads import recording_upload_routes
from .retrieval import DateScopeError
from .service import Memory, public_source
from .settings import RUNTIME_FIELDS, Settings, public_settings, validate_setting_changes
from .source_matching import UploadGrouping
from .speaker_api import speaker_routes
from .store import Store
from .summary_translation import summary_translation_routes
from .translation import translation_routes
from .vendor_catalog import file_vendor
from .vendor_routes import vendor_routes
from .voice_api import voice_routes
from .weekly_api import weekly_routes


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    configuration = Configuration(config)
    config = configuration.startup(config)
    settings_lock = Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal config
        store = Store(
            config.data_dir,
            database_url=config.database_url.get_secret_value() if config.database_url else None,
            database_schema=config.database_schema,
            storage=storage_from_settings(config, config.data_dir / "audio"),
        )
        saved = {
            key: value for key, value in store.runtime_settings().items() if key in RUNTIME_FIELDS
        }
        config = Settings.model_validate(config.model_dump() | saved)
        async with httpx.AsyncClient(
            timeout=config.inference_timeout_seconds, follow_redirects=False
        ) as client:
            app.state.profiles = Profiles(config.data_dir, client, settings=config)
            try:
                default = app.state.profiles.start(DEFAULT_PROFILE, config, store)
                for field in (
                    "memory", "weekly", "omi", "worker", "weekly_worker", "embedding_worker"
                ):
                    setattr(app.state, field, getattr(default, field))
                for profile in app.state.profiles.list():
                    app.state.profiles.start(profile["id"], config)
                yield
            finally:
                await app.state.profiles.close()

    app = FastAPI(
        title="Daythread",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def profile_scope(request: Request, call_next):
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)
        headers = request.headers.getlist("x-omi-profile")
        query = request.query_params.getlist("profile_id")
        if request.url.path in ("/v1/profiles", "/v1/settings") or (
            request.url.path.startswith("/v1/profiles/")
            or request.url.path == "/v1/configuration"
            or request.url.path.startswith("/v1/configuration/")
        ):
            headers, query = [], []
        if len(headers) > 1 or len(query) > 1 or (headers and query and headers != query):
            return JSONResponse(
                status_code=422, content={"detail": "Supply one matching profile ID"}
            )
        profile_id = (headers or query or [DEFAULT_PROFILE])[0]
        if profile_id not in app.state.profiles.runtimes:
            return JSONResponse(status_code=404, content={"detail": "Profile not found"})
        token = selected_profile.set(profile_id)
        request.state.memory = memory()
        try:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Omi-Profile"] = profile_id
            return response
        finally:
            selected_profile.reset(token)

    app.include_router(label_router)
    app.include_router(importance_router)

    @app.exception_handler(LabelError)
    async def label_error(request: Request, exc: LabelError) -> Response:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    app.add_middleware(RedactForwardingToken)

    def memory() -> Memory:
        return runtime().memory

    def runtime():
        return app.state.profiles.runtimes[selected_profile.get()]

    install_archive_export(app, lambda: app.state.profiles)

    @app.get("/v1/profiles")
    def profiles() -> dict:
        return {"profiles": app.state.profiles.list(), "default_profile_id": DEFAULT_PROFILE}

    @app.post("/v1/profiles", status_code=201)
    async def create_profile(body: NewProfile) -> dict:
        with settings_lock:
            try:
                profile = app.state.profiles.create(body)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            app.state.profiles.start(profile["id"], config)
        return profile

    @app.patch("/v1/profiles/{profile_id}")
    def rename_profile(profile_id: str, body: NewProfile) -> dict:
        try:
            return app.state.profiles.rename(profile_id, body)
        except KeyError:
            raise HTTPException(404, "Profile not found") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    app.include_router(forwarding_routes(memory))
    app.include_router(decision_routes(memory))
    app.include_router(inbox_router(memory))
    app.include_router(translation_routes(memory))
    app.include_router(summary_translation_routes(memory))
    app.include_router(notification_routes(memory))
    app.include_router(generation_routes(lambda: runtime().jobs))
    app.include_router(recap_routes(memory, lambda: runtime().jobs))
    app.include_router(speaker_routes(memory))
    app.include_router(voice_routes(memory))
    app.include_router(recording_upload_routes(memory))
    app.include_router(location_zone_routes(memory))
    app.include_router(recording_location_routes(memory))
    app.include_router(conversation_tag_routes(memory))
    app.include_router(vendor_routes(lambda: config, runtime))

    @app.exception_handler(OmiAPIError)
    async def omi_error(request: Request, exc: OmiAPIError) -> Response:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.get("/v1/integrations/omi")
    def omi_status() -> dict:
        return runtime().omi.status()

    @app.post("/v1/integrations/omi/import")
    async def omi_import(body: ImportRequest) -> dict:
        result = await runtime().omi.import_page(body)
        memory().store.apply_retention(
            config.audio_retention_days, config.transcript_retention_days
        )
        # Apply retention before exposing imported text in the response.
        result["items"] = [
            public_source(row)
            for item in result["items"]
            if (row := memory().store.get_audio(item["id"])) is not None
        ]
        return result

    @app.exception_handler(httpx.HTTPError)
    @app.exception_handler(InferenceError)
    async def upstream_error(request: Request, exc: Exception) -> Response:
        if isinstance(exc, InferenceDisabled):
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        return JSONResponse(status_code=502, content={"detail": "Local inference is unavailable"})

    @app.exception_handler(EvidenceChanged)
    async def evidence_changed(request: Request, exc: Exception) -> Response:
        return JSONResponse(
            status_code=409,
            content={"detail": "Recorded evidence or its output changed; reload and retry"},
        )

    @app.exception_handler(DateScopeError)
    async def invalid_chat_date(request: Request, exc: Exception) -> Response:
        return JSONResponse(status_code=422, content={"detail": "Use valid ISO calendar dates"})

    @app.get("/v1/status")
    def status() -> dict:
        return {
            "archive_revision": memory().store.archive_revision(),
            "label_revision": memory().store.label_revision(),
            "timezone": config.timezone,
            "worker_enabled": config.worker_enabled,
            "processing_enabled": {
                "stt": config.stt_enabled, "llm": config.llm_enabled,
                "embedding": config.embedding_enabled,
            },
            "weekly_worker_running": bool(
                runtime().weekly_worker and not runtime().weekly_worker.done()
            ),
            "worker_running": bool(runtime().worker and not runtime().worker.done()),
            "meeting_max_minutes": config.meeting_max_minutes,
            "hint_policy": config.hint_policy.model_dump(),
            "capture_paused": config.capture_paused,
            "inference": getattr(memory().inference, "health", {}),
            **memory().store.observations(),
            "generation": runtime().jobs.observation(),
            "embeddings": memory().retriever.status()
            | {
                "worker_running": bool(
                    runtime().embedding_worker and not runtime().embedding_worker.done()
                ),
            },
        }

    @app.post("/v1/embeddings/rebuild")
    async def rebuild_embeddings() -> dict:
        retriever = memory().retriever
        set_ids = [key for key in (retriever.set_id, *retriever.route_set_ids.values()) if key]
        if not set_ids or not config.background_processing_enabled:
            raise HTTPException(409, "Enable embeddings and restart before rebuilding")
        await asyncio.to_thread(memory().store.rebuild_embeddings, set_ids)
        return await asyncio.to_thread(retriever.status)

    @app.put("/v1/phones/{phone_id}/status")
    def phone_status(phone_id: UUID, body: PhoneObservation) -> dict:
        try:
            value = body.model_dump(mode="json")
            value["observed_at"] = stamp(body.observed_at)
            return memory().store.save_phone(str(phone_id), value)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.put("/v1/capture-state/{event_id}")
    def capture_state(event_id: UUID, body: CaptureState) -> dict:
        value = body.model_dump(mode="json")
        value["occurred_at"] = stamp(body.occurred_at)
        try:
            return memory().store.save_capture_state(str(event_id), value)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/completeness")
    def completeness(
        start: AwareDatetime,
        end: AwareDatetime,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        if not start < end or end - start > timedelta(days=366):
            raise HTTPException(422, "Choose an ordered interval of at most 366 days")
        return memory().store.completeness(start, end, offset, limit)

    @app.get("/v1/outputs/{output_id}/evidence")
    def output_evidence(output_id: UUID) -> dict:
        result = memory().store.evidence(str(output_id))
        if result is None:
            raise HTTPException(404, "Output evidence unavailable")
        return result

    @app.put("/v1/feedback/{feedback_id}")
    def feedback(feedback_id: UUID, body: Feedback) -> dict:
        try:
            return memory().store.save_feedback(str(feedback_id), body.model_dump(mode="json"))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/feedback")
    def feedback_list(
        limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)
    ) -> dict:
        return memory().store.feedback(limit, offset)

    @app.post("/v1/feedback/{feedback_id}/events")
    def feedback_event(feedback_id: UUID, body: FeedbackEvent) -> dict:
        try:
            return memory().store.resolve_feedback(str(feedback_id), body.model_dump(mode="json"))
        except KeyError as exc:
            raise HTTPException(404, "Feedback unavailable") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/v1/feedback/{feedback_id}")
    def delete_feedback(feedback_id: UUID) -> dict:
        memory().store.delete_feedback(str(feedback_id))
        return {"deleted": str(feedback_id)}

    @app.get("/v1/settings")
    def get_settings() -> dict:
        return public_settings(config)

    @app.get("/v1/usage")
    def get_usage(
        month: Annotated[str | None, Query(pattern=r"^[0-9]{4}-[0-9]{2}$")] = None,
    ) -> dict:
        active = memory()
        if month is None:
            month = utc_now().astimezone(ZoneInfo(active.settings.timezone)).date().isoformat()[:7]
        try:
            date.fromisoformat(f"{month}-01")
        except ValueError as exc:
            raise HTTPException(422, "Use a valid month in YYYY-MM format") from exc
        return active.usage.report(active.settings.timezone, month=month)

    def activate_settings(revised: Settings, changes: dict) -> None:
        nonlocal config
        config = revised
        for profile in app.state.profiles.runtimes.values():
            profile.memory.settings = revised
            profile.memory.notifications.settings = revised
            profile.memory.inference.settings = revised
            profile.omi.key = revised.omi_api_key.get_secret_value().strip()
            for adapter in profile.vendors.values():
                adapter.key = getattr(revised, adapter.key_setting).get_secret_value().strip()
            if profile.memory is not app.state.memory:
                profile.memory.store.save_settings({
                    key: getattr(revised, key) for key in changes if key in RUNTIME_FIELDS
                })
            if "timezone" in changes:
                profile.memory.store.save_decision_timezone(config.timezone)
                profile.memory.store.invalidate_diaries()
            if {"audio_retention_days", "transcript_retention_days"} & changes.keys():
                profile.memory.store.apply_retention(
                    config.audio_retention_days, config.transcript_retention_days
                )

    @app.post("/v1/managed-model-policy")
    def quiesce_models(changes: dict) -> dict:
        if not config.managed_hosting:
            raise HTTPException(403, "This policy is only available for managed archives")
        allowed = {"stt_enabled", "llm_enabled", "embedding_enabled"}
        if not changes or changes.keys() - allowed or any(
            type(value) is not bool for value in changes.values()
        ):
            raise HTTPException(422, "Supply task policy flags as booleans")
        with settings_lock:
            if any(value and not getattr(config, key) for key, value in changes.items()):
                raise HTTPException(409, "Restart the archive to enable a paused task")
            # Update existing objects too: in-flight tasks may hold a settings reference
            # or a task-bound inference adapter while another HTTP lease prevents restart.
            targets = [config]
            for profile in app.state.profiles.runtimes.values():
                current = profile.memory
                targets.extend([
                    current.settings, current.inference.settings, current.retriever.settings,
                    current.retriever.embeddings.settings, current.notifications.settings,
                ])
                targets.extend(
                    adapter.settings
                    for adapter in getattr(current.retriever, "embedding_adapters", {}).values()
                )
                for kind in ("stt", "llm"):
                    if changes.get(kind + "_enabled") is False:
                        current.inference.health[kind] = {"state": "disabled"}
                if changes.get("embedding_enabled") is False:
                    current.retriever.set_id = None
                    current.retriever.route_set_ids = {}
                    current.retriever.embeddings.health = {"state": "disabled"}
                    for adapter in getattr(current.retriever, "embedding_adapters", {}).values():
                        adapter.health = {"state": "disabled"}
            for target in targets:
                for key, value in changes.items():
                    if not value:
                        setattr(target, key, False)
            return {key: getattr(config, key) for key in sorted(allowed)}

    @app.get("/v1/configuration")
    def get_configuration() -> dict:
        with settings_lock:
            return configuration.view(config)

    def preview_connection(changes: dict) -> Settings:
        with settings_lock:
            return configuration.prepare(config, changes)

    app.include_router(
        model_connection_routes(preview_connection, lambda: memory().inference.client)
    )

    @app.patch("/v1/configuration")
    def update_configuration(body: dict) -> dict:
        with settings_lock:
            if body.get("revision") != configuration.revision:
                raise HTTPException(
                    409, "Settings changed. Reload before saving."
                )
            changes = body.get("changes")
            if not isinstance(changes, dict):
                raise HTTPException(422, "Supply configuration changes")
            try:
                desired = configuration.prepare(config, changes)
                live = {key: getattr(desired, key) for key in changes if key not in RESTART_FIELDS}
                revised = Settings.model_validate(config.model_dump() | live)
            except ValidationError as exc:
                problems = [
                    f"{'.'.join(map(str, item['loc'])) or 'Settings'}: {item['msg']}"
                    for item in exc.errors(include_input=False, include_context=False)
                ]
                raise HTTPException(422, "; ".join(problems)) from exc
            except (ValueError, TypeError, AttributeError) as exc:
                raise HTTPException(422, "Invalid configuration settings or model routes") from exc
            configuration.save(desired, changes.keys())
            app.state.memory.store.save_settings({
                key: getattr(desired, key) for key in changes if key in RUNTIME_FIELDS
            })
            activate_settings(revised, changes)
            return configuration.view(config)

    @app.patch("/v1/settings")
    def update_settings(changes: dict) -> dict:
        nonlocal config
        with settings_lock:
            if unknown := changes.keys() - set(RUNTIME_FIELDS):
                raise HTTPException(422, f"Unknown settings: {', '.join(sorted(unknown))}")
            try:
                validate_setting_changes(config, changes)
                revised = Settings.model_validate(config.model_dump() | changes)
            except ValueError as exc:
                raise HTTPException(422, "Invalid setting value") from exc
            app.state.memory.store.save_settings({key: getattr(revised, key) for key in changes})
            activate_settings(revised, changes)
            configuration.revision = str(uuid4())
            return public_settings(config)

    @app.get("/v1/openapi")
    def schema() -> dict:
        return app.openapi()

    @app.put("/v1/audio/{audio_id}")
    async def upload(
        audio_id: UUID,
        request: Request,
        device_id: Annotated[str, Query(min_length=1, max_length=80, pattern=r"^[\w-]+$")],
        started_at: AwareDatetime,
        source_kind: Literal["", "manual_upload"] = "",
        source_id: Annotated[str, Query(max_length=255)] = "",
        vendor_id: Annotated[str, Query(max_length=80)] = "manual",
        recording_id: UUID | None = None,
        part_index: Annotated[int | None, Query(ge=0, le=59)] = None,
        part_count: Annotated[int | None, Query(ge=1, le=60)] = None,
        conversation_source_id: UUID | None = None,
        recording_started_at: AwareDatetime | None = None,
        recording_ended_at: AwareDatetime | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        horizontal_accuracy: float | None = None,
        location_captured_at: AwareDatetime | None = None,
    ) -> dict:
        try:
            file_vendor(vendor_id)
            if vendor_id != "manual" and source_kind != "manual_upload":
                raise ValueError("Vendor provenance applies to imported audio files")
            grouping = UploadGrouping(
                vendor_id=vendor_id if vendor_id != "manual" else None,
                recording_id=str(recording_id) if recording_id else None,
                part_index=part_index, part_count=part_count,
                conversation_source_id=(
                    str(conversation_source_id) if conversation_source_id else None
                ),
                recording_started_at=recording_started_at, recording_ended_at=recording_ended_at,
            ).model_dump(exclude_none=True, mode="json")
            if grouping and source_kind != "manual_upload":
                raise ValueError("Recording groups apply to manual uploads")
            if any(v is not None for v in (
                latitude, longitude, horizontal_accuracy, location_captured_at
            )):
                location = CaptureLocation(
                    latitude=latitude, longitude=longitude,
                    horizontal_accuracy=horizontal_accuracy, captured_at=location_captured_at,
                )
                if source_kind or device_id in {"web", "manual-web"}:
                    raise ValueError("GPS observations apply only to phone capture")
                location.validate_capture_time(started_at)
                grouping["location"] = location.model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if started_at > utc_now() + timedelta(seconds=30):
            raise HTTPException(422, "Capture time is in the future; check phone clock")
        chunks = bytearray()
        async for part in request.stream():
            chunks.extend(part)
            if len(chunks) > 4_000_000:
                raise HTTPException(413, "Maximum audio chunk size is 4 MB")
        data = bytes(chunks)
        try:
            duration = wav_duration(data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            ingest = asyncio.create_task(asyncio.to_thread(
                memory().store.ingest, audio_id, device_id, started_at, data, duration,
                source_kind=source_kind, source_id=source_id,
                source_metadata=grouping,
            ))
            try:
                row = await asyncio.shield(ingest)
            except asyncio.CancelledError:
                # SQLite/S3 work keeps running after cancellation of to_thread.
                # Join it before request/profile cleanup can close the archive.
                with CancelScope(shield=True):
                    while not ingest.done():
                        try:
                            await asyncio.shield(ingest)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    with suppress(Exception):
                        ingest.result()
                raise
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return public_source(row)

    @app.get("/v1/audio/{audio_id}")
    def audio(audio_id: UUID) -> Response:
        row = memory().store.get_audio(str(audio_id))
        if row is None or not row["audio_available"]:
            raise HTTPException(404, "Audio chunk not found")
        if memory().store.audio_storage.is_remote:
            try:
                return Response(
                    memory().store.read_audio(str(audio_id)), media_type="audio/wav",
                    headers={"Content-Disposition": f'attachment; filename="{audio_id}.wav"'},
                )
            except FileNotFoundError:
                raise HTTPException(404, "Audio chunk not found") from None
        if not memory().store.has_audio(str(audio_id)):
            raise HTTPException(404, "Audio chunk not found")
        return FileResponse(row["path"], media_type="audio/wav", filename=f"{audio_id}.wav")

    @app.get("/v1/storage")
    def storage_usage() -> dict:
        return {"used_bytes": sum(
            runtime.memory.store.storage_usage()
            for runtime in app.state.profiles.runtimes.values()
        )}

    @app.delete("/v1/audio/{audio_id}")
    def delete_audio(audio_id: UUID) -> dict:
        if not memory().store.delete_audio(str(audio_id)):
            raise HTTPException(404, "Audio chunk not found")
        return {"deleted": str(audio_id)}

    @app.post("/v1/audio/{audio_id}/retry")
    def retry_transcription(audio_id: UUID) -> dict:
        with memory().store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM audio WHERE id=?", (str(audio_id),)).fetchone()
            if not row:
                raise HTTPException(404, "Source unavailable")
            empty_completed = row["state"] == "done" and not row["text"] and (
                row["transcription_outcome"] in ("silence_gate", "empty_stt")
            )
            if (
                row["state"] not in ("blocked", "queued") and not empty_completed
            ) or not row["audio_available"]:
                raise HTTPException(409, "Only pending or empty automatic transcripts can retry")
            if not memory().store.has_audio(str(audio_id)):
                raise HTTPException(409, "Restore the missing audio file before retrying")
            db.execute(
                "UPDATE audio SET state='queued',retry_at='',error=NULL,error_code=NULL,"
                "transcription_details='{}' WHERE id=?",
                (str(audio_id),),
            )
        return {"id": str(audio_id), "state": "queued"}

    @app.get("/v1/sources/{source_id}")
    def source(source_id: UUID) -> dict:
        row = memory().store.get_audio(str(source_id))
        if row is None:
            raise HTTPException(404, "Source unavailable")
        return public_source(row)

    @app.get("/v1/recording-groups")
    def timeline_recording_groups(
        day: date, q: str = Query(default="", max_length=200),
        limit: int = Query(default=30, ge=1, le=200), offset: int = Query(default=0, ge=0),
        zone_id: UUID | None = None,
        tag_id: UUID | None = None,
    ) -> dict:
        start, end = memory().day_window(day)
        return recording_groups(
            memory().store, start, end, q, limit, offset,
            zone_id=str(zone_id) if zone_id else None,
            tag_id=str(tag_id) if tag_id else None,
        )

    @app.get("/v1/recording-groups/{group_id}/sources")
    def timeline_group_sources(
        group_id: UUID, day: date, q: str = Query(default="", max_length=200),
        limit: int = Query(default=30, ge=1, le=200), offset: int = Query(default=0, ge=0),
        zone_id: UUID | None = None,
        tag_id: UUID | None = None,
    ) -> dict:
        start, end = memory().day_window(day)
        try:
            return recording_groups(
                memory().store, start, end, q, limit, offset, group_id=str(group_id),
                zone_id=str(zone_id) if zone_id else None,
                tag_id=str(tag_id) if tag_id else None,
            )
        except KeyError as exc:
            raise HTTPException(404, "Recording group unavailable") from exc

    @app.get("/v1/transcripts")
    def transcripts(
        day: date | None = None,
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        deduplicate: bool = False,
        zone_id: UUID | None = None,
        tag_id: UUID | None = None,
    ) -> dict:
        start, end = memory().day_window(day) if day else (None, None)
        rows, total = memory().store.transcripts(
            start, end, q, limit, offset, deduplicate=deduplicate,
            zone_id=str(zone_id) if zone_id else None,
            tag_id=str(tag_id) if tag_id else None,
        )
        return {"items": [public_source(row) for row in rows], "total": total}

    @app.get("/v1/transcripts/{audio_id}")
    def transcript(audio_id: UUID, response: Response) -> dict:
        row = memory().store.get_audio(str(audio_id))
        if row is None:
            raise HTTPException(404, "Transcript not found")
        response.headers["Cache-Control"] = "no-store"
        return public_source(row)

    @app.patch("/v1/transcripts/{audio_id}")
    def correct_transcript(audio_id: UUID, body: TranscriptCorrection) -> dict:
        row = memory().store.correct_transcript(str(audio_id), body.text)
        if row is None:
            raise HTTPException(404, "Transcript not found")
        return public_source(row)

    @app.get("/v1/transcripts/{audio_id}/revisions")
    def transcript_revisions(audio_id: UUID) -> dict:
        if memory().store.get_audio(str(audio_id)) is None:
            raise HTTPException(404, "Transcript not found")
        return {"items": memory().store.transcript_revisions(str(audio_id))}

    @app.post("/v1/gaps")
    def capture_gap(body: CaptureGap) -> dict:
        if body.ended_at > utc_now() + timedelta(seconds=30):
            raise HTTPException(422, "Gap time is in the future; check phone clock")
        value = body.model_dump(mode="json") | {
            "started_at": stamp(body.started_at),
            "ended_at": stamp(body.ended_at),
        }
        try:
            return memory().store.append_gap(value)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/events")
    def event(event: ButtonEvent, receipt_only: bool = Query(default=False)) -> dict:
        if event.occurred_at > utc_now() + timedelta(seconds=30):
            raise HTTPException(422, "Event time is in the future; check phone clock")
        try:
            stored = memory().store.append_event(
                event, config.meeting_max_minutes, config.marker_context_seconds, config.hint_policy
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        policy = HintPolicy.model_validate_json(stored["hint_policy"])
        receipt = {
            field: stored[field] for field in ("event_id", "device_id", "occurred_at", "action")
        } | {
            "meeting_max_minutes": stored["max_minutes"],
            "meeting_min_seconds": policy.meeting_min_seconds,
            "marker_cooldown_seconds": policy.marker_cooldown_seconds,
        }
        metadata = json.loads(stored["hint_policy"])
        receipt.update({field: metadata[field] for field in (
            "meeting_intent", "meeting_id", "meeting_capture_device_id",
        )
                        if field in metadata})
        return receipt if receipt_only else memory().timeline() | receipt

    @app.get("/v1/timeline")
    def timeline() -> dict:
        return memory().timeline()

    @app.post("/v1/rewind")
    async def rewind(body: RewindRequest) -> dict:
        end = body.end or utc_now()
        return await memory().window(
            end - timedelta(minutes=body.minutes),
            end,
            f"a rewind of the last {body.minutes} minutes",
            body.summarize,
            kind="rewind",
        )

    @app.get("/v1/rewind/audio")
    def rewind_audio(
        minutes: Annotated[int, Query(ge=1, le=60)] = 10, end: AwareDatetime | None = None
    ) -> Response:
        stop = end or utc_now()
        start = stop - timedelta(minutes=minutes)
        rows = [row for row in memory().store.window(start, stop) if row["audio_available"]]
        if not rows:
            raise HTTPException(404, "No audio captured in this window")
        data, gaps = render_window(rows, start, stop, read_audio=memory().store.read_audio)
        return Response(
            data,
            media_type="audio/wav",
            headers={"X-Omi-Audio-Gaps": str(len(gaps)), "Cache-Control": "no-store"},
        )

    @app.get("/v1/diary")
    def diary() -> dict:
        return {
            "items": [
                {key: row.get(key) for key in ("day", "summary", "generated_at", "pending_chunks")}
                for row in memory().cached_recaps([
                    date.fromisoformat(saved["day"]) for saved in memory().store.diaries()
                ])
            ]
        }

    @app.get("/v1/calendar")
    def calendar(month: Annotated[str, Query(pattern=r"^[0-9]{4}-[0-9]{2}$")]) -> dict:
        try:
            start = date.fromisoformat(f"{month}-01")
        except ValueError as exc:
            raise HTTPException(422, "Use a valid month in YYYY-MM format") from exc
        end = start.replace(day=monthrange(start.year, start.month)[1])
        return {
            "month": month,
            "days": [
                {"day": day, "has_summary": True} for day in memory().store.summary_days(start, end)
            ],
        }

    @app.get("/v1/diary/{day}.md")
    def diary_markdown(day: date) -> FileResponse:
        record = memory().store.diary(day.isoformat())
        if not record:
            start, end = memory().day_window(day)
            if not memory().store.decisions(start, end)["total"]:
                raise HTTPException(404, "Generate a recap or create decisions first")
        content = memory().cached_recap(day)
        memory().store.export_diary(day.isoformat(), content)
        return FileResponse(
            memory().store.diary_dir / f"{day}.md", media_type="text/markdown", filename=f"{day}.md"
        )

    @app.get("/v1/diary/{day}/versions")
    def diary_versions(day: date) -> dict:
        return {"items": memory().store.diary_versions(day.isoformat())}

    @app.post("/v1/meetings/{meeting_id}/recap")
    async def meeting_recap(meeting_id: UUID) -> dict:
        matches = [
            meeting
            for meeting in memory().timeline()["meetings"]
            if meeting["id"] == str(meeting_id)
        ]
        if not matches:
            raise HTTPException(404, "Meeting not found")
        meeting = matches[0]
        start = datetime.fromisoformat(meeting["context_start"])
        end = datetime.fromisoformat(meeting["context_end"])
        result = await memory().window(
            start, end, "meeting notes with decisions and action items", kind="meeting"
        )
        return {**result, "meeting": meeting}

    @app.post("/v1/conversation-prep/search", response_model=PrepSearchResult)
    def prep_search(body: PrepSearch, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return conversation_prep.search(memory(), body)

    @app.post("/v1/conversation-prep/generate", response_model=PrepResult)
    async def prep_generate(body: PrepGenerate, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            return await conversation_prep.generate(memory(), body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/conversation-prep/validate")
    def prep_validate(body: PrepValidate, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return {"valid": conversation_prep.validate(memory(), body.search, body.snapshot)}

    @app.post("/v1/chat")
    async def chat(body: ChatRequest) -> dict:
        try:
            return await memory().chat(
                body.question,
                body.limit,
                str(body.session_id) if body.session_id else None,
                body.day,
                str(body.meeting_id) if body.meeting_id else None,
                bool(body.model_fields_set & {"day", "meeting_id", "start_day", "end_day"}),
                body.start_day,
                body.end_day,
            )
        except ChatScopeNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except ChatScopeConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Chat session not found") from exc

    @app.post("/v1/chat/stream")
    async def chat_stream(body: ChatRequest) -> StreamingResponse:
        async def events():
            queue = asyncio.Queue(maxsize=16)

            async def run():
                try:
                    result = await memory().chat(
                        body.question,
                        body.limit,
                        str(body.session_id) if body.session_id else None,
                        body.day,
                        str(body.meeting_id) if body.meeting_id else None,
                        bool(body.model_fields_set & {"day", "meeting_id", "start_day", "end_day"}),
                        body.start_day,
                        body.end_day,
                        on_event=queue.put,
                    )
                    await queue.put({"type": "done", "result": result})
                except Exception as exc:
                    if isinstance(exc, (ChatScopeNotFound, KeyError)):
                        status, message = 404, "Chat or selected recordings not found"
                    elif isinstance(exc, ChatScopeConflict):
                        status, message = 409, "Start a new chat to choose a different scope"
                    elif isinstance(exc, EvidenceChanged):
                        status, message = 409, "Recorded evidence changed; reload and retry"
                    elif isinstance(exc, DateScopeError):
                        status, message = 422, "Use valid ISO calendar dates"
                    elif isinstance(exc, InferenceDisabled):
                        status, message = 409, str(exc)
                    elif isinstance(exc, (InferenceError, httpx.HTTPError, TimeoutError)):
                        status, message = 502, "Local inference is unavailable"
                    else:
                        status, message = 500, "The answer could not be completed; try again"
                    await queue.put({"type": "error", "status": status, "message": message})

            task = asyncio.create_task(run())
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=10)
                    except TimeoutError:
                        event = {"type": "heartbeat"}
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                    if event["type"] in ("done", "error"):
                        break
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/search")
    async def search(body: ChatRequest) -> dict:
        try:
            return await memory().search(
                body.question,
                body.limit,
                str(body.session_id) if body.session_id else None,
                body.day,
                str(body.meeting_id) if body.meeting_id else None,
                bool(body.model_fields_set & {"day", "meeting_id", "start_day", "end_day"}),
                body.start_day,
                body.end_day,
            )
        except ChatScopeNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except ChatScopeConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Chat session not found") from exc

    @app.get("/v1/chat/sessions")
    def chat_sessions() -> dict:
        return {"items": memory().store.chat_sessions()}

    @app.get("/v1/chat/sessions/{session_id}")
    def chat_session(session_id: UUID) -> dict:
        session = memory().store.chat_session(str(session_id))
        if not session:
            raise HTTPException(404, "Chat session not found")
        return session

    app.include_router(weekly_routes(lambda: runtime().weekly))

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> FileResponse:
        if path == "v1" or path.startswith("v1/"):
            raise HTTPException(404, "API route not found")
        root = (config.web_dir or Path(__file__).parent / "web").resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(404, "File not found")
        if candidate.is_file():
            return FileResponse(candidate)
        index = root / "index.html"
        if not index.is_file() or (Path(path).suffix and path):
            raise HTTPException(404, "Web app assets are not installed")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
