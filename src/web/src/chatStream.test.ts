import { afterEach, describe, expect, it, vi } from "vitest";
import { streamChat } from "./chatStream";

afterEach(() => vi.unstubAllGlobals());

const completed = {
  session_id: "session", answer: "Søren betaler", sources: [], message_id: "assistant",
  user_message_id: "user", evidence: { generated_at: "2026-10-16T12:00:00Z" },
};

function response(events: unknown[], incomplete = false) {
  const bytes = new TextEncoder().encode(events.map((event) => JSON.stringify(event)).join("\n") + (incomplete ? "" : "\n"));
  return new Response(new ReadableStream({ start(controller) {
    // Deliberately split UTF-8 characters and JSON frames at every byte.
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } }), { headers: { "Content-Type": "application/x-ndjson" } });
}

describe("streamed chat protocol", () => {
  it("delivers provisional Unicode text before returning the validated final answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([
      { type: "status", stage: "searching" },
      { type: "delta", text: "Søren " },
      { type: "heartbeat" },
      { type: "delta", text: "betaler" },
      { type: "done", result: completed },
    ])));
    const seen: unknown[] = [];
    const answer = await streamChat({ question: "Who pays?" }, (event) => seen.push(event), new AbortController().signal);
    expect(seen).toEqual([
      { type: "status", stage: "searching" }, { type: "delta", text: "Søren " },
      { type: "delta", text: "betaler" },
    ]);
    expect(answer.answer).toBe("Søren betaler");
  });
  it("rejects an interrupted stream even after text was delivered", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([{ type: "delta", text: "Partial answer" }])));
    await expect(streamChat({}, () => {}, new AbortController().signal)).rejects.toThrow("interrupted");
  });
  it("propagates evidence invalidation without accepting the provisional answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([
      { type: "delta", text: "Old answer" },
      { type: "error", status: 409, message: "Recorded evidence changed; reload and retry" },
    ])));
    await expect(streamChat({}, () => {}, new AbortController().signal)).rejects.toThrow("evidence changed");
  });
  it("supports a complete final frame without a trailing newline", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([
      { type: "done", result: { ...completed, answer: "Answer" } },
    ], true)));
    expect((await streamChat({}, () => {}, new AbortController().signal)).answer).toBe("Answer");
  });
  it("preserves AbortError so stopping does not become a backend failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("Stopped", "AbortError")));
    await expect(streamChat({}, () => {}, new AbortController().signal)).rejects.toMatchObject({ name: "AbortError" });
  });
  it("rejects malformed final messages before they can reach the rendered conversation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([
      { type: "done", result: { session_id: "session", answer: "Answer", sources: [] } },
    ])));
    await expect(streamChat({}, () => {}, new AbortController().signal)).rejects.toThrow("response was invalid");
  });
  it("does not expose an unexpected HTML response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>internal details</html>", { headers: { "Content-Type": "text/html" } })));
    await expect(streamChat({}, () => {}, new AbortController().signal)).rejects.toThrow("response was invalid");
  });
});
