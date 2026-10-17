"""Profile-scoped notification feed, APNs registration, and short foreground leases."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

InstallationID = Annotated[str, Path(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")]


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=512, pattern=r"^[a-fA-F0-9]+$")
    environment: Literal["sandbox", "production"]
    active: bool


class Presence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


class Acknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    ids: list[UUID] = Field(max_length=100)


def notification_routes(get_memory) -> APIRouter:
    router = APIRouter(prefix="/v1/notifications")

    @router.get("")
    def feed(after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        return get_memory().store.notifications_after(after, limit)

    @router.get("/status")
    def status():
        service = get_memory().notifications
        service.sender.settings = get_memory().settings
        return {"push_configured": service.sender.configured}

    @router.put("/devices/{installation_id}")
    async def register(installation_id: InstallationID, body: DeviceRegistration, request: Request):
        memory = get_memory()
        # All profile runtimes exist before requests are accepted. No await between
        # removal and registration: one token can receive only the current profile.
        for runtime in request.app.state.profiles.runtimes.values():
            if runtime.memory is not memory:
                runtime.memory.store.unregister_notification_device(
                    installation_id, body.token.lower(),
                )
        memory.store.register_notification_device(
            installation_id, body.token.lower(), body.environment, body.active,
        )
        return {"registered": True}

    @router.post("/devices/{installation_id}/presence")
    def presence(installation_id: InstallationID, body: Presence):
        if not get_memory().store.notification_presence(installation_id, body.active):
            raise HTTPException(status_code=404, detail="Notification device not registered")
        return {"active": body.active, "lease_seconds": 60 if body.active else 0}

    @router.delete("/devices/{installation_id}")
    def unregister(installation_id: InstallationID):
        get_memory().store.unregister_notification_device(installation_id)
        return {"registered": False}

    @router.post("/ack")
    def acknowledge(body: Acknowledgement):
        get_memory().store.acknowledge_notifications(
            body.installation_id, [str(value) for value in body.ids],
        )
        return {"acknowledged": True}

    return router
