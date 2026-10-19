import type { ReactNode } from "react";
import { CopySummary } from "./CopySummary";
import { recapReadingText } from "./recapItems";
import { TranscriptTranslation } from "./TranscriptTranslation";
import type { TranscriptTranslationData } from "./transcriptTranslationRequests";
import { RichText } from "./UI";

// Keep source-linked tools and task identities attached to the saved original.
export function SummaryTranslation({ outputId, original, children, renderContent, renderFollowing }: {
  outputId?: string; original: string; children?: ReactNode;
  renderContent?: (text: string, translated: boolean, translation?: NonNullable<TranscriptTranslationData["translation"]>, language?: string) => ReactNode;
  renderFollowing?: (language?: string) => ReactNode;
}) {
  if (!outputId) return <>{renderContent?.(original, false) ?? children ?? <RichText text={original} />}{renderFollowing?.()}</>;
  return <TranscriptTranslation key={`${outputId}:${original}`} sourceId={outputId}
    original={original} content="summary" renderFollowing={renderFollowing} renderText={(text, translated, translation, language) =>
      renderContent ? renderContent(text, translated, translation, language) : translated ? <>
        <RichText text={recapReadingText(text)} />
        <CopySummary text={recapReadingText(text)} />
      </> : children ?? <RichText text={original} />
    } />;
}
