import { prepareSamples } from "./audioUpload.testSupport";
import { renderToStaticMarkup } from "react-dom/server";
import { setLanguagePreference, t } from "./i18n";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VendorConnection } from "./VendorConnections";
import { transcriptExportBody } from "./TranscriptExportImport";
import { sourceVendor, sourceVendorLabel, vendorLabel, VendorOptions, type Vendor } from "./vendors";
import { globalEndpoint } from "./profiles";
import { SourceList } from "./SourceList";
import { RecordingUploadQueue, type PrepareRecording, type PreparedRecording } from "./audioUpload";

afterEach(() => setLanguagePreference("en"));

const plaud: Vendor = {
  id: "plaud", label: "Plaud", capabilities: ["file_upload", "transcript_export"], configured: false,
  import_instructions: "Export your original transcript.", setup_url: "https://support.plaud.ai/",
};
const pocket: Vendor = {
  ...plaud, id: "pocket", label: "Pocket", capabilities: ["file_upload", "transcript_import"], configured: true,
};
const source = { id: "example", device_id: "manual-web", started_at: "2026-10-15T12:00:00Z",
  ended_at: "2026-10-15T12:01:00Z", state: "done", text: "Original speech" };

describe("vendor capabilities and provenance", () => {
  it("offers upload only for vendors declaring that capability", () => {
    const html = renderToStaticMarkup(<VendorOptions vendors={[plaud, pocket, { ...plaud, id: "future", label: "Future device", capabilities: [] }]} />);
    expect(html).toContain('value="manual"');
    expect(html).toContain('value="plaud"');
    expect(html).toContain('value="pocket"');
    expect(html).not.toContain('value="future"');
  });
  it("uses authoritative catalog identity and preserves future brand spelling", () => {
    const future = { ...source, source_kind: "omi_api", vendor_id: "future_brand",
      vendor_label: "newBrand voiceIO", source_metadata: { vendor_id: "plaud" }, audio_available: false };
    expect(sourceVendor(future)).toBe("future_brand");
    expect(sourceVendorLabel(future)).toBe("newBrand voiceIO");
    const html = renderToStaticMarkup(<SourceList timezone="UTC" expanded sources={[future]} />);
    expect(html).toContain("Imported transcript from newBrand voiceIO");
    expect(html).toContain("Imported from newBrand voiceIO. This import includes the transcript only");
    expect(html).not.toContain("Future Brand");
    expect(sourceVendorLabel({ source_kind: "pocket_api" })).toBe("Pocket");
    expect(sourceVendorLabel({ source_metadata: { vendor_id: "plaud" } })).toBe("Plaud");
  });
  it("keeps audio capture distinct from transcript imports when the backend supplies a vendor", () => {
    const html = renderToStaticMarkup(<SourceList timezone="UTC" expanded sources={[
      { ...source, source_kind: "omi_audio_webhook", vendor_id: "omi", vendor_label: "Omi", audio_available: false },
    ]} />);
    expect(html).toContain("Forwarded from the official Omi integration");
    expect(html).not.toContain("Imported transcript");
    expect(html).not.toContain("This import includes the transcript only");
  });
  it("shows Plaud transcript exports without inventing a cloud connection", () => {
    const html = renderToStaticMarkup(<VendorConnection vendor={plaud} expanded profileSelected onSaved={vi.fn()} onConfigure={vi.fn()} />);
    expect(html).toContain("Transcript export (SRT or TXT)");
    expect(html).toContain("Import transcript export");
    expect(html).not.toContain("Import latest conversations");
    expect(html).not.toContain("API key");
  });
  it("makes cloud import depend on a configured key and a selected profile", () => {
    const render = (vendor: Vendor, selected: boolean) => renderToStaticMarkup(<VendorConnection
      vendor={vendor} expanded profileSelected={selected} onSaved={vi.fn()} onConfigure={vi.fn()} />);
    expect(render(pocket, true)).toContain('class="button">Import latest conversations');
    expect(render(pocket, false)).toContain('class="button" disabled="">Import latest conversations');
    expect(render({ ...pocket, configured: false }, true)).toContain("Configure Pocket connection");
    expect(render({ ...pocket, configured: false }, true)).toContain('class="button" disabled="">Import latest conversations');
  });
  it("keeps catalog discovery global while import writes remain profile scoped", () => {
    expect(globalEndpoint("/v1/vendors")).toBe(true);
    expect(globalEndpoint("/v1/vendors/pocket")).toBe(true);
    expect(globalEndpoint("/v1/vendors/pocket/import")).toBe(false);
    expect(globalEndpoint("/v1/vendors/plaud/import-export")).toBe(false);
  });
  it("labels branded uploads and future transcript adapters while retaining filenames", () => {
    const manual = { ...source, source_kind: "manual_upload", source_id: "meeting.m4a", source_metadata: { vendor_id: "plaud" } };
    const html = renderToStaticMarkup(<SourceList timezone="UTC" expanded sources={[manual]} />);
    expect(html).toContain("Uploaded recording from Plaud");
    expect(html).toContain("meeting.m4a");
    expect(sourceVendor({ source_kind: "future_api" })).toBe("future");
    expect(vendorLabel("future")).toBe("Future");
    const transcript = renderToStaticMarkup(<SourceList timezone="UTC" expanded sources={[
      { ...source, source_kind: "pocket_api", audio_available: false },
      { ...source, id: "export", source_kind: "plaud_export", audio_available: false },
    ]} />);
    expect(transcript).toContain("Imported from Pocket. This import includes the transcript only");
    expect(transcript).toContain("Imported from Plaud. This import includes the transcript only");
  });
});

