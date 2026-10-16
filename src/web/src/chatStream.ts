import { profileRequestOptions } from "./profiles";
import { ApiError } from "./api";
import type { ChatMessage, Evidence, Source } from "./types";

export type ChatResult = {
  answer: string;
  sources: Source[];
  session_id: string;
  message_id: string;
  user_message_id: string;
  evidence: Evidence;
  retrieval_details?: ChatMessage["retrieval_details"];
};

export type ChatProgress =
  | { type: "status"; stage: "searching" | "expanding" | "generating" }
  | { type: "delta"; text: string };

export function exchangeMessages(result: ChatResult, question: string): ChatMessage[] {
  const created_at = result.evidence.generated_at;
  return [
    { id: result.user_message_id, role: "user", content: question, sources: [], created_at },
    { id: result.message_id, role: "assistant", content: result.answer, sources: result.sources,
      evidence: result.evidence, retrieval_details: result.retrieval_details, created_at },
  ];
}

export async function streamChat(
  body: unknown, onProgress: (event: ChatProgress) => void, signal: AbortSignal,
): Promise<ChatResult> {
  const options = profileRequestOptions("/v1/chat/stream", {
    method: "POST", signal,
    headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
    body: JSON.stringify(body),
  });
  options.signal?.throwIfAborted();
  let response: Response;
  try {
    response = await fetch("/v1/chat/stream", options);
    options.signal?.throwIfAborted();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("The local backend could not be reached. Check your connection and try again.", 0);
  }
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch { /* Non-JSON server error. */ }
    throw new ApiError(typeof detail === "string" ? detail : "The answer could not be started. Please try again.", response.status);
  }
  if (!response.headers.get("content-type")?.includes("application/x-ndjson")) {
    throw new ApiError("The answer response was invalid. Please try again.", 502);
  }
  if (!response.body) throw new ApiError("Streaming is unavailable in this browser.", 0);
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let pending = "", bytes = 0, textLength = 0;
  function event(line: string): ChatResult | undefined {
    if (!line.trim()) return;
    let value;
    try { value = JSON.parse(line); } catch {
      throw new ApiError("The answer response was invalid. Please try again.", 502);
    }
    if (!value || typeof value !== "object") throw new ApiError("The answer response was invalid. Please try again.", 502);
    if (value.type === "done") {
      if (!value.result || typeof value.result.answer !== "string" || typeof value.result.session_id !== "string"
        || typeof value.result.message_id !== "string" || typeof value.result.user_message_id !== "string"
        || typeof value.result.evidence?.generated_at !== "string" || !Array.isArray(value.result.sources)) {
        throw new ApiError("The answer response was invalid. Please try again.", 502);
      }
      return value.result as ChatResult;
    }
    if (value.type === "error") throw new ApiError(value.message || "The answer could not be completed.", value.status || 502);
    if (value.type === "delta" && typeof value.text === "string") {
      textLength += value.text.length;
      if (textLength > 60000) throw new ApiError("The answer exceeded its text limit.", 502);
      onProgress(value);
    }
    else if (value.type === "status" && ["searching", "expanding", "generating"].includes(value.stage)) onProgress(value);
    else if (value.type !== "heartbeat") throw new ApiError("The answer response was invalid. Please try again.", 502);
  }
  try {
    while (true) {
      const next = await reader.read();
      options.signal?.throwIfAborted();
      bytes += next.value?.byteLength ?? 0;
      if (bytes > 16_000_000) throw new ApiError("The answer response exceeded its limit.", 502);
      pending += decoder.decode(next.value, { stream: !next.done });
      let newline: number;
      while ((newline = pending.indexOf("\n")) >= 0) {
        const result = event(pending.slice(0, newline));
        pending = pending.slice(newline + 1);
        if (result) return result;
      }
      if (next.done) {
        if (pending.trim()) { const result = event(pending); if (result) return result; }
        throw new ApiError("The answer was interrupted. Please try again.", 502);
      }
    }
  } catch (error) {
    if (error instanceof ApiError || (error instanceof DOMException && error.name === "AbortError")) throw error;
    throw new ApiError("The answer was interrupted. Please try again.", 502);
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
