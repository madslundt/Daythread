import { useEffect, useState } from "react";
import { errorMessage, post, request } from "./api";
import { Icon } from "./Icon";
import { AudioForwarding } from "./AudioForwarding";
import { Configuration } from "./Configuration";
import type { Settings as SettingsData, Source } from "./types";
import { useResource } from "./useResource";
import { ErrorState, Loading, Notice } from "./UI";
import { parseThemePreference, setThemePreference, useThemePreference } from "./theme";

type NumberKey = {
  [K in keyof SettingsData]: SettingsData[K] extends number ? K : never;
}[keyof SettingsData];
const hintFields: {
  key: NumberKey;
  label: string;
  options: number[];
  unit?: "minutes";
  zeroLabel?: string;
  detail: string;
}[] = [
  {
    key: "marker_cooldown_seconds",
    label: "Important flag cooldown",
    options: [0, 15, 30, 60, 120, 300, 600, 1800, 3600],
    zeroLabel: "No cooldown",
    detail: "Wait before accepting another flag. Extra clicks do not extend the wait.",
  },
  {
    key: "marker_context_seconds",
    label: "Context before a flag",
    options: [0, 15, 30, 60, 120, 180, 300],
    zeroLabel: "No extra context",
    detail: "Surrounding speech to consider before an important flag.",
  },
  {
    key: "marker_after_seconds",
    label: "Context after a flag",
    options: [0, 15, 30, 60, 120, 300, 600],
    zeroLabel: "No extra context",
    detail: "Surrounding speech to consider after an important flag.",
  },
  {
    key: "meeting_min_seconds",
    label: "Minimum meeting length",
    options: [60, 120, 300, 600, 900, 1800, 3600],
    detail: "Shorter hints are ignored; recordings remain. Cannot exceed the auto-stop duration.",
  },
  {
    key: "meeting_context_seconds",
    label: "Context around meetings",
    options: [0, 15, 30, 60, 120, 300, 600],
    zeroLabel: "No extra context",
    detail: "Speech to include before and after the meeting gesture times.",
  },
  {
    key: "meeting_max_minutes",
    label: "Meeting auto stop",
    options: [1, 5, 15, 30, 60, 90, 120, 180, 240, 360, 480],
    unit: "minutes",
    detail: "End a forgotten hint automatically. Cannot be shorter than the minimum meeting length.",
  },
];
const retentionOptions = [0, 1, 7, 14, 30, 90, 180, 365, 730, 1825];
const editableSettings: (keyof SettingsData)[] = [
  "timezone", "language", "meeting_max_minutes", "meeting_min_seconds",
  "meeting_context_seconds", "marker_context_seconds", "marker_after_seconds",
  "marker_cooldown_seconds", "recap_hour", "capture_paused",
  "audio_retention_days", "transcript_retention_days",
];
const languageNames = new Intl.DisplayNames(["en"], { type: "language" });
// ISO 639-1 choices. The local transcription model determines language support.
const languageCodes = (
  "aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy " +
  "da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz " +
  "ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv " +
  "mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt " +
  "qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr " +
  "ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu"
).split(" ");
function languageLabel(code: string) {
  try {
    return languageNames.of(code) || code;
  } catch {
    return code;
  }
}
const languages = languageCodes.map((value) => ({ value, label: languageLabel(value) }))
  .sort((a, b) => a.label.localeCompare(b.label, "en"));
const timezones = typeof Intl.supportedValuesOf === "function"
  ? Intl.supportedValuesOf("timeZone")
  : ["Europe/Copenhagen", "Europe/London", "Europe/Paris", "America/New_York",
    "America/Chicago", "America/Denver", "America/Los_Angeles", "Asia/Tokyo",
    "Asia/Kolkata", "Asia/Shanghai", "Australia/Sydney", "Pacific/Auckland"];
