import { t } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { errorMessage, request } from "./api";
import { Icon } from "./Icon";
import type { ConfigurationDraft, RouteRow } from "./configurationModel";
import { modelConnectionRequest, type ModelConnectionResult, type ModelProvider } from "./modelConnection";

export function ModelPicker({ id, label, value, onChange, provider, draft, row, revision, description }: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  provider: ModelProvider;
  draft: ConfigurationDraft;
  row?: RouteRow;
  revision: string;
  description?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(false);
  const [custom, setCustom] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [catalog, setCatalog] = useState<{ key: string; models?: string[]; error?: string }>();
  const [testing, setTesting] = useState("");
  const [result, setResult] = useState<{ key: string; value: ModelConnectionResult }>();
  const testController = useRef<AbortController | undefined>(undefined);
  let lookupBody = "", testBody = "", previewError = "";
  try {
    lookupBody = JSON.stringify(modelConnectionRequest(draft, provider, row, false));
    testBody = JSON.stringify(modelConnectionRequest(draft, provider, row));
  } catch (failure) { previewError = errorMessage(failure); }
  // Include the saved revision: a retained write-only key may have just changed.
  const lookupKey = `${revision}:${lookupBody}`;
  const testKey = `${revision}:${testBody}`;
  const current = catalog?.key === lookupKey ? catalog : undefined;
  const models = current?.models || [];
  const loading = active && !previewError && !current;
  const inherited = row ? String(draft.values[`${provider}_model`] || "") : "";
  const selectedModel = value.trim() || inherited.trim();
  const currentResult = result?.key === testKey ? result.value : undefined;

  useEffect(() => {
    if (!root.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { setActive(true); observer.disconnect(); }
    }, { rootMargin: "120px" });
    observer.observe(root.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!active || !lookupBody || previewError) return;
    const controller = new AbortController();
    setCatalog(undefined);
    const timer = window.setTimeout(() => {
      request<{ models: string[] }>("/v1/configuration/models", {
        method: "POST", body: lookupBody, signal: controller.signal,
      }).then((data) => {
        if (!controller.signal.aborted) setCatalog({ key: lookupKey, models: data.models });
      }).catch((failure) => {
        if (!controller.signal.aborted) setCatalog({ key: lookupKey, error: errorMessage(failure) });
      });
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [active, lookupBody, lookupKey, previewError, refresh]);

  useEffect(() => {
    // Editing the connection invalidates any previous result and cancels old work.
    testController.current?.abort();
    setTesting("");
    setResult(undefined);
    return () => testController.current?.abort();
  }, [testKey]);

  async function testConnection() {
    if (testing || !testBody || previewError || !selectedModel) return;
    const controller = new AbortController();
    testController.current = controller;
    setTesting(testKey);
    setResult(undefined);
    try {
      const value = await request<ModelConnectionResult>("/v1/configuration/test", {
        method: "POST", body: testBody, signal: controller.signal,
      });
      if (!controller.signal.aborted) setResult({ key: testKey, value });
    } catch (failure) {
      if (!controller.signal.aborted) setResult({ key: testKey, value: { ok: false, message: errorMessage(failure), latency_ms: 0 } });
    } finally { if (!controller.signal.aborted) setTesting(""); }
  }

  return <div className="setting-field model-picker" ref={root}>
    <label id={`${id}-label`} htmlFor={id}>{label}</label>
    <select id={id} aria-labelledby={`${id}-label`} aria-describedby={`${id}-hint ${id}-catalog`}
      value={custom ? "custom" : row && !value ? "inherit" : value ? `model:${value}` : "empty"}
      onFocus={() => setActive(true)} onChange={(event) => {
        if (event.target.value === "custom") { setCustom(true); return; }
        setCustom(false);
        onChange(event.target.value === "inherit" ? "" : event.target.value.slice(6));
      }}>
      {row ? <option value="inherit">{t("Use default model")}{inherited ? ` (${inherited})` : ""}</option> : null}
      {!row && !value ? <option value="empty" disabled>{t("Choose a model")}</option> : null}
      {value && !models.includes(value) ? <option value={`model:${value}`}>{value} {t("(current)")}</option> : null}
      {models.map((model) => <option key={model} value={`model:${model}`}>{model}</option>)}
      <option value="custom">{t("Custom…")}</option>
    </select>
    {custom ? <label className="model-custom" htmlFor={`${id}-custom`}>
      <span>{t("Custom model name")}</span>
      <input id={`${id}-custom`} value={value} placeholder={row ? t("Leave blank to use the default model") : t("Enter the model name served by your endpoint")}
        autoCapitalize="none" autoComplete="off" spellCheck={false}
        onChange={(event) => onChange(event.target.value)} />
    </label> : null}
    <small id={`${id}-hint`}>{description || "Choose a model from this server, or enter a custom name."}</small>
    <p id={`${id}-catalog`} className={`caption model-catalog-status${current?.error || previewError ? " model-connection-error" : ""}`} role="status">
      {previewError || (loading ? "Looking up available models…" : current?.error ? `${current.error} You can still enter a custom model.` : current?.models ?
        models.length ? `${models.length} ${models.length === 1 ? "model" : "models"} returned by this server. Choose one that supports ${provider === "stt" ? "transcription" : "chat"}.` :
        "This server returned no models. Use Custom to enter a model name." : "Models are looked up from your configured server.")}
    </p>
    <div className="button-row model-connection-actions">
      <button type="button" className="button small" disabled={Boolean(testing) || !selectedModel || Boolean(previewError)} onClick={() => void testConnection()}>
        <Icon name="check" size={16} />{testing ? t("Testing…") : t("Test connection")}
      </button>
      <button type="button" className="text-button" disabled={loading || Boolean(previewError)} onClick={() => { setActive(true); setRefresh((count) => count + 1); }}>
        <Icon name="refresh" size={14} />{t("Refresh models")}{" "}</button>
    </div>
    <small>{t("Tests use your current entries without saving.")}{" "}{provider === "stt" ? t("A short synthetic audio clip is sent; your recordings are not used.") : t("A short test message is sent to the selected model.")}</small>
    {currentResult ? <p className={`notice ${currentResult.ok ? "notice-success" : "notice-error"} model-test-result`} role={currentResult.ok ? "status" : "alert"}>
      {currentResult.message}{currentResult.latency_ms > 0 ? ` (${(currentResult.latency_ms / 1000).toFixed(1)}s)` : ""}
    </p> : null}
  </div>;
}
