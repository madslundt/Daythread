import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TranscriptionReview } from "./TranscriptionReview";

describe("cleanup readings remain separate from saved evidence", () => {
  it("shows conflicting words as review-only with original playback guidance", () => {
    const html = renderToStaticMarkup(<TranscriptionReview onReview={() => {}} details={{
      chunks: [{
        start: 30, end: 60, gain_db: 12, enhancement_status: "complete",
        candidate: { text: "The quieter speaker said Friday", differs: true },
      }],
    }} />);
    expect(html).toContain("needs review");
    expect(html).toContain("excluded from search and summaries");
    expect(html).toContain("30.0–60.0 seconds");
    expect(html).toContain("The quieter speaker said Friday");
    expect(html).toContain("Review and correct transcript");
    expect(html).not.toContain("Save automatically");
  });

  it("discloses missing cleanup without claiming the original recognition failed", () => {
    const html = renderToStaticMarkup(<TranscriptionReview onReview={() => {}} details={{
      chunks: [{ start: 0, end: 30, gain_db: 0, enhancement_status: "unavailable" }],
    }} />);
    expect(html).toContain("original transcription was kept");
    expect(html).not.toContain("needs review");
  });

  it("does not present matching or empty cleanup as additional speech", () => {
    const html = renderToStaticMarkup(<TranscriptionReview onReview={() => {}} details={{
      chunks: [
        { start: 0, end: 10, gain_db: 0, enhancement_status: "complete",
          candidate: { text: "Same words", differs: false } },
        { start: 10, end: 20, gain_db: 0, enhancement_status: "complete",
          candidate: { text: "", differs: true } },
      ],
    }} />);
    expect(html).toBe("");
  });
});
