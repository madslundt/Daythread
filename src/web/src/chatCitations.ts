import type { Root, PhrasingContent, Text } from "mdast";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import type { Plugin } from "unified";
import { unified } from "unified";

const sourceId = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const citationGroups = /\[([^\[\]]+)\]/g;
const parser = unified().use(remarkParse).use(remarkGfm);

export function citationIds(value: string): string[] {
  const ids = value.split(",").map((id) => id.trim().toLowerCase());
  return ids.every((id) => sourceId.test(id)) ? ids : [];
}

type MarkdownNode = { type: string; children?: MarkdownNode[] };

function visitCitationText(node: MarkdownNode, visit: (text: Text) => PhrasingContent[]) {
  // Only prose can contain citations, not a code example, image, or link label.
  if (!node.children || ["link", "linkReference", "image", "imageReference"].includes(node.type)) return;
  node.children = node.children.flatMap((child) => {
    if (child.type === "text") return visit(child as Text);
    visitCitationText(child, visit);
    return [child];
  });
}

function verifiedCitations(text: string, known: ReadonlySet<string>) {
  return Array.from(text.matchAll(citationGroups)).flatMap((match) => {
    const ids = citationIds(match[1]);
    return ids.length && ids.every((id) => known.has(id))
      ? [{ ids, start: match.index, end: match.index + match[0].length }] : [];
  });
}

export function answerCitations(answer: string, ids: readonly string[]): string[] {
  if (!ids.length || !answer.includes("[")) return [];
  const known = new Set(ids.map((id) => id.toLowerCase()));
  const cited = new Set<string>();
  visitCitationText(parser.parse(answer), (node) => {
    for (const match of verifiedCitations(node.value, known)) match.ids.forEach((id) => cited.add(id));
    return [node];
  });
  return [...cited];
}

export type CitationTarget = { number: number; id: string };
export type CitationTargets = ReadonlyMap<string, CitationTarget>;

export const remarkCitations: Plugin<[{ targets: CitationTargets }], Root> = ({ targets }) => {
  const known = new Set(targets.keys());
  return (tree) => {
    visitCitationText(tree, (node) => {
      const matches = verifiedCitations(node.value, known);
      if (!matches.length) return [node];
      const children: PhrasingContent[] = [];
      let cursor = 0;
      for (const { ids, start, end } of matches) {
        if (start > cursor) children.push({ type: "text", value: node.value.slice(cursor, start) });
        const shown = new Set<number>();
        const buttons: PhrasingContent[] = [];
        for (const id of ids) {
          const { number } = targets.get(id)!;
          if (shown.has(number)) continue;
          shown.add(number);
          buttons.push({ type: "emphasis", data: { hName: "button", hProperties: { "data-source": id } }, children: [
            { type: "text", value: String(number) },
          ] });
        }
        children.push({ type: "emphasis", data: { hName: "span", hProperties: { className: ["chat-citation-group"] } }, children: buttons });
        cursor = end;
      }
      if (cursor < node.value.length) children.push({ type: "text", value: node.value.slice(cursor) });
      return children;
    });
  };
};
