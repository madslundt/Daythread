import { afterEach, describe, expect, it, vi } from "vitest";
import { copySummary } from "./CopySummary";

afterEach(() => vi.unstubAllGlobals());
function legacyClipboard(success = true) {
  class FocusTarget { focus = vi.fn(); }
  const previous = new FocusTarget();
  const input = { value: "", readOnly: false, style: { position: "", left: "" }, select: vi.fn(), focus: vi.fn(), remove: vi.fn() };
  const execute = vi.fn(() => success);
  vi.stubGlobal("HTMLElement", FocusTarget);
  vi.stubGlobal("document", {
    activeElement: previous, createElement: () => input, body: { append: vi.fn() }, execCommand: execute,
  });
  return { previous, input, execute };
}
describe("copying summaries", () => {
  it("writes the complete topic summary with the native clipboard API", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await copySummary("Tesla Roadster\n\nThe complete topic summary.");
    expect(writeText).toHaveBeenCalledWith("Tesla Roadster\n\nThe complete topic summary.");
  });
  it("supports local HTTP servers without the Clipboard API and restores focus", async () => {
    vi.stubGlobal("navigator", {});
    const { input, execute, previous } = legacyClipboard();
    await copySummary("A complete summary.");
    expect(input.value).toBe("A complete summary.");
    expect(execute).toHaveBeenCalledWith("copy");
    expect(input.remove).toHaveBeenCalledOnce();
    expect(previous.focus).toHaveBeenCalledOnce();
  });
  it("cleans up and reports failure if both clipboard methods are unavailable", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => { throw new Error("Unavailable"); }) } });
    const { input, previous } = legacyClipboard(false);
    await expect(copySummary("Summary")).rejects.toThrow("Clipboard unavailable");
    expect(input.remove).toHaveBeenCalledOnce();
    expect(previous.focus).toHaveBeenCalledOnce();
  });
});
