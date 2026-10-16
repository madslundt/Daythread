import { navigatePath, subscribeNavigation } from "./routing";
import { useCallback, useEffect, useState } from "react";
import { errorMessage, request } from "./api";
import { Icon } from "./Icon";
import { OmiIntegration } from "./OmiIntegration";
import { SettingsNavigation, sectionFromPath, matchesSettings, type SettingsSection } from "./SettingsNavigation";
import "./settings.css";
import { Configuration } from "./Configuration";
import type { Settings as SettingsData } from "./types";
import { useResource } from "./useResource";
import { ErrorState, Loading } from "./UI";
import { parseThemePreference, setThemePreference, useThemePreference } from "./theme";

type NumberKey = {
  [K in keyof SettingsData]-?: SettingsData[K] extends number ? K : never;
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
    label: "Time between important flags",
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
    detail: "Shorter meeting gestures are ignored; recordings are kept. Cannot exceed the auto-stop duration.",
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
    label: "Automatically end a meeting after",
    options: [1, 5, 15, 30, 60, 90, 120, 180, 240, 360, 480],
    unit: "minutes",
    detail: "End a meeting gesture if you forget to stop it. Cannot be shorter than the minimum meeting length.",
  },
];
const retentionOptions = [0, 1, 7, 14, 30, 90, 180, 365, 730, 1825];
const editableSettings: (keyof SettingsData)[] = [
  "timezone", "summary_language", "meeting_max_minutes", "meeting_min_seconds",
  "meeting_context_seconds", "marker_context_seconds", "marker_after_seconds",
  "marker_cooldown_seconds", "recap_hour", "recap_interval_minutes", "recap_quiet_minutes", "capture_paused",
  "audio_retention_days", "transcript_retention_days",
];
const serverHiddenFields = [...editableSettings, "language", "audio_forwarding_enabled"];
const languageNames = new Intl.DisplayNames(["en"], { type: "language" });
// ISO 639-1 choices for summaries and optional transcript translations.
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
  const [configurationRevision, setConfigurationRevision] = useState(0);
  const [serverDirty, setServerDirty] = useState(false);
  const [section, setSection] = useState<SettingsSection>(sectionFromPath);
  const [search, setSearch] = useState("");
  const [serverMatches, setServerMatches] = useState<{ query: string; count: number }>();
  const query = search.trim();
  const onSearchResults = useCallback((count: number) => setServerMatches({ query: search, count }), [search]);
  function selectSection(next: SettingsSection) {
    setSearch("");
    setSaved(false);
    setSection(next);
    navigatePath(`/settings/${next}`);
  }
  useEffect(() => {
    const sync = () => { setSection(sectionFromPath()); setSearch(""); };
    return subscribeNavigation(sync);
  }, []);
  const show = (group: string, category: SettingsSection) => query
    ? matchesSettings(group, query) : section === category;
  const showServer = query ? serverMatches?.query !== search || serverMatches.count !== 0 : section === "server";
  const preferencesVisible = show("appearance", "general") || show("daily", "general")
    || show("gestures", "gestures") || show("storage", "storage");
  const noMatches = query && !["appearance", "daily", "gestures", "storage", "connections"].some((group) => matchesSettings(group, query))
    && serverMatches?.query === search && serverMatches.count === 0;
  const dirty = Boolean(draft && resource.data && editableSettings.some((key) => draft[key] !== resource.data?.[key]));
  useEffect(() => {
    if (resource.data) setDraft(resource.data);
  }, [resource.data]);
  useEffect(() => {
    if (!dirty && !serverDirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, serverDirty]);
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
      setConfigurationRevision((value) => value + 1);
      window.dispatchEvent(new Event("omi:settings-changed"));
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
    <div className="settings-content settings-layout">
      <SettingsNavigation section={section} search={search} onSearch={setSearch} onSelect={selectSection}
        preferencesDirty={dirty} serverDirty={serverDirty} />
      <div className="settings-panels">
      {dirty && !preferencesVisible ? <div className="notice">
        <span>You have unsaved preferences in another category.</span>
        <button type="button" className="text-button" onClick={() => selectSection("general")}>Review preferences</button>
      </div> : null}
      {query ? <div className="settings-section-heading"><h2>Search results</h2>
        <p>Settings matching “{query}” across all categories.</p></div> : null}
      {noMatches ? <div className="settings-empty" role="status"><Icon name="search" size={28} />
        <h2>No settings found</h2><p>Try “language”, “audio”, “recap”, or “model”.</p>
        <button type="button" className="button" onClick={() => setSearch("")}>Clear search</button></div> : null}
      <section hidden={!show("appearance", "general")}>
      {!query ? <div className="settings-section-heading"><h2>Daily preferences</h2>
        <p>Make Omi fit your routine, from appearance to your daily recap.</p></div> : null}
      <fieldset>
        <legend>Appearance <span className="settings-badge">Saves automatically</span></legend>
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
      </section>
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : null}
      {!draft ? (
        resource.loading ? (
          <Loading>Loading settings…</Loading>
        ) : null
      ) : (
        <form id="preferences-form" className="settings-form" onSubmit={save} aria-busy={busy}>
          <fieldset disabled={busy} hidden={!show("daily", "general")}>
            <legend>Recording & daily recap</legend>
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
              <div className="setting-field">
                <span>Transcription</span>
                <small>Spoken languages are detected automatically. Original transcripts keep the languages used in the conversation.</small>
              </div>
              <label className="setting-field" htmlFor="summary-language">
                <span id="summary-language-label">Summary language</span>
                <select
                  id="summary-language"
                  value={draft.summary_language}
                  aria-labelledby="summary-language-label"
                  aria-describedby="summary-language-hint"
                  onChange={(event) => update("summary_language", event.target.value)}
                >
                  <option value="auto">Automatic / follow source language</option>
                  {!languageCodes.includes(draft.summary_language) && draft.summary_language !== "auto" ? (
                    <option value={draft.summary_language}>{languageLabel(draft.summary_language)} (current value)</option>
                  ) : null}
                  {languages.map(({ value, label }) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
                <small id="summary-language-hint">
                  Choose the language for recaps, summaries, and optional transcript translations. Automatic follows the source language. Transcripts are translated only when you click Translate.
                </small>
              </label>
              <p className="caption">Recaps and decision proposals update automatically as transcripts become available, including recordings added to earlier days.</p>
              <label className="setting-field" htmlFor="recap-quiet">
                <span id="recap-quiet-label">Wait after speech before including a conversation</span>
                <NumberSelect id="recap-quiet" value={draft.recap_quiet_minutes ?? 5}
                  options={[1, 2, 5, 10, 15, 30]} onChange={(value) => update("recap_quiet_minutes", value)}
                  label={(minutes) => durationLabel(minutes * 60)} />
                <small id="recap-quiet-hint">Keep the whole current conversation out until it finishes. A pause can end a conversation; missing uploads are not treated as observed silence. Original audio, including silence and noise, follows your retention settings.</small>
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
          <fieldset disabled={busy} hidden={!show("gestures", "gestures")}>
            <legend>Flags & meetings</legend>
            <p className="muted settings-description">
              Mark an important moment or a meeting to help Omi understand nearby speech.
              These gestures provide context; they do not trim your recordings.
            </p>
            {[{ title: "Important flags", fields: hintFields.slice(0, 3) },
              { title: "Meeting gestures", fields: hintFields.slice(3) }].map((group) => <div className="settings-subgroup" key={group.title}>
            <h3>{group.title}</h3>
            <div className="settings-grid">
              {group.fields.map((field) => (
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
            </div></div>)}
          </fieldset>
          <fieldset disabled={busy} hidden={!show("storage", "storage")}>
            <legend>Storage & privacy</legend>
            <p className="settings-description muted">Choose how long recordings stay in your archive. Audio and transcripts have separate schedules.</p>
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
              This app has no sign-in. Anyone who can reach your server
              address can access this archive.
            </p>
          </fieldset>
        </form>
      )}
      <div hidden={!showServer}>
        <Configuration search={search} refreshRevision={configurationRevision} onDirtyChange={setServerDirty}
          onSearchResults={onSearchResults}
          onSaved={() => { setConnectionRevision((value) => value + 1); onSaved(); }}
          hiddenFields={serverHiddenFields} />
      </div>
      <div hidden={!show("connections", "connections")}>
        <OmiIntegration refreshRevision={connectionRevision} search={search}
          onConfigure={() => { setSection("server"); setSearch("Omi connections"); }}
          onSaved={() => { setConfigurationRevision((value) => value + 1); onSaved(); }} />
      </div>
          {error ? <ErrorState message={error} /> : null}
          <div hidden={!preferencesVisible || (!dirty && !busy && !saved)} className={`settings-actions${dirty || busy ? " has-changes" : ""}`}>
            <p className="caption settings-save-status" role="status">
              {busy ? "Saving your preferences…" : saved ? "Preferences saved on your server." : dirty ? "Unsaved preferences." : "Your preferences are up to date."}
            </p>
            {dirty ? <button type="button" className="button" disabled={busy} onClick={() => {
              setDraft(resource.data);
              setError("");
              setSaved(false);
            }}>Discard changes</button> : null}
            <button form="preferences-form" className="button dark" disabled={busy || !dirty}>
              <Icon name="check" size={18} />
              {busy ? "Saving…" : "Save preferences"}
            </button>
          </div>
      </div>
    </div>
  );
}
