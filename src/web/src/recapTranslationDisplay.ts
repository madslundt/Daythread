import type { SummaryTranslationStructure } from "./transcriptTranslationRequests";

const uuid = /\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/gi;
const prefix = /^[ \t]*(?:#{1,6}\s+|(?:[-*+]\s+|\d+[.)]\s+)(?:\[[ xX]\]\s*)?)/;
const citations = (text: string) => JSON.stringify((text.match(uuid) || []).map((id) => id.toLowerCase()).sort());
const label = /^\*\*[^*]+\*\*(\s*(?:[—–:-]\s*)?)/;

// Translated display is accepted only when every original block keeps its exact identity.
export function recapTranslationDisplay(original: string, translated: string, structure?: SummaryTranslationStructure): string | undefined {
  if (!structure || structure.version !== 1 || !structure.summary_version || !structure.blocks.length) return;
  const characters = Array.from(original), pieces: string[] = [];
  let cursor = 0;
  for (const [index, block] of structure.blocks.entries()) {
    if (block.index !== index || !["heading", "item", "text"].includes(block.kind)
      || !Number.isInteger(block.start_offset) || !Number.isInteger(block.end_offset)
      || block.start_offset < cursor || block.end_offset <= block.start_offset || block.end_offset > characters.length) return;
    const gap = characters.slice(cursor, block.start_offset).join("");
    const originalPrefix = block.original_text.match(prefix)?.[0] || "";
    const displayPrefix = block.text.match(prefix)?.[0] || "";
    const originalLabel = block.original_text.slice(originalPrefix.length).match(label);
    const displayLabel = block.text.slice(displayPrefix.length).match(label);
    if (gap.trim() || characters.slice(block.start_offset, block.end_offset).join("") !== block.original_text
      || (block.start_offset > 0 && characters[block.start_offset - 1] !== "\n")
      || (block.end_offset < characters.length && !/[\r\n]/.test(characters[block.end_offset]))
      || !block.text.trim() || /[\r\n]/.test(block.original_text + block.text)
      || citations(block.original_text) !== citations(block.text) || originalPrefix !== displayPrefix
      || Boolean(originalLabel) !== Boolean(displayLabel) || originalLabel?.[1] !== displayLabel?.[1]) return;
    pieces.push(gap, block.text); cursor = block.end_offset;
  }
  const tail = characters.slice(cursor).join("");
  if (tail.trim()) return;
  pieces.push(tail);
  const rebuilt = pieces.join("");
  return rebuilt === translated ? rebuilt : undefined;
}