function durationLabel(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0)
    return `${seconds / 3600} ${seconds === 3600 ? "hour" : "hours"}`;
  if (seconds >= 60 && seconds % 60 === 0)
    return `${seconds / 60} ${seconds === 60 ? "minute" : "minutes"}`;
  return `${seconds} ${seconds === 1 ? "second" : "seconds"}`;
}
function retentionLabel(days: number): string {
  return days === 0 ? "Until I remove it" : `${days} ${days === 1 ? "day" : "days"}`;
}
function NumberSelect({ id, value, options, onChange, label, unavailable }: {
  id: string;
  value: number;
  options: number[];
  onChange: (value: number) => void;
  label: (value: number) => string;
  unavailable?: (value: number) => boolean;
}) {
  // Keep exact values saved by another client, even when they are not a preset.
  const values = options.includes(value)
    ? options
    : [...options, value].sort((a, b) => a - b);
  return (
    <select
      id={id}
      value={value}
      aria-labelledby={`${id}-label`}
      aria-describedby={`${id}-hint`}
      onChange={(event) => onChange(Number(event.target.value))}
    >
      {values.map((option) => (
        <option key={option} value={option} disabled={unavailable?.(option)}>
          {label(option)}{options.includes(option) ? "" : " (current value)"}
        </option>
      ))}
    </select>
  );
}

