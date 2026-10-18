import { languageCodes } from "./languages";
import { LocationZones } from "./LocationZones";
import { displayLocale, t } from "./i18n";
import { ProfileManagement } from "./ProfileManagement";
import type { Profile } from "./profiles";
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
import { interfaceLanguage, interfaceLanguages, setLanguagePreference, useLanguagePreference, type LanguagePreference } from "./i18n";
import { ManagedModels } from "./ManagedModels";
import { ManagedAccountPanel, ManagedConnections, useManagedAccount } from "./ManagedAccount";
import { Usage } from "./Usage";

const retentionOptions = [0, 1, 7, 14, 30, 90, 180, 365, 730, 1825];
export const editableSettings: (keyof SettingsData)[] = [
  "timezone", "summary_language", "translation_languages", "audio_retention_days", "transcript_retention_days",
];
const serverHiddenFields = [...editableSettings, "language", "translation_languages", "capture_paused",
  "meeting_quiet_minutes", "meeting_max_minutes", "meeting_min_seconds", "meeting_context_seconds", "marker_context_seconds",
  "marker_after_seconds", "marker_cooldown_seconds", "recap_hour", "recap_interval_minutes", "recap_quiet_minutes", "audio_forwarding_enabled"];
export function preferenceChanges(draft: SettingsData, managed: boolean): Partial<SettingsData> {
  return Object.fromEntries(editableSettings
    .filter((key) => !managed || !["audio_retention_days", "transcript_retention_days"].includes(key))
    .map((key) => [key, draft[key]]));
}

function languageLabel(code: string) {
  try {
    return new Intl.DisplayNames([interfaceLanguage()], { type: "language" }).of(code) || code;
  } catch {
    return code;
  }
}
const timezones = typeof Intl.supportedValuesOf === "function"
  ? Intl.supportedValuesOf("timeZone")
  : ["Europe/Copenhagen", "Europe/London", "Europe/Paris", "America/New_York",
    "America/Chicago", "America/Denver", "America/Los_Angeles", "Asia/Tokyo",
    "Asia/Kolkata", "Asia/Shanghai", "Australia/Sydney", "Pacific/Auckland"];
function retentionLabel(days: number): string {
  return days === 0 ? t("Until I remove it")
    : new Intl.NumberFormat(displayLocale(), { style: "unit", unit: "day", unitDisplay: "long" }).format(days);
}
function NumberSelect({ id, value, options, onChange, label, unavailable, disabled }: {
  id: string;
  value: number;
  options: number[];
  onChange: (value: number) => void;
  label: (value: number) => string;
  unavailable?: (value: number) => boolean;
  disabled?: boolean;
}) {
  // Keep exact values saved by another client, even when they are not a preset.
  const values = options.includes(value)
    ? options
    : [...options, value].sort((a, b) => a - b);
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      aria-labelledby={`${id}-label`}
      aria-describedby={`${id}-hint`}
      onChange={(event) => onChange(Number(event.target.value))}
    >
      {values.map((option) => (
        <option key={option} value={option} disabled={unavailable?.(option)}>
          {label(option)}{options.includes(option) ? "" : t("(current value)")}
        </option>
      ))}
    </select>
  );
}

