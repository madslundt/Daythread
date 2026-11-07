"""Bounded foundation receipts; a passing fixture panel never implies universal accuracy."""

import re
from urllib.parse import urlparse

FOUNDATION_CHECKS = {
    "development_accuracy", "consumer_profile", "negative_and_quiet_speech_controls",
    "cold_and_cross_history_repeat", "long_recording_content_review", "reserved_test_nonregression",
}
PRODUCER_FIELDS = {
    "model_sha256": r"[a-f0-9]{64}", "snapshot": r"[^\s\x00-\x1f\x7f]{1,200}",
    "runtime": r"[^\s\x00-\x1f\x7f]{1,200}", "runtime_commit": r"[a-f0-9]{7,40}",
    "homecompute_revision": r"[a-f0-9]{40}", "image_id": r"sha256:[a-f0-9]{64}",
    "device": r"[^\s\x00-\x1f\x7f]{1,200}",
}
PIN_SETTINGS = {
    "model_sha256": "recording_transcription_expected_model_sha256",
    "snapshot": "recording_transcription_expected_model_snapshot",
    "runtime": "recording_transcription_expected_runtime",
    "runtime_commit": "recording_transcription_expected_runtime_commit",
    "homecompute_revision": "recording_transcription_expected_homecompute_revision",
    "image_id": "recording_transcription_expected_image_id",
    "device": "recording_transcription_expected_device",
}
FIXED_PROFILE = {
    "stt_model": "whisper", "stt_chunk_seconds": 120, "stt_max_gain_db": 0,
    "stt_language_candidates": ["da", "en"], "stt_filter_speech": False,
    "stt_preserve_quiet": True, "stt_boundary_search_seconds": 5,
    "stt_vocabulary": [], "stt_vocabulary_mode": "off", "stt_routes": {},
    "stt_segment_timestamps": True, "alignment_enabled": False,
    "stt_enhancement_mode": "on_empty", "stt_enhancement_timeout_seconds": 15,
    "inference_timeout_seconds": 900, "recording_transcription_enabled": True,
    "recording_transcription_alignment_enabled": True,
    "recording_transcription_max_seconds": 1200,
    "recording_transcription_max_bytes": 40_000_000,
    "recording_transcription_timeout_seconds": 1800,
}


