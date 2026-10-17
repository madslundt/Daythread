"""Capability-driven vendor endpoints shared by all archive profiles."""

from fastapi import APIRouter, HTTPException

from .plaud_api import EXPORT_NAMESPACE, PlaudExport, normalize_plaud_export
from .service import public_source
from .vendor_catalog import VENDORS, vendor_definition, vendor_status
from .vendor_imports import TranscriptArchive, VendorImportRequest, VendorPage


def vendor_routes(settings, runtime):
    router = APIRouter(prefix="/v1/vendors")

    def definition(vendor_id: str):
        try:
            return vendor_definition(vendor_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    def retained(result: dict) -> dict:
        store = runtime().memory.store
        config = settings()
        store.apply_retention(config.audio_retention_days, config.transcript_retention_days)
        result["items"] = [
            public_source(row)
            for item in result["items"]
            if (row := store.get_audio(item["id"])) is not None
        ]
        return result

    @router.get("")
    def catalog() -> dict:
        return {"items": [vendor_status(vendor, settings()) for vendor in VENDORS]}

    @router.get("/{vendor_id}")
    def status(vendor_id: str) -> dict:
        return vendor_status(definition(vendor_id), settings())

    @router.post("/{vendor_id}/import")
    async def import_page(vendor_id: str, body: VendorImportRequest) -> dict:
        vendor = definition(vendor_id)
        if "transcript_import" not in vendor.capabilities:
            raise HTTPException(422, "This vendor does not support cloud transcript import")
        return retained(await runtime().vendors[vendor_id].import_page(body))

    @router.post("/plaud/import-export")
    def plaud_export(body: PlaudExport) -> dict:
        try:
            recording = normalize_plaud_export(body)
        except ValueError as exc:
            raise HTTPException(422, "Invalid Plaud transcript export or recording bounds") from exc
        result = TranscriptArchive(
            runtime().memory.store, "plaud", EXPORT_NAMESPACE, "plaud_export"
        ).persist_page(VendorPage([recording]))
        return retained(result)

    return router
