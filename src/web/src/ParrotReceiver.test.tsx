import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigurationData } from "./configurationModel";
import { generateParrotSecret, ParrotReceiver } from "./ParrotReceiver";
import { VendorConnection } from "./VendorConnections";
import { selectProfile } from "./profiles";

const resource = vi.hoisted(() => ({
  data: undefined as ConfigurationData | undefined, loading: false, error: "",
  refresh: vi.fn(), setData: vi.fn(),
}));
vi.mock("./useResource", () => ({ useResource: () => resource }));
afterEach(() => selectProfile("default"));

beforeEach(() => {
  resource.data = {
    revision: "initial", fields: [], pending_restart: [],
    values: { parrot_webhook_enabled: false, parrot_webhook_secret: null },
    secrets: { parrot_webhook_secret: false },
  };
  resource.loading = false;
  resource.error = "";
});

describe("inline Parrot receiver settings", () => {
  it("keeps configuration beside the selected profile's receiver address", () => {
    selectProfile("17741b4f-289e-418f-a280-e08b97287872");
    const html = renderToStaticMarkup(<VendorConnection vendor={{
      id: "parrot", label: "Parrot", configured: false,
      capabilities: ["file_upload", "transcript_webhook"], import_instructions: "", setup_url: "",
    }} expanded profileSelected onSaved={vi.fn()} onConfigure={vi.fn()} />);
    expect(html).toContain("Parrot webhook enabled");
    expect(html).toContain("Generate signing secret");
    expect(html).toContain("/v1/integrations/parrot/webhook?profile_id=17741b4f-289e-418f-a280-e08b97287872");
    expect(html).not.toContain("Configure Parrot connection");
  });

  it("offers the receiver switch and signing secret together without server navigation", () => {
    const html = renderToStaticMarkup(<ParrotReceiver onSaved={vi.fn()} />);
    expect(html).toContain("Parrot webhook enabled");
    expect(html).toContain("Parrot webhook secret");
    expect(html).toContain('type="password"');
    expect(html).toContain("Save Parrot settings");
    expect(html).toContain("Generate signing secret");
    expect(html).not.toContain("server settings");
  });

  it("generates independent URL-safe secrets with 256 bits of randomness", () => {
    const first = generateParrotSecret();
    const second = generateParrotSecret();
    expect(first).toMatch(/^[a-f0-9]{64}$/);
    expect(second).toMatch(/^[a-f0-9]{64}$/);
    expect(first).not.toBe(second);
  });

  it("shows a saved secret as write-only and permits keeping it", () => {
    resource.data!.values.parrot_webhook_enabled = true;
    resource.data!.secrets.parrot_webhook_secret = true;
    const html = renderToStaticMarkup(<ParrotReceiver onSaved={vi.fn()} />);
    expect(html).toContain("enabled; waiting for deliveries");
    expect(html).toContain("Signing secret saved. Leave blank to keep it, or enter a replacement.");
    expect(html).toContain('value=""');
    expect(html).not.toContain("missing signing secret");
  });

  it("distinguishes an enabled receiver with a missing secret", () => {
    resource.data!.values.parrot_webhook_enabled = true;
    const html = renderToStaticMarkup(<ParrotReceiver onSaved={vi.fn()} />);
    expect(html).toContain("missing signing secret");
    expect(html).toContain('required=""');
    expect(html).toContain("These settings apply to all profiles.");
  });

  it("does not offer editable defaults while configuration is unavailable", () => {
    resource.data = undefined;
    resource.loading = true;
    const html = renderToStaticMarkup(<ParrotReceiver onSaved={vi.fn()} />);
    expect(html).toContain("Loading server settings");
    expect(html).not.toContain('type="password"');
  });
});
