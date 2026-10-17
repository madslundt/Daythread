"""Brand capabilities, independent of recording transport and archive storage."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class VendorDefinition:
    id: str
    label: str
    capabilities: tuple[str, ...]
    setup_url: str
    import_instructions: str
    key_setting: str | None = None


VENDORS = (
    VendorDefinition(
        "manual",
        "Other device",
        ("file_upload",),
        "",
        "Choose an exported audio file and its original recording time.",
    ),
    VendorDefinition(
        "omi",
        "Omi",
        ("file_upload", "local_capture", "transcript_import", "audio_webhook"),
        "https://docs.omi.me/api-reference/introduction",
        "Pair in the iPhone companion, upload exported audio, or import cloud transcripts.",
        "omi_api_key",
    ),
    VendorDefinition(
        "plaud",
        "Plaud",
        ("file_upload", "transcript_export"),
        "https://support.plaud.ai/hc/en-us/articles/51023259082393-Export-recordings-transcripts-and-summaries",
        "Export audio or a transcript (SRT or TXT) from Plaud, then import it here.",
    ),
    VendorDefinition(
        "pocket",
        "Pocket",
        ("file_upload", "transcript_import"),
        "https://docs.heypocketai.com/docs/api",
        "Upload exported audio or import transcripts with your Pocket API key.",
        "pocket_api_key",
    ),
    VendorDefinition(
        "apple",
        "Apple Voice Memos",
        ("file_upload",),
        "https://support.apple.com/guide/iphone/share-a-recording-iph3d6dc359/ios",
        "Share a Voice Memos recording to Files, then choose the exported audio.",
    ),
    VendorDefinition(
        "sony",
        "Sony",
        ("file_upload",),
        "https://www.sony.com/electronics/support",
        "Copy an audio recording from your recorder and choose the exported file.",
    ),
    VendorDefinition(
        "zoom",
        "Zoom recorders",
        ("file_upload",),
        "https://zoomcorp.com/",
        "Copy a WAV or MP3 recording from your recorder and choose the audio file.",
    ),
)


def vendor_definition(vendor_id: str) -> VendorDefinition:
    for vendor in VENDORS:
        if vendor.id == vendor_id:
            return vendor
    raise ValueError("Unknown recording vendor")


def file_vendor(vendor_id: str) -> str:
    if "file_upload" not in vendor_definition(vendor_id).capabilities:
        raise ValueError("Vendor does not support audio file import")
    return vendor_id


def vendor_status(vendor: VendorDefinition, settings) -> dict:
    configured = bool(
        vendor.key_setting and getattr(settings, vendor.key_setting).get_secret_value().strip()
    )
    return {**asdict(vendor), "configured": configured}