def exact_keys(value: object, names: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("Invalid foundation qualification " + label)
    return value


def digest_value(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def validate_foundation(value: dict, legacy_checks: set[str], modules: set[str]) -> dict:
    required = legacy_checks | FOUNDATION_CHECKS
    if (type(value.get("schema_version")) is not int or value.get("schema_version") != 2
            or value.get("status") != "qualified_transcription_foundation"
            or value.get("scope") != "bounded_transcription_profile"
            or value.get("accuracy_status") != "DECLARED_FIXTURES_AND_PROFILE_ONLY"):
        raise ValueError("Completed bounded foundation qualification is required")
    checks = exact_keys(value.get("checks"), required, "checks")
    evidence = exact_keys(value.get("evidence_sha256"), required, "evidence digests")
    if any(checks[name] != "passed" or not digest_value(evidence[name]) for name in required):
        raise ValueError("Pending or failed foundation qualification cannot deploy")
    producer = exact_keys(value.get("producer"), set(PRODUCER_FIELDS), "producer")
    if any(not isinstance(producer[name], str) or not re.fullmatch(pattern, producer[name])
           for name, pattern in PRODUCER_FIELDS.items()):
        raise ValueError("Invalid foundation qualification producer pins")
    if (value.get("source_revision") != producer["homecompute_revision"]
            or value.get("image_id") != producer["image_id"]):
        raise ValueError("Foundation qualification producer identities differ")
    hashes = exact_keys(value.get("deployment_module_hashes"), modules, "deployment source hashes")
    if not all(digest_value(item) for item in hashes.values()):
        raise ValueError("Invalid foundation qualification source hash")
    asr_hashes = exact_keys(value.get("qualified_asr_hashes"), {
        "transcription", "audio", "stt_result", "stt_producer", "transcription_filter",
        "recording_transcription_client", "speech_processing", "evaluate_audio",
    }, "qualified ASR source hashes")
    if not all(digest_value(item) for item in asr_hashes.values()):
        raise ValueError("Invalid qualified ASR source hash")
    if not isinstance(value.get("consumer_client_revision"), str) or not re.fullmatch(
        r"[a-f0-9]{40}", value["consumer_client_revision"],
    ):
        raise ValueError("Qualified consumer client revision is required")
    profile = exact_keys(value.get("profile"), set(FIXED_PROFILE) | {
        "stt_base_url", "recording_transcription_base_url",
    }, "profile")
    def matches(actual, expected):
        if type(expected) in (int, float):
            return type(actual) in (int, float) and actual == expected
        return type(actual) is type(expected) and actual == expected

    if any(not matches(profile[name], expected) for name, expected in FIXED_PROFILE.items()):
        raise ValueError("Foundation profile differs from the qualified gain-zero profile")
    url = profile["recording_transcription_base_url"]
    parsed = urlparse(url) if isinstance(url, str) else None
    if (not parsed or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/recording-stt/v1")
            or profile["stt_base_url"] != url):
        raise ValueError("Foundation profile must reuse the existing private recording gateway")
    if value.get("credential_policy") != "reuse_existing_recording_role_key":
        raise ValueError("Foundation profile requires private recording role-key reuse")
    target = exact_keys(value.get("settings_target"), {"relative_path", "sha256"},
                        "settings target")
    name = target["relative_path"]
    if (not isinstance(name, str) or name.startswith("/") or ".." in name.split("/")
            or name.split("/")[-1] != ".private-settings.json"
            or any(ord(char) < 32 for char in name) or not digest_value(target["sha256"])):
        raise ValueError("Invalid CAS-pinned private settings target")
    bounds = {"max_audio_seconds": 1200, "max_audio_bytes": 40_000_000,
              "backend_timeout_seconds": 600}
    if value.get("bounds") != bounds:
        raise ValueError("Foundation qualification needs the tested bounded request limits")
    if value.get("reserved_reference_exposure") != "preparation_exposed_before_first_asr":
        raise ValueError("Reserved-reference preparation exposure must remain explicit")
    limitations = value.get("accuracy_limitations")
    if (not isinstance(limitations, list) or not 1 <= len(limitations) <= 32
            or any(not isinstance(item, str) or not item.strip() or len(item) > 2000
                   for item in limitations)):
        raise ValueError("Explicit bounded accuracy limitations are required")
    return {key: value[key] for key in (
        "schema_version", "status", "scope", "accuracy_status", "source_revision", "image_id",
        "producer", "consumer_client_revision", "qualified_asr_hashes", "deployment_module_hashes",
        "profile", "credential_policy", "settings_target", "bounds", "accuracy_limitations",
        "reserved_reference_exposure",
    )} | {"checks": dict(sorted(checks.items())), "evidence_sha256": dict(sorted(evidence.items()))}


def verify_source_hashes(value: dict, deployed: dict, qualified_asr: dict | None = None) -> None:
    if value["status"] != "qualified_transcription_foundation":
        return
    if value["deployment_module_hashes"] != deployed:
        raise ValueError("Foundation qualification deployment source differs")
    for name, expected in value["qualified_asr_hashes"].items():
        if name in deployed and deployed[name] != expected:
            raise ValueError("Foundation qualification ASR source differs")
    if qualified_asr is not None and value["qualified_asr_hashes"] != qualified_asr:
        raise ValueError("Foundation qualification ASR/scoring source differs")


def runtime_asr_hashes(value: dict) -> dict:
    if value["status"] != "qualified_transcription_foundation":
        return {}
    return {name: sha for name, sha in value["qualified_asr_hashes"].items()
            if name != "evaluate_audio"}
