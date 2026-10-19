"""Selective daily synthesis can omit noise without losing the recording archive."""

import pytest
from test_recaps import FakeInference, insert
from test_recaps import client as client

from omi_local.recaps import NO_CONTENT


@pytest.mark.parametrize("empty_summary", [NO_CONTENT, f" \n{NO_CONTENT}\n "])
def test_no_worthwhile_daily_content_is_cached_without_publishing_a_fake_highlight(
    client, empty_summary
):
    class SelectiveDaily(FakeInference):
        async def summarize(self, sources, purpose):
            self.calls.append((sources, purpose))
            if purpose.startswith("a readable diary recap"):
                return empty_summary
            return f"## Coffee\nWe discussed coffee. [{sources[0]['id']}]"

    memory = client.app.state.memory
    memory.inference = SelectiveDaily()
    original = "Would you like coffee? Yes, please."
    key = insert(client, text=original, kind="manual_upload")
    result = client.post("/v1/recaps/2026-10-16").json()

    assert result["summary"] is None
    assert result["no_recap_content"] is True
    assert result.get("output_id") is None and result.get("evidence") is None
    assert {source["id"] for source in result["sources"]} == {key}
    assert result["included_through"]
    assert memory.store.get_audio(key)["text"] == original
    assert memory.store.notifications_after(0)["notifications"] == []
    again = client.post("/v1/recaps/2026-10-16").json()
    assert again["summary"] is None and again["no_recap_content"] is True
    assert len(memory.inference.calls) == 2  # Cached selection, not repeated model calls.


def test_relevance_policy_reaches_both_preparation_and_daily_synthesis(client):
    insert(client, text="Send the renewal before Friday. We also chatted about coffee.",
           kind="manual_upload")
    client.post("/v1/recaps/2026-10-16")
    purposes = [purpose for _, purpose in client.app.state.memory.inference.calls]
    assert len(purposes) == 2
    # This checks the request contract; semantic precision still depends on the chosen model.
    for purpose in purposes:
        assert "lasting relevance" in purpose
        assert "Do not fill a quota" in purpose
        assert "retain every unique detail" not in purpose
    assert "fleeting conversational requests" in purposes[0]
    assert "only topics with useful supported substance" in purposes[1]
