import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { generateParrotSecret, ParrotReceiver, type ParrotSettings } from "./ParrotReceiver";
import { VendorConnection } from "./VendorConnections";
import { selectProfile } from "./profiles";

const resource = vi.hoisted(() => ({
  data: undefined as ParrotSettings | undefined, loading: false, error: "",
  refresh: vi.fn(), setData: vi.fn(),
}));
vi.mock("./useResource", () => ({ useResource: () => resource }));
afterEach(() => selectProfile("default"));

beforeEach(() => {
  resource.data = {
    revision: "initial", enabled: false, secret: "",
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
    expect(html).toContain("Webhook URL");
    expect(html).toContain("Copy webhook URL");
    expect(html).toContain('readOnly=""');
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

  it("retains the saved secret masked and makes it available to copy or reveal", () => {
    resource.data!.enabled = true;
    resource.data!.secret = "saved-test-signing-secret";
    const html = renderToStaticMarkup(<ParrotReceiver onSaved={vi.fn()} />);
    expect(html).toContain("enabled; waiting for deliveries");
    expect(html).toContain("Saving a different secret immediately revokes the old one.");
    expect(html).toContain('value="saved-test-signing-secret"');
    expect(html).toContain('type="password"');
    expect(html).toContain("Copy signing secret");
    expect(html).toContain("Show signing secret");
    expect(html).not.toContain("missing signing secret");
  });

  it("distinguishes an enabled receiver with a missing secret", () => {
    resource.data!.enabled = true;
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