export function Settings({ onSaved, profiles = [], selectedProfile = null, onCreateProfile, onUpdateProfile, onSelectProfile, profileError, onRetryProfiles }: {
  onSaved: () => void;
  profiles?: Profile[];
  selectedProfile?: string | null;
  onSelectProfile?: (id: string | null) => void;
  profileError?: string;
  onRetryProfiles?: () => void;
  onCreateProfile?: (profile: Profile) => void;
  onUpdateProfile?: (profile: Profile) => void;
}) {
  const { managed } = useManagedAccount();
  const theme = useThemePreference();
  const interfaceLanguagePreference = useLanguagePreference();
  const languages = languageCodes.map((value) => ({ value, label: languageLabel(value) }))
    .sort((a, b) => a.label.localeCompare(b.label, interfaceLanguage()));
  const resource = useResource<SettingsData>("/v1/settings");
  const [draft, setDraft] = useState<SettingsData>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [connectionRevision, setConnectionRevision] = useState(0);
  const [configurationRevision, setConfigurationRevision] = useState(0);
  const [serverDirty, setServerDirty] = useState(false);
  const [section, setSection] = useState<SettingsSection>(() => sectionFromPath(managed));
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
    const sync = () => { setSection(sectionFromPath(managed)); setSearch(""); };
    return subscribeNavigation(sync);
  }, [managed]);
  const show = (group: string, category: SettingsSection) => query
    ? matchesSettings(group, query) : section === category;
  const showServer = query ? serverMatches?.query !== search || serverMatches.count !== 0 : section === "server";
  const preferencesVisible = show("appearance", "general") || show("daily", "general")
    || show("storage", "storage");
  const noMatches = query && !["account", "profiles", "appearance", "daily", "storage", "connections", "locations", "usage"].some((group) => matchesSettings(group, query))
    && (managed || serverMatches?.query === search && serverMatches.count === 0);
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
      const changes = preferenceChanges(draft, managed);
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
        preferencesDirty={dirty} serverDirty={serverDirty} managed={managed} />
      <div className="settings-panels">
      {dirty && !preferencesVisible ? <div className="notice">
        <span>{t("You have unsaved preferences in another category.")}</span>
        <button type="button" className="text-button" onClick={() => selectSection(draft?.audio_retention_days !== resource.data?.audio_retention_days || draft?.transcript_retention_days !== resource.data?.transcript_retention_days ? "storage" : "general")}>{t("Review preferences")}</button>
      </div> : null}
      {query ? <div className="settings-section-heading"><h2>{t("Search results")}</h2>
        <p>{t("Settings matching “")}{query}{t("” across all categories.")}</p></div> : null}
      {noMatches ? <div className="settings-empty" role="status"><Icon name="search" size={28} />
        <h2>{t("No settings found")}</h2><p>{t("Try “language”, “audio”, “recap”, or “model”.")}</p>
        <button type="button" className="button" onClick={() => setSearch("")}>{t("Clear search")}</button></div> : null}
      {!query && section === "account" ? <div className="settings-section-heading"><h2>{t("Account & profiles")}</h2></div> : null}
      {!query && section === "connections" ? <div className="settings-section-heading"><h2>{t("Recording & devices")}</h2></div> : null}
      {show("profiles", "account") && onCreateProfile && onUpdateProfile && onSelectProfile ? <ProfileManagement
        profiles={profiles} selected={selectedProfile} onCreate={onCreateProfile} managed={managed}
        onUpdate={onUpdateProfile} onSelect={onSelectProfile} error={profileError} onRetry={onRetryProfiles} /> : null}
      {show("account", "account") ? <details className="settings-disclosure" open={query ? true : undefined}>
        <summary>{t("Hosting & account")}</summary><div className="settings-disclosure-body"><ManagedAccountPanel /></div>
      </details> : null}
      {managed ? <div hidden={!show("account", "account")}><ManagedModels /></div> : null}
      {show("usage", "account") ? <details className="settings-disclosure" open={query ? true : undefined}>
        <summary>{t("Audio & credit usage")}</summary><div className="settings-disclosure-body"><Usage key={selectedProfile} /></div>
      </details> : null}
      <section hidden={!show("appearance", "general")}>
      {!query ? <div className="settings-section-heading"><h2>{t("General")}</h2></div> : null}
      <fieldset>
        <legend>{t("Appearance")}{" "}<span className="settings-badge">{t("Saves automatically")}</span></legend>
        <label className="setting-field" htmlFor="appearance">
          <span id="appearance-label">{t("Theme")}</span>
          <select
            id="appearance"
            value={theme}
            aria-labelledby="appearance-label"
            aria-describedby="appearance-hint"
            onChange={(event) =>
              setThemePreference(parseThemePreference(event.target.value))
            }
          >
            <option value="system">{t("System")}</option>
            <option value="light">{t("Light")}</option>
            <option value="dark">{t("Dark")}</option>
          </select>
          <small id="appearance-hint">{t("Saved on this device. System follows your device’s appearance.")}</small>
        </label>
        <label className="setting-field" htmlFor="interface-language">
          <span id="interface-language-label">{t("Interface language")}</span>
          <select id="interface-language" value={interfaceLanguagePreference}
            aria-labelledby="interface-language-label"
            onChange={(event) => setLanguagePreference(event.target.value as LanguagePreference)}>
            <option value="system">{t("System language")}</option>
            {interfaceLanguages.map((code) => <option key={code} value={code}>
              {new Intl.DisplayNames([code], { type: "language" }).of(code)}
            </option>)}
          </select>
          <small>{t("Saved on this device. This changes the interface, not transcription or summary languages.")}</small>
        </label>
      </fieldset>
      </section>
      {resource.error ? (
        <ErrorState message={resource.error} retry={resource.refresh} />
      ) : null}
      {!draft ? (
        resource.loading ? (
          <Loading>{t("Loading settings…")}</Loading>
        ) : null
      ) : (
        <form id="preferences-form" className="settings-form" onSubmit={save} aria-busy={busy}>
          <fieldset disabled={busy} hidden={!show("daily", "general")}>
            <legend>{t("Language & timezone")}</legend>
            <div className="settings-grid">
              <label className="setting-field" htmlFor="timezone">
                <span id="timezone-label">{t("Home timezone")}</span>
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
                  {t("Dates, diary boundaries, and daily recaps use this timezone.")}{" "}</small>
              </label>
              <div className="setting-field">
                <span>{t("Transcription")}</span>
                <small>{t("Spoken languages are detected automatically. Original transcripts keep the languages used in the conversation.")}</small>
              </div>
              <label className="setting-field" htmlFor="summary-language">
                <span id="summary-language-label">{t("Summary language")}</span>
                <select
                  id="summary-language"
                  value={draft.summary_language}
                  aria-labelledby="summary-language-label"
                  aria-describedby="summary-language-hint"
                  onChange={(event) => update("summary_language", event.target.value)}
                >
                  <option value="auto">{t("Automatic / follow source language")}</option>
                  {!languageCodes.includes(draft.summary_language) && draft.summary_language !== "auto" ? (
                    <option value={draft.summary_language}>{languageLabel(draft.summary_language)} {t("(current value)")}</option>
                  ) : null}
                  {languages.map(({ value, label }) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
                <small id="summary-language-hint">
                  {t("Choose the language for recaps and summaries. Automatic follows the source language.")}{" "}</small>
              </label>
              <div className="setting-field translation-language-settings">
                <span id="translation-languages-label">{t("Translation languages")}</span>
                <small id="translation-languages-hint">{t("Choose the languages offered by the transcript and summary Translate buttons. These are independent of the generated summary language. Translations are generated only when you click Translate.")}</small>
                <ul className="translation-language-list" aria-labelledby="translation-languages-label">
                  {(draft.translation_languages ?? ["en"]).map((code) => <li key={code}>
                    <span>{languageLabel(code)}</span>
                    <button type="button" className="text-button" aria-label={t("Remove {value0} translation language", { value0: languageLabel(code) })}
                      onClick={() => update("translation_languages", (draft.translation_languages ?? ["en"]).filter((value) => value !== code))}>{t("Remove")}</button>
                  </li>)}
                </ul>
                {draft.translation_languages?.length === 0 ? <small>{t("No translation languages enabled. Add a language to use Translate.")}</small> : null}
                <label htmlFor="translation-language-add" className="sr-only">{t("Add translation language")}</label>
                <select id="translation-language-add" value="" aria-describedby="translation-languages-hint"
                  onChange={(event) => { if (event.target.value) update("translation_languages", [...(draft.translation_languages ?? ["en"]), event.target.value]); }}>
                  <option value="">{t("Add translation language…")}</option>
                  {languages.filter(({ value }) => !(draft.translation_languages ?? ["en"]).includes(value)).map(({ value, label }) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </div>
              <p className="caption">{t("Recaps and decision proposals update automatically as transcripts become available, including recordings added to earlier days.")}</p>
            </div>
          </fieldset>
          <fieldset disabled={busy} hidden={!show("storage", "storage")}>
            <legend>{t("Storage & privacy")}</legend>
            <p className="settings-description muted">{managed ? t("Audio and transcripts are kept until you delete them.") : t("Choose how long recordings stay in your archive. Audio and transcripts have separate schedules.")}</p>
            {!managed ? <div className="settings-grid">
              <label className="setting-field" htmlFor="audio-retention">
                <span id="audio-retention-label">{t("Keep original audio")}</span>
                <NumberSelect
                  id="audio-retention"
                  value={managed ? 0 : draft.audio_retention_days}
                  disabled={managed}
                  options={retentionOptions}
                  label={retentionLabel}
                  onChange={(value) => update("audio_retention_days", value)}
                />
                <small id="audio-retention-hint">{managed ? t("Until I remove it") : t("Default: 7 days. Expired audio is removed automatically; transcripts have their own retention period.")}</small>
              </label>
              <label className="setting-field" htmlFor="transcript-retention">
                <span id="transcript-retention-label">{t("Keep transcripts")}</span>
                <NumberSelect
                  id="transcript-retention"
                  value={managed ? 0 : draft.transcript_retention_days}
                  disabled={managed}
                  options={retentionOptions}
                  label={retentionLabel}
                  onChange={(value) => update("transcript_retention_days", value)}
                />
                <small id="transcript-retention-hint">
                  {managed ? t("Until I remove it") : t("Default: 30 days. Expiring transcripts also clears derived diary/chat content and related inbox items, including manual edits and history. Shorter retention periods apply to existing recordings when saved.")}{" "}</small>
              </label>
            </div> : null}
            <p className="caption access-note">
              {managed ? t("Your hosted archive is protected by your Daythread account. Sign out on shared devices.") : t("This app has no sign-in. Anyone who can reach your server address can access this archive.")}{" "}</p>
          </fieldset>
        </form>
      )}
      {show("locations", "locations") ? <LocationZones key={selectedProfile} /> : null}
      {!managed ? <div hidden={!showServer}>
        <Configuration search={search} refreshRevision={configurationRevision} onDirtyChange={setServerDirty}
          onSearchResults={onSearchResults}
          onSaved={() => { setConnectionRevision((value) => value + 1); onSaved(); }}
          hiddenFields={serverHiddenFields} />
      </div> : null}
      <div hidden={!show("connections", "connections")}>
        {managed ? <ManagedConnections /> : <OmiIntegration profileSelected={Boolean(selectedProfile)} refreshRevision={connectionRevision} search={search}
          onConfigure={(label) => { setSection("server"); setSearch(label ?? t("Omi connections")); }}
          onSaved={() => { setConfigurationRevision((value) => value + 1); onSaved(); }} />}
      </div>
          {error ? <ErrorState message={error} /> : null}
          <div hidden={!preferencesVisible || (!dirty && !busy && !saved)} className={`settings-actions${dirty || busy ? " has-changes" : ""}`}>
            <p className="caption settings-save-status" role="status">
              {busy ? t("Saving your preferences…") : saved ? managed ? t("Preferences saved.") : t("Preferences saved on your server.") : dirty ? t("Unsaved preferences.") : t("Your preferences are up to date.")}
            </p>
            {dirty ? <button type="button" className="button" disabled={busy} onClick={() => {
              setDraft(resource.data);
              setError("");
              setSaved(false);
            }}>{t("Discard changes")}</button> : null}
            <button form="preferences-form" className="button dark" disabled={busy || !dirty}>
              <Icon name="check" size={18} />
              {busy ? t("Saving…") : t("Save preferences")}
            </button>
          </div>
      </div>
    </div>
  );
}
