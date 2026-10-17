import { t } from "./i18n";
import { useEffect, useState } from "react";

export async function copySummary(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return; } catch { /* Try local HTTP fallback. */ }
  }
  // Local home servers can use HTTP, where the Clipboard API is unavailable.
  const previous = document.activeElement;
  const input = document.createElement("textarea");
  input.value = text;
  input.readOnly = true;
  input.style.position = "fixed";
  input.style.left = "-9999px";
  document.body.append(input);
  input.focus();
  input.select();
  try {
    if (!document.execCommand("copy")) throw new Error(t("Clipboard unavailable"));
  } finally {
    input.remove();
    if (previous instanceof HTMLElement) previous.focus();
  }
}

export function CopySummary({ text, label = t("Copy summary"), context }: { text: string; label?: string; context?: string }) {
  const [status, setStatus] = useState<"ready" | "copied" | "failed">("ready");
  useEffect(() => {
    if (status !== "copied") return;
    const timer = window.setTimeout(() => setStatus("ready"), 2000);
    return () => window.clearTimeout(timer);
  }, [status]);
  return <span className="copy-summary">
    <button type="button" className="text-button" aria-label={context ? `${label}: ${context}` : label} onClick={async (event) => {
      event.preventDefault(); event.stopPropagation();
      try { await copySummary(text); setStatus("copied"); } catch { setStatus("failed"); }
    }}>{status === "copied" ? t("Copied") : label}</button>
    <span className={status === "failed" ? "caption" : "sr-only"} role="status">
      {status === "copied" ? t("Summary copied to clipboard.") : status === "failed" ? t("Could not copy. Select the summary text to copy it.") : ""}
    </span>
  </span>;
}
