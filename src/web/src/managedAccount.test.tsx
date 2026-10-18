import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ManagedAccountPanel, ManagedAccountProvider, ManagedArchiveGate } from "./ManagedAccount";
import { SettingsNavigation, sectionFromPath } from "./SettingsNavigation";
import { ProfileManagement } from "./ProfileManagement";
import { MANAGED_SERVICE_ORIGIN } from "./managedService";

afterEach(() => vi.unstubAllGlobals());

describe("optional web account entry", () => {
  it("offers a fixed sign-in destination without collecting credentials on the own backend", () => {
    const html = renderToStaticMarkup(<ManagedAccountPanel />);
    expect(html).toContain(`href="${MANAGED_SERVICE_ORIGIN}/signin"`);
    expect(html).toContain(`href="${MANAGED_SERVICE_ORIGIN}/pricing"`);
    expect(html).not.toContain('href="/pricing"');
    expect(html).toContain("Your current archive stays on this server.");
    expect(html).not.toContain("<input");
    expect(html).not.toContain("<form");
  });

  it("keeps own-backend server controls alongside the optional account category", () => {
    const html = renderToStaticMarkup(<SettingsNavigation section="account" search="" onSearch={() => {}}
      onSelect={() => {}} preferencesDirty={false} serverDirty={false} />);
    expect(html).toContain('href="/settings/account" aria-current="page"');
    expect(html).toContain("Advanced");
  });

  it("uses account actions instead of exposing managed server administration", () => {
    const html = renderToStaticMarkup(<SettingsNavigation section="account" search="" onSearch={() => {}}
      onSelect={() => {}} preferencesDirty={false} serverDirty={false} managed />);
    expect(html).toContain('href="/settings/account"');
    expect(html).not.toContain('href="/settings/server"');
    expect(html).not.toContain("STT &amp; LLM endpoints");
    vi.stubGlobal("window", { location: { pathname: "/settings/server" } });
    expect(sectionFromPath(true)).toBe("account");
  });

  it("describes hosted profile access through the signed-in account", () => {
    const html = renderToStaticMarkup(<ProfileManagement profiles={[{ id: "default", name: "Personal archive" }]} selected="default" onSelect={() => {}}
      onCreate={() => {}} onUpdate={() => {}} managed />);
    expect(html).toContain("Only your signed-in account can access these profiles.");
    expect(html).not.toContain("Anyone using this server");
    expect(html).not.toContain("Create profile");
    expect(html).toContain("Personal archive");
    expect(html).toContain("Edit Personal archive");
  });

  it("waits for account validation before rendering hosted archive content", () => {
    vi.stubGlobal("window", { location: { origin: MANAGED_SERVICE_ORIGIN } });
    const html = renderToStaticMarkup(<ManagedAccountProvider><ManagedArchiveGate>
      <span>private archive content</span>
    </ManagedArchiveGate></ManagedAccountProvider>);
    expect(html).toContain("Loading your account");
    expect(html).not.toContain("private archive content");
  });

  it("opens the own-backend archive immediately without a managed service request", () => {
    vi.stubGlobal("window", { location: { origin: "http://localhost:8787" } });
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const html = renderToStaticMarkup(<ManagedAccountProvider><ManagedArchiveGate>
      <span>own archive</span>
    </ManagedArchiveGate></ManagedAccountProvider>);
    expect(html).toContain("own archive");
    expect(fetch).not.toHaveBeenCalled();
  });
});