describe("localized vendor catalog and server messages", () => {
  it("translates server-supplied setup instructions and descriptive brand labels", () => {
    setLanguagePreference("da");
    const html = renderToStaticMarkup(<VendorConnection vendor={{ ...plaud,
      import_instructions: "Export audio or a transcript (SRT or TXT) from Plaud, then import it here.",
    }} expanded profileSelected onSaved={vi.fn()} onConfigure={vi.fn()} />);
    expect(html).toContain("Eksporter lyd eller en transskription");
    expect(html).toContain("Importer transskriptionseksport");
    expect(html).not.toContain("Export audio or a transcript");
    const options = renderToStaticMarkup(<VendorOptions vendors={[{
      ...plaud, id: "zoom", label: "Zoom recorders",
    }]} />);
    expect(options).toContain('value="zoom">Zoom-optagere');
  });
  it("translates shared credential diagnostics without changing the vendor’s name", () => {
    setLanguagePreference("da");
    expect(t("Save your Pocket API key in server settings to enable import"))
      .toBe("Gem din Pocket-API-nøgle i serverindstillingerne for at aktivere import");
    expect(t("Pocket rejected the API key; check its read permission"))
      .toBe("Pocket afviste API-nøglen. Kontrollér dens læsetilladelse");
    expect(t("Device connections")).toBe("Enhedsforbindelser");
  });
});

describe("transcript export imports", () => {
  it("preserves original SRT and converts explicit capture times to UTC", async () => {
    const content = "1\n00:00:00,000 --> 00:00:03,000\nOriginal speech\n";
    await expect(transcriptExportBody(new File([content], "recording.SRT"),
      "2026-10-15T12:00:00+02:00", "2026-10-15T12:01:00+02:00")).resolves.toEqual({
      format: "srt", content, started_at: "2026-10-15T10:00:00.000Z", ended_at: "2026-10-15T10:01:00.000Z",
    });
  });
  it("accepts plain transcript exports while rejecting missing or reversed times", async () => {
    const file = new File(["Original speech"], "recording.txt");
    expect((await transcriptExportBody(file, "2026-10-15T12:00:00Z", "2026-10-15T12:01:00Z")).format).toBe("txt");
    await expect(transcriptExportBody(file, "", "2026-10-15T12:01:00Z")).rejects.toThrow("original recording start");
    await expect(transcriptExportBody(file, "2026-10-15T12:02:00Z", "2026-10-15T12:01:00Z")).rejects.toThrow("end after the start");
  });
  it("bounds file reads and only accepts transcript formats", async () => {
    for (const file of [new File(["speech"], "audio.wav"), new File([], "empty.srt"), new File([new Uint8Array(1_000_001)], "large.txt")])
      await expect(transcriptExportBody(file, "2026-10-15T12:00:00Z", "2026-10-15T12:01:00Z")).rejects.toThrow("SRT or TXT");
  });
});

describe("vendor upload retry identity", () => {
  it("uses the server plan’s vendor and preserves it even when a retry changes the selection", async () => {
    const file = new File(["recording"], "pocket-export.wav");
    const scope = { id: "example", signal: new AbortController().signal };
    let plan: Extract<PreparedRecording, { status: "upload" }> | undefined;
    const prepare = vi.fn(async (body: PrepareRecording) => {
      plan ??= { ...body, status: "upload", vendor_id: "pocket", recording_id: "canonical",
        parts: body.digests.map((digest) => ({ id: "part-id", digest, uploaded: false })) };
      return plan;
    });
    const send = vi.fn(async (path: string, body: Blob) => {
      const url = new URL(path, "http://localhost");
      return { id: "part-id", device_id: "manual-web", started_at: url.searchParams.get("started_at"),
        ended_at: new Date(Date.parse(url.searchParams.get("started_at")!) + (body.size - 44) / 32).toISOString() };
    }).mockRejectedValueOnce(new Error("Network interrupted"));
    const queue = new RecordingUploadQueue(prepareSamples(async () => new Float32Array(16000)), send,
      { lookup: async () => ({ duplicate: false }), prepare });
    const recording = { file, vendorId: "plaud", recordedAt: "2026-10-15T12:01:00Z", timeReference: "end" as const };
    await expect(queue.upload(recording, "", scope.signal, scope, vi.fn(), vi.fn())).rejects.toThrow("Network interrupted");
    await queue.upload({ ...recording, vendorId: "sony" }, "", scope.signal, scope, vi.fn(), vi.fn());
    expect(prepare.mock.calls.map(([body]) => body.vendor_id)).toEqual(["plaud", "pocket"]);
    expect(send.mock.calls[0][0]).toEqual(send.mock.calls[1][0]);
    const url = new URL(send.mock.calls[1][0], "http://localhost");
    expect(url.searchParams.get("vendor_id")).toBe("pocket");
    expect(url.searchParams.get("source_id")).toBe(file.name);
    expect(url.searchParams.get("source_kind")).toBe("manual_upload");
  });
});
