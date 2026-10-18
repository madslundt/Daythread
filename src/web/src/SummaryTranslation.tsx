import type { ReactNode } from "react";
import { CopySummary } from "./CopySummary";
import { recapReadingText } from "./recapItems";
import { TranscriptTranslation } from "./TranscriptTranslation";
import { RichText } from "./UI";

// Keep source-linked tools and task identities attached to the saved original.
export function SummaryTranslation({ outputId, original, children }: {
  outputId?: string; original: string; children?: ReactNode;
}) {
  if (!outputId) return children ?? <RichText text={original} />;
  return <TranscriptTranslation key={`${outputId}:${original}`} sourceId={outputId}
    original={original} content="summary" renderText={(text, translated) =>
      translated ? <>
        <RichText text={recapReadingText(text)} />
        <CopySummary text={recapReadingText(text)} />
      </> : children ?? <RichText text={original} />
    } />;
}
