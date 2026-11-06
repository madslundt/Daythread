import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "assistant_context_deployment", ROOT / "scripts/deploy_assistant_context_homecore.py"
)
DEPLOYMENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOYMENT)


def test_remote_program_compiles_and_keeps_export_disabled():
    compile(DEPLOYMENT.REMOTE, "assistant_context_remote", "exec")
    assert "OMI_ASSISTANT_CONTEXT_ENABLED':'false'" in DEPLOYMENT.REMOTE
    assert "Disabled assistant-context route did not return 404" in DEPLOYMENT.REMOTE
    assert DEPLOYMENT.BASE_RELEASE == "20261106-transcription-fc4a4eeddd4f"
    assert "transcription source hashes changed during provider rollout" in DEPLOYMENT.REMOTE
    assert len(DEPLOYMENT.ASR_MODULE_HASHES) == 7


def test_deployment_payload_uses_exact_published_main(monkeypatch):
    revision = "a" * 40

    def fake_git(_root, *args):
        if args == ("rev-parse", "HEAD"):
            return revision
        if args[:2] == ("status", "--porcelain"):
            return ""
        if args[:2] == ("ls-files", "--"):
            return "\n".join(DEPLOYMENT.FILES)
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return revision + "\trefs/heads/main"
        raise AssertionError(args)

    monkeypatch.setattr(DEPLOYMENT, "_git", fake_git)
    payload = DEPLOYMENT.make_payload(ROOT, "stage")
    assert payload["revision"] == revision
    assert payload["base_image"] == DEPLOYMENT.BASE_IMAGE
    assert payload["token_source"] == "/etc/homecompute/daythread-assistant-context-token"
    assert payload["release"].startswith("20261106-assistant-context-")


def test_deployment_payload_refuses_unpublished_main(monkeypatch):
    revision = "b" * 40

    def fake_git(_root, *args):
        if args == ("rev-parse", "HEAD"):
            return revision
        if args[:2] == ("status", "--porcelain"):
            return ""
        if args[:2] == ("ls-files", "--"):
            return "\n".join(DEPLOYMENT.FILES)
        if args == ("ls-remote", "origin", "refs/heads/main"):
            return "c" * 40 + "\trefs/heads/main"
        raise AssertionError(args)

    monkeypatch.setattr(DEPLOYMENT, "_git", fake_git)
    with pytest.raises(ValueError, match="origin/main"):
        DEPLOYMENT.make_payload(ROOT, "stage")
