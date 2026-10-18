import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { preferenceChanges } from "./Settings";
import type { Settings } from "./types";
import { SettingsNavigation, sectionFromPath, matchesSettings } from "./SettingsNavigation";
import { advancedConfigurationFields, type ConfigurationField } from "./configurationModel";

afterEach(() => vi.unstubAllGlobals());

const navigation = (managed = false) => renderToStaticMarkup(<SettingsNavigation section="general" search="" onSearch={() => {}}
  onSelect={() => {}} preferencesDirty={false} serverDirty={false} managed={managed} />);

describe("simplified settings", () => {
  it("offers six destinations and keeps server administration out of hosted accounts", () => {
    const html = navigation();
    expect(html.match(/href="\/settings\//g)).toHaveLength(6);
    for (const label of ["General", "Recording &amp; devices", "Storage &amp; privacy", "Location zones", "Account &amp; profiles", "Advanced"])
      expect(html).toContain(label);
    expect(html).not.toContain("Flags &amp; meetings");
    expect(navigation(true).match(/href="\/settings\//g)).toHaveLength(5);
    expect(navigation(true)).not.toContain('href="/settings/server"');
  });

  it.each([["profiles", "account"], ["usage", "account"], ["locations", "locations"], ["gestures", "connections"]])(
    "keeps old %s links working in %s", (path, expected) => {
      vi.stubGlobal("window", { location: { pathname: `/settings/${path}` } });
      expect(sectionFromPath()).toBe(expected);
    });

  const field = (key: string): ConfigurationField => ({ key, label: key, group: "Test", kind: "string", description: "", nullable: false, restart_required: false });
  it("curates advanced controls instead of exposing new backend, retired or fixed-policy fields", () => {
    const keys = ["stt_model", "llm_model", "meeting_max_minutes", "meeting_quiet_minutes", "meeting_context_seconds", "marker_cooldown_seconds",
      "translation_languages", "capture_paused", "recap_hour", "recap_quiet_minutes", "silence_threshold", "data_dir", "new_backend_option"];
    expect(advancedConfigurationFields(keys.map(field), {}).map(({ key }) => key)).toEqual(["stt_model", "llm_model"]);
  });

  it("shows tuning only after the corresponding feature is enabled", () => {
    const fields = ["embedding_enabled", "embedding_model", "diarization_enabled", "diarization_base_url", "voice_recognition_enabled",
      "voice_recognition_report", "chat_query_expansion_timeout_seconds", "stt_enhancement_timeout_seconds"].map(field);
    expect(advancedConfigurationFields(fields, { stt_enhancement_mode: "off" }).map(({ key }) => key))
      .toEqual(["embedding_enabled", "diarization_enabled"]);
    expect(advancedConfigurationFields(fields, { embedding_enabled: true, diarization_enabled: true,
      voice_recognition_enabled: true, chat_query_expansion: true, stt_enhancement_mode: "on_empty" })).toEqual(fields);
  });
});


it("excludes all meeting policy fields from preferences even when a legacy draft contains them", () => {
  expect(matchesSettings("meetings", "quiet")).toBe(false);
  const draft: Settings = {
    timezone: "Europe/Copenhagen", summary_language: "auto", meeting_quiet_minutes: 15,
    audio_retention_days: 7, transcript_retention_days: 30,
    language: "auto", capture_paused: true, translation_languages: ["da"],
    meeting_max_minutes: 120, meeting_min_seconds: 60, meeting_context_seconds: 60,
    marker_context_seconds: 60, marker_after_seconds: 60, marker_cooldown_seconds: 60, recap_hour: 22,
  };
  expect(preferenceChanges(draft, false)).toEqual({ timezone: "Europe/Copenhagen", summary_language: "auto",
    translation_languages: ["da"], audio_retention_days: 7, transcript_retention_days: 30 });
  expect(preferenceChanges(draft, true)).toEqual({ timezone: "Europe/Copenhagen", summary_language: "auto", translation_languages: ["da"] });
});
