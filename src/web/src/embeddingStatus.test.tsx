import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { embeddingProgressState, EmbeddingStatus } from "./EmbeddingStatus";
import { advancedConfigurationFields, type ConfigurationField } from "./configurationModel";

describe("semantic search settings", () => {
  it("shows coverage separately from endpoint readiness", () => {
    expect(embeddingProgressState({ state: "unknown", indexed: 1, total: 2 })).toBe("incomplete");
    expect(embeddingProgressState({ state: "ready", indexed: 2, total: 2 })).toBe("ready");
    expect(embeddingProgressState({ state: "unknown", indexed: 2, total: 2 })).toBe("unknown");
    expect(embeddingProgressState({ state: "unavailable", indexed: 1, total: 2 })).toBe("unavailable");
  });
  it("requires a selected profile before requesting or rebuilding its index", () => {
    const html = renderToStaticMarkup(<EmbeddingStatus profileSelected={false} />);
    expect(html).toContain("Choose a profile");
    expect(html).not.toContain("Rebuild search index");
  });
  it("exposes chunking controls and keeps reranker credentials behind enablement", () => {
    const fields = ["embedding_enabled", "embedding_chunking_strategy", "embedding_target_tokens",
      "embedding_tokenizer", "retrieval_evidence_character_budget", "reranker_enabled", "reranker_token"]
      .map((key): ConfigurationField => ({ key, label: key, group: "Semantic search", kind: "string",
        nullable: false, restart_required: true, description: "" }));
    const visible = advancedConfigurationFields(fields, { embedding_enabled: true }).map(({ key }) => key);
    expect(visible).toContain("embedding_target_tokens");
    expect(visible).toContain("retrieval_evidence_character_budget");
    expect(visible).toContain("reranker_enabled");
    expect(visible).not.toContain("reranker_token");
    expect(advancedConfigurationFields(fields, { embedding_enabled: true, reranker_enabled: true })).toEqual(fields);
  });
});
