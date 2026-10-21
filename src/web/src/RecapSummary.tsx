import { useId, useState, type ReactNode } from "react";
import { t } from "./i18n";
import { SummaryTranslation } from "./SummaryTranslation";

export function recapPreview(text: string): string {
  const plain = text.replace(/\[([^\]\n]+)\]\([^\s)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "").replace(/(\*\*|__|\*|_|`)(.*?)\1/g, "$2")
    .replace(/\s+/g, " ").trim();
  if (plain.length <= 240) return plain;
  return plain.slice(0, 240).replace(/\s+\S*$/, "") + "…";
}

export function RecapSummary({ outputId, text, renderContent, children }: {
  outputId?: string; text: string; renderContent: (text: string) => ReactNode; children?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  return <RecapSummaryView {...{ outputId, text, renderContent, children, expanded, id }}
    onToggle={() => setExpanded((value) => !value)} />;
}

export function RecapSummaryView({ outputId, text, renderContent, children, expanded, id, onToggle }: {
  outputId?: string; text: string; renderContent: (text: string) => ReactNode; children?: ReactNode;
  expanded: boolean; id: string; onToggle: () => void;
}) {
  return <div className="recap-summary-disclosure">
    <div id={id}>
      <SummaryTranslation outputId={outputId} original={text} renderContent={(content) => expanded
        ? renderContent(content) : <p className="recap-summary-preview">{recapPreview(content)}</p>} />
      {expanded ? children : null}
    </div>
    <button type="button" className="text-button recap-summary-toggle" aria-expanded={expanded}
      aria-controls={id} onClick={onToggle}>{expanded ? t("Collapse recap") : t("Expand recap")}</button>
  </div>;
}
