import { afterEach, describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ConversationTags, TagEditor, TagFilter } from "./ConversationTags";
import { setLanguagePreference } from "./i18n";

afterEach(() => setLanguagePreference("system"));
describe("manual conversation tags", () => {
  it("offers a visible add action on an untagged recording", () => {
    const html = renderToStaticMarkup(<ConversationTags sourceID="source-1" />);
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('Add tag</button>');
    expect(html).toContain("No tags yet");
  });

  it("keeps user-authored tag names literal and escapes markup", () => {
    setLanguagePreference("da");
    const html = renderToStaticMarkup(<ConversationTags sourceID="source-1" tags={[
      { id: "a", name: "Home" }, { id: "b", name: "<script>alert(1)</script>" },
    ]} />);
    expect(html).toContain("Home");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("explains full conversation scope and keeps edits disabled until state is loaded", () => {
    const html = renderToStaticMarkup(<TagEditor path="/v1/recording-groups/group/tags?day=2026-10-17" group />);
    expect(html).toContain("including those hidden by filters");
    expect(html).toContain("Search or create a tag");
    expect(html).not.toContain("<select");
    expect(html).toContain("<fieldset disabled");
  });

  it("uses an independent tag selector with a clear reset", () => {
    const html = renderToStaticMarkup(<TagFilter value="" onChange={() => {}} />);
    expect(html).toContain("All tags");
    expect(html).not.toContain("Location zone");
  });
});
