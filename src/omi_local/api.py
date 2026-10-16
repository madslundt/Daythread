import asyncio
import json
from calendar import monthrange
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import AwareDatetime, ValidationError

from . import conversation_prep
from .audio import render_window
from .audio_forwarding import RedactForwardingToken, forwarding_routes
from .configuration import RESTART_FIELDS, Configuration
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
from .embeddings import Embeddings, run_embedding_worker
from .importance import router as importance_router
from .inbox_api import inbox_router
from .inference import Inference, InferenceError
from .label_api import router as label_router
from .label_models import LabelError
from .omi_api import ImportRequest, OmiAPI, OmiAPIError
from .prep_models import PrepGenerate, PrepResult, PrepSearch, PrepSearchResult, PrepValidate
from .retrieval import DateScopeError
from .service import Memory, public_source
from .settings import RUNTIME_FIELDS, Settings, public_settings
from .store import Store
from .weekly import WeeklyReviews
from .weekly_api import weekly_routes
from .worker import run_worker


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    configuration = Configuration(config)
    config = configuration.startup(config)
    settings_lock = Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal config
        store = Store(config.data_dir)
        config = Settings.model_validate(config.model_dump() | store.runtime_settings())
        async with httpx.AsyncClient(
            timeout=config.inference_timeout_seconds, follow_redirects=False
        ) as client:
            memory = Memory(store, config, Inference(config, client))
            memory.retriever.embeddings = Embeddings(config, client)
            app.state.memory = memory
            app.state.weekly = WeeklyReviews(memory)
            weekly_task = asyncio.create_task(app.state.weekly.run())
            app.state.weekly_worker = weekly_task
            app.state.omi = OmiAPI(config, client, store)
            task = None
            if config.worker_enabled:
                store.recover()
                task = asyncio.create_task(run_worker(memory))
            app.state.worker = task
            embedding_task = None
            if config.worker_enabled and memory.retriever.set_id:
                embedding_task = asyncio.create_task(
                    run_embedding_worker(
                        store,
                        memory.retriever.embeddings,
                        memory.retriever.set_id,
                    )
                )
            app.state.embedding_worker = embedding_task
            try:
                yield
            finally:
                for running in (task, embedding_task, weekly_task):
                    if running:
                        running.cancel()
                        with suppress(asyncio.CancelledError):
                            await running

    app = FastAPI(
        title="Omi Local",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def label_no_store(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/v1/labels", "/v1/configuration")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(label_router)
    app.include_router(importance_router)

    @app.exception_handler(LabelError)
    async def label_error(request: Request, exc: LabelError) -> Response:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    app.add_middleware(RedactForwardingToken)

    def memory() -> Memory:
        return app.state.memory

    app.include_router(forwarding_routes(memory))
    app.include_router(decision_routes(memory))
    app.include_router(inbox_router(memory))

    @app.exception_handler(OmiAPIError)
    async def omi_error(request: Request, exc: OmiAPIError) -> Response:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.get("/v1/integrations/omi")
    def omi_status() -> dict:
        return app.state.omi.status()

    @app.post("/v1/integrations/omi/import")
    async def omi_import(body: ImportRequest) -> dict:
        result = await app.state.omi.import_page(body)
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
            "weekly_worker_running": not app.state.weekly_worker.done(),
            "worker_running": bool(app.state.worker and not app.state.worker.done()),
            "meeting_max_minutes": config.meeting_max_minutes,
            "hint_policy": config.hint_policy.model_dump(),
            "capture_paused": config.capture_paused,
            "inference": getattr(memory().inference, "health", {}),
            **memory().store.observations(),
            "embeddings": memory().retriever.status()
            | {
                "worker_running": bool(
                    app.state.embedding_worker and not app.state.embedding_worker.done()
                ),
            },
        }

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

    def activate_settings(revised: Settings, changes: dict) -> None:
        nonlocal config
        config = revised
        memory().settings = revised
        memory().inference.settings = revised
        app.state.omi.key = revised.omi_api_key.get_secret_value().strip()
        if "timezone" in changes:
            memory().store.save_decision_timezone(config.timezone)
            memory().store.invalidate_diaries()
        if {"audio_retention_days", "transcript_retention_days"} & changes.keys():
            memory().store.apply_retention(
                config.audio_retention_days, config.transcript_retention_days
            )

    @app.get("/v1/configuration")
    def get_configuration() -> dict:
        with settings_lock:
            return configuration.view(config)

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
            memory().store.save_settings({
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
                revised = Settings.model_validate(config.model_dump() | changes)
            except ValidationError as exc:
                raise HTTPException(422, "Invalid setting value") from exc
            memory().store.save_settings(changes)
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
    ) -> dict:
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
            row = memory().store.ingest(audio_id, device_id, started_at, data, duration)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return public_source(row)

    @app.get("/v1/audio/{audio_id}")
    def audio(audio_id: UUID) -> FileResponse:
        row = memory().store.get_audio(str(audio_id))
        if row is None or not row["audio_available"] or not Path(row["path"]).is_file():
            raise HTTPException(404, "Audio chunk not found")
        return FileResponse(row["path"], media_type="audio/wav", filename=f"{audio_id}.wav")

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
            if row["state"] not in ("blocked", "queued") or not row["audio_available"]:
                raise HTTPException(409, "Only pending recordings can retry")
            if not Path(row["path"]).is_file():
                raise HTTPException(409, "Restore the missing audio file before retrying")
            db.execute(
                "UPDATE audio SET state='queued',retry_at='',error=NULL,error_code=NULL WHERE id=?",
                (str(audio_id),),
            )
        return {"id": str(audio_id), "state": "queued"}

    @app.get("/v1/sources/{source_id}")
    def source(source_id: UUID) -> dict:
        row = memory().store.get_audio(str(source_id))
        if row is None:
            raise HTTPException(404, "Source unavailable")
        return public_source(row)

    @app.get("/v1/transcripts")
    def transcripts(
        day: date | None = None,
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        start, end = memory().day_window(day) if day else (None, None)
        rows, total = memory().store.transcripts(start, end, q, limit, offset)
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
        data, gaps = render_window(rows, start, stop)
        return Response(
            data,
            media_type="audio/wav",
            headers={"X-Omi-Audio-Gaps": str(len(gaps)), "Cache-Control": "no-store"},
        )

    @app.post("/v1/recaps/{day}")
    async def recap(day: date) -> dict:
        return await memory().recap(day)

    @app.get("/v1/recaps/{day}")
    def cached_recap(day: date) -> dict:
        return memory().cached_recap(day)

    @app.get("/v1/diary")
    def diary() -> dict:
        return {
            "items": [
                {key: row.get(key) for key in ("day", "summary", "generated_at", "pending_chunks")}
                for row in memory().store.diaries()
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
        content = json.loads(record["content"]) if record else memory().cached_recap(day)
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

    app.include_router(weekly_routes(lambda: app.state.weekly))

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
