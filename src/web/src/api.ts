import { t } from "./i18n";
import { profileRequestOptions, profileResourceUrl, profileScope, type ProfileScope } from "./profiles";
import { isManagedOrigin, managedMutationHeaders } from "./managedService";

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
  const headers = managedMutationHeaders(path, options.method, prepared.headers);
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
      isManagedOrigin() ? t("The Daythread service could not be reached. Check your connection and try again.")
        : t("The local backend could not be reached. Check your connection and try again."),
      0,
    );
  }
  if (!response.ok) {
    if (response.status === 401 && isManagedOrigin())
      window.dispatchEvent(new Event("daythread:session-expired"));
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
          ? isManagedOrigin() ? t("Your model is unavailable. Check your account’s inference settings, then try again.")
            : t("Your local model is unavailable. Check the STT / LLM service, then try again.")
          : t("The request could not be completed ({value0}). Please try again.", { value0: response.status });
    throw new ApiError(message, response.status);
  }
  const result = response.status === 204 ? undefined : await response.json();
  prepared.signal?.throwIfAborted();
  return result as T;
}

export function post<T>(path: string, data: unknown = {}): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(data) });
}

// Fetch does not expose upload byte progress. Keep the same profile and abort
// boundaries while using XHR for recording transfers.
export function uploadRecording<T>(
  path: string,
  body: Blob,
  onProgress: (sent: number) => void,
  signal: AbortSignal,
  scope: ProfileScope = profileScope(),
): Promise<T> {
  const prepared = profileRequestOptions(path, { signal }, scope);
  return new Promise((resolve, reject) => {
    const activeSignal = prepared.signal!;
    if (activeSignal.aborted) { reject(activeSignal.reason); return; }
    const xhr = new XMLHttpRequest();
    const cleanup = () => activeSignal.removeEventListener("abort", abort);
    const abort = () => { xhr.abort(); cleanup(); reject(activeSignal.reason); };
    xhr.open("PUT", path);
    managedMutationHeaders(path, "PUT", prepared.headers).forEach((value, name) => xhr.setRequestHeader(name, value));
    xhr.setRequestHeader("Content-Type", "audio/wav");
    xhr.upload.onprogress = (event) => onProgress(Math.min(body.size, event.loaded));
    xhr.onerror = () => {
      cleanup();
      reject(new ApiError(isManagedOrigin() ? t("The Daythread service could not be reached. Check your connection and try again.")
        : t("The local backend could not be reached. Check your connection and try again."), 0));
    };
    xhr.onload = () => {
      cleanup();
      if (activeSignal.aborted) { reject(activeSignal.reason); return; }
      let result: unknown;
      try { result = JSON.parse(xhr.responseText); } catch { /* Invalid receipt is rejected below. */ }
      if (xhr.status < 200 || xhr.status >= 300) {
        if (xhr.status === 401 && isManagedOrigin()) window.dispatchEvent(new Event("daythread:session-expired"));
        const detail = (result as { detail?: unknown } | undefined)?.detail;
        reject(new ApiError(typeof detail === "string" ? detail
          : t("The request could not be completed ({value0}). Please try again.", { value0: xhr.status }), xhr.status));
      } else if (result === undefined) {
        reject(new ApiError(t("The server returned an invalid upload receipt. Retry to confirm this part."), xhr.status));
      } else resolve(result as T);
    };
    activeSignal.addEventListener("abort", abort, { once: true });
    xhr.send(body);
  });
}

export function audioUrl(id: string): string {
  return profileResourceUrl(`/v1/audio/${encodeURIComponent(id)}`);
}

export function rewindAudioUrl(minutes: number, end: string): string {
  return profileResourceUrl(`/v1/rewind/audio?${new URLSearchParams({ minutes: String(minutes), end })}`);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? t(error.message)
    : t("Something went wrong. Please try again.");
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
