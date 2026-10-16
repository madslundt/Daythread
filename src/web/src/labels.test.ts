import { describe, expect, it } from "vitest";
import { cleanDraft } from "./LabelEditors";

describe("label editor API boundary", () => {
  it("renames a fetched label without leaking server fields or inventing an alias", () => {
    const fetched = {
      id: "836a4ce4-40fd-405f-b12b-fea9c24dfe92",
      kind: "person" as const,
      name: "Alex",
      description: " Cycling club ",
      aliases: [" Ålex ", ""],
      merged_into: null,
    };
    // A fetched object has more fields than the writable DTO. The strict backend
    // rejects them; a successful rename sends only explicit user-editable metadata.
    expect(cleanDraft({ ...fetched, name: "Alex (cycling)" })).toEqual({
      kind: "person",
      name: "Alex (cycling)",
      description: "Cycling club",
      aliases: ["Ålex"],
    });
  });
});
