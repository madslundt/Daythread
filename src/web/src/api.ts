import { profileRequestOptions, profileResourceUrl, profileScope, type ProfileScope } from "./profiles";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
  scope: ProfileScope = profileScope(),
): Promise<T> {
  const prepared = profileRequestOptions(path, options, scope);
  const headers = new Headers(prepared.headers);
  if (options.body && !(options.body instanceof Blob) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  prepared.headers = headers;
  prepared.signal?.throwIfAborted();
  let response: Response;
  try {
    response = await fetch(path, prepared);
    prepared.signal?.throwIfAborted();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(
      "The local backend could not be reached. Check your connection and try again.",
      0,
    );
  }
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* Non-JSON server error. */
    }
    const message =
      typeof detail === "string"
        ? detail
        : response.status === 502
          ? "Your local model is unavailable. Check the STT / LLM service, then try again."
          : `The request could not be completed (${response.status}). Please try again.`;
    throw new ApiError(message, response.status);
  }
  const result = response.status === 204 ? undefined : await response.json();
  prepared.signal?.throwIfAborted();
  return result as T;
}

export function post<T>(path: string, data: unknown = {}): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(data) });
}

export function audioUrl(id: string): string {
  return profileResourceUrl(`/v1/audio/${encodeURIComponent(id)}`);
}

export function rewindAudioUrl(minutes: number, end: string): string {
  return profileResourceUrl(`/v1/rewind/audio?${new URLSearchParams({ minutes: String(minutes), end })}`);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

// getRandomValues is available on LAN HTTP origins as well as HTTPS.
export function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
