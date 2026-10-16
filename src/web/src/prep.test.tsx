import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PrepBrief } from "./PrepBrief";
import type { PrepResult } from "./prep";

const id = "e01a0dcc-bdde-41ba-a02d-7ff606effda5";
const source = {
  id,
  version: "a".repeat(64),
  device_id: "fixture",
  started_at: "2026-10-25T01:30:00Z",
  ended_at: "2026-10-25T01:31:00Z",
  state: "done",
  text: "May: I'll send it if approved.",
  audio_available: false,
  source_kind: "omi_api",
};
const citation = {
  source_id: id,
  version: source.version,
  excerpt: source.text,
  started_at: source.started_at,
  ended_at: source.ended_at,
  segment_start: null,
  segment_end: null,
};
const result: PrepResult = {
  as_of: "2026-10-25T02:00:00Z",
  generated_at: "2026-10-25T02:01:00Z",
  model: "local",
  prompt_version: "fixture",
  latest_source_id: id,
  user_context: "My private assumption: May already sent it.",
  search: {
    kind: "person",
    subject: "May",
    aliases: [],
    qualifier: "",
    start_day: null,
    end_day: null,
    offset: 0,
  },
  snapshot: source.version,
  timezone: "Europe/Copenhagen",
  sources: [source],
  coverage: {
    total_matches: 3,
    pending_chunks: 1,
    capture_gaps: 1,
    returned: 1,
    omitted_matches: 2,
    limitations: ["Literal retrieval is incomplete."],
  },
  reviewed_excerpts: [
    {
      id,
      text: source.text,
      started_at: source.started_at,
      omitted_characters: 100,
    },
  ],
  latest_discussion: [
    {
      text: "A plan was mentioned; participation is unverified.",
      citations: [citation],
      completion_status: null,
      owner: null,
    },
  ],
  promises_to_check: [
    {
      text: "Check sending the plan if approved.",
      citations: [citation],
      completion_status: "unknown",
      owner: null,
    },
  ],
  recorded_questions: [],
  suggested_questions: [],
};

describe("preparation evidence presentation", () => {
  it("keeps user assumptions out of generated evidence and presents dated uncertainty and omissions", () => {
    const html = renderToStaticMarkup(
      <PrepBrief result={result} timezone="Europe/Copenhagen" />,
    );
    expect(html).not.toContain(result.user_context);
    expect(html).toContain("completion and owner unknown");
    expect(html).toContain("Suggested questions · proposals based on evidence");
    expect(html).toContain("2 matching recordings were not selected");
    expect(html).toContain("100");
    expect(html).toContain("characters omitted");
    expect(html).toContain("Oct 25, 2026, 2:30 AM");
    expect(html).toContain("No supported items in the reviewed excerpts");
  });
  it("renders generated text and exact excerpts as text, never executable markup", () => {
    const injected = {
      ...result,
      latest_discussion: [
        {
          ...result.latest_discussion[0],
          text: '<script>alert("claim")</script>',
          citations: [
            { ...citation, excerpt: '<a href="javascript:alert(1)">quote</a>' },
          ],
        },
      ],
    };
    const html = renderToStaticMarkup(
      <PrepBrief result={injected} timezone="UTC" />,
    );
    expect(html).not.toContain("<script>");
    expect(html).not.toContain('<a href="javascript:');
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&lt;a href=");
  });
});