export function Settings({ onSaved }: { onSaved: () => void }) {
  const theme = useThemePreference();
  const resource = useResource<SettingsData>("/v1/settings");
  const [draft, setDraft] = useState<SettingsData>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [connectionRevision, setConnectionRevision] = useState(0);
  const dirty = Boolean(draft && resource.data && editableSettings.some((key) => draft[key] !== resource.data?.[key]));
  useEffect(() => {
    if (resource.data) setDraft(resource.data);
  }, [resource.data]);
  function update<K extends keyof SettingsData>(
    key: K,
    value: SettingsData[K],
  ) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
    setSaved(false);
    setError("");
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft || busy || !dirty) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const changes = Object.fromEntries(editableSettings.map((key) => [key, draft[key]]));
      resource.setData(
        await request<SettingsData>("/v1/settings", {
          method: "PATCH",
          body: JSON.stringify(changes),
        }),
      );
      setSaved(true);
      onSaved();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  const timezoneOptions = [...new Set([
    "UTC", ...timezones, Intl.DateTimeFormat().resolvedOptions().timeZone,
    ...(draft ? [draft.timezone] : []),
  ])].sort();
  return (
    <div className="settings-content">
      <fieldset>
        <legend>Appearance</legend>
        <label className="setting-field" htmlFor="appearance">
          <span id="appearance-label">Theme</span>
          <select
            id="appearance"
            value={theme}
            aria-labelledby="appearance-label"
            aria-describedby="appearance-hint"
            onChange={(event) =>
              setThemePreference(parseThemePreference(event.target.value))
            }
          >
            <option value="system">System</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
          <small id="appearance-hint">Saved on this device. System follows your device’s appearance.</small>
        </label>
      </fieldset>
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : null}
      {!draft ? (
        resource.loading ? (
          <Loading>Loading settings…</Loading>
        ) : null
      ) : (
        <form className="settings-form" onSubmit={save} aria-busy={busy}>
          <fieldset disabled={busy}>
            <legend>Capture & diary</legend>
            <div className="settings-grid">
              <label className="setting-field" htmlFor="timezone">
                <span id="timezone-label">Home timezone</span>
                <select
                  id="timezone"
                  value={draft.timezone}
                  aria-labelledby="timezone-label"
                  aria-describedby="timezone-hint"
                  onChange={(event) => update("timezone", event.target.value)}
                >
                  {timezoneOptions.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone.replaceAll("_", " ").replaceAll("/", " / ")}
                    </option>
                  ))}
                </select>
                <small id="timezone-hint">
                  Dates, diary boundaries, and daily recaps use this timezone.
                </small>
              </label>
              <label className="setting-field" htmlFor="language">
                <span id="language-label">Transcription language</span>
                <select
                  id="language"
                  value={draft.language}
                  aria-labelledby="language-label"
                  aria-describedby="language-hint"
                  onChange={(event) => update("language", event.target.value)}
                >
                  <option value="auto">Automatic / mixed languages</option>
                  {!languageCodes.includes(draft.language) && draft.language !== "auto" ? (
                    <option value={draft.language}>{languageLabel(draft.language)} (current value)</option>
                  ) : null}
                  {languages.map(({ value, label }) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
                <small id="language-hint">
                  Automatic detects the spoken language. Choose a language supported by your local transcription model.
                </small>
              </label>
              <label className="setting-field" htmlFor="recap-hour">
                <span id="recap-hour-label">Daily recap hour</span>
                <NumberSelect
                  id="recap-hour"
                  value={draft.recap_hour}
                  options={Array.from({ length: 24 }, (_, hour) => hour)}
                  onChange={(value) => update("recap_hour", value)}
                  label={(hour) => `${String(hour).padStart(2, "0")}:00`}
                />
                <small id="recap-hour-hint">
                  In your home timezone. The worker begins saving today’s recap at this time and catches up on earlier days.
                </small>
              </label>
              <label className="capture-toggle">
                <input
                  type="checkbox"
                  checked={draft.capture_paused}
                  onChange={(event) =>
                    update("capture_paused", event.target.checked)
                  }
                />
                <span>
                  Pause capture
                  <small>
                    The iPhone checks this setting when connected. Save changes
                    to apply it.
                  </small>
                </span>
              </label>
            </div>
          </fieldset>
          <fieldset disabled={busy}>
            <legend>Approximate hints</legend>
            <p className="muted settings-description">
              Flags and meeting gestures guide interpretation of nearby speech.
              They do not define exact recording boundaries.
            </p>
            <div className="settings-grid">
              {hintFields.map((field) => (
                <label
                  className="setting-field"
                  htmlFor={field.key}
                  key={field.key}
                >
                  <span id={`${field.key}-label`}>{field.label}</span>
                  <NumberSelect
                    id={field.key}
                    value={draft[field.key]}
                    options={field.options}
                    onChange={(value) => update(field.key, value)}
                    label={(value) =>
                      value === 0 && field.zeroLabel
                        ? field.zeroLabel
                        : durationLabel(value * (field.unit === "minutes" ? 60 : 1))
                    }
                    unavailable={
                      field.key === "meeting_min_seconds"
                        ? (value) => value > draft.meeting_max_minutes * 60
                        : field.key === "meeting_max_minutes"
                          ? (value) => value * 60 < draft.meeting_min_seconds
                          : undefined
                    }
                  />
                  <small id={`${field.key}-hint`}>{field.detail}</small>
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset disabled={busy}>
            <legend>Local archive</legend>
            <div className="settings-grid">
              <label className="setting-field" htmlFor="audio-retention">
                <span id="audio-retention-label">Keep original audio</span>
                <NumberSelect
                  id="audio-retention"
                  value={draft.audio_retention_days}
                  options={retentionOptions}
                  label={retentionLabel}
                  onChange={(value) => update("audio_retention_days", value)}
                />
                <small id="audio-retention-hint">Default: 7 days. Expired audio is removed automatically; transcripts have their own retention period.</small>
              </label>
              <label className="setting-field" htmlFor="transcript-retention">
                <span id="transcript-retention-label">Keep transcripts</span>
                <NumberSelect
                  id="transcript-retention"
                  value={draft.transcript_retention_days}
                  options={retentionOptions}
                  label={retentionLabel}
                  onChange={(value) => update("transcript_retention_days", value)}
                />
                <small id="transcript-retention-hint">
                  Default: 30 days. Expiring transcripts also clears derived diary/chat content and related inbox items, including manual edits and history. Shorter retention periods apply to existing recordings when saved.
                </small>
              </label>
            </div>
            <p className="caption access-note">
              This app has no sign-in. Anyone who can reach your home-core
              address can access this archive.
            </p>
          </fieldset>
          {error ? <ErrorState message={error} /> : null}
          <div className={`settings-actions${dirty || busy ? " has-changes" : ""}`}>
            <p className="caption settings-save-status" role="status">
              {busy ? "Saving your preferences…" : saved ? "Settings saved on home-core." : dirty ? "You have unsaved changes." : "Your preferences are up to date."}
            </p>
            {dirty ? <button type="button" className="button" disabled={busy} onClick={() => {
              setDraft(resource.data);
              setError("");
              setSaved(false);
            }}>Discard changes</button> : null}
            <button className="button dark" disabled={busy || !dirty}>
              <Icon name="check" size={18} />
              {busy ? "Saving…" : "Save changes"}
            </button>
          </div>
        </form>
      )}
      <Configuration onSaved={() => { setConnectionRevision((value) => value + 1); onSaved(); }} hiddenFields={editableSettings} />
      <OmiIntegration key={connectionRevision} onSaved={onSaved} />
    </div>
  );
}

type OmiImportResult = {
  imported: number;
  existing: number;
  skipped: number;
  has_more: boolean;
  next_offset: number;
  items: Source[];
};

function OmiIntegration({ onSaved }: { onSaved: () => void }) {
  const resource = useResource<{ configured: boolean }>("/v1/integrations/omi");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<OmiImportResult>();
  const [nextPage, setNextPage] = useState<{ end: string; offset: number }>();
  const unavailable = busy || resource.loading || Boolean(resource.error) || !resource.data?.configured;
  async function importPage(next = false) {
    if (unavailable || (next && !nextPage)) return;
    const page = next && nextPage ? nextPage : { end: new Date().toISOString(), offset: 0 };
    setBusy(true);
    setError("");
    setResult(undefined);
    if (!next) setNextPage(undefined);
    try {
      const imported = await post<OmiImportResult>("/v1/integrations/omi/import", {
        end_date: page.end,
        limit: 25,
        offset: page.offset,
      });
      setResult(imported);
      setNextPage(imported.has_more ? { end: page.end, offset: imported.next_offset } : undefined);
      window.dispatchEvent(new Event("omi:archive-changed"));
      onSaved();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <fieldset>
      <legend>Omi connection</legend>
      <AudioForwarding onSaved={onSaved} />
      <p className="muted settings-description">
        Connect your Omi to the iPhone app over Bluetooth for local audio capture.
        This path does not need an Omi API key.
      </p>
      <p className="muted settings-description">
        Optionally import existing conversations from Omi’s cloud into your local
        archive. Imports include transcripts only, without original audio.
      </p>
      {resource.error ? <ErrorState message={resource.error} retry={resource.refresh} /> : null}
      {resource.loading ? <Loading>Checking Omi API connection…</Loading> : resource.data ? (
        <p className="caption">
          {resource.data.configured
            ? "Omi API is configured on your server. Import up to 25 conversations at a time, newest first."
            : <>To enable cloud import, save your Omi API key in Models, keys &amp; server settings → Omi connections.
              Saved keys stay on home-core and are never displayed here.</>}
        </p>
      ) : null}
      {error ? <ErrorState message={error} /> : null}
      {result ? (
        <Notice tone="success">
          Last page: {result.imported} imported, {result.existing} already in your archive,
          {" "}{result.skipped} skipped.
          {!result.has_more ? " No more conversations in this import." : " More conversations are available."}
        </Notice>
      ) : null}
      <div className="button-row">
        <button type="button" className="button" disabled={unavailable}
          onClick={() => void importPage()}>
          {busy ? "Importing…" : "Import latest conversations"}
        </button>
        {nextPage ? (
          <button type="button" className="button" disabled={unavailable}
            onClick={() => void importPage(true)}>
            Load next 25
          </button>
        ) : null}
        {!resource.loading && !resource.error && !resource.data?.configured ? (
          <button type="button" className="text-button" onClick={resource.refresh}>Check connection again</button>
        ) : null}
      </div>
    </fieldset>
  );
}
