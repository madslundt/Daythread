import { memo, type MouseEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { remarkCitations, type CitationTarget, type CitationTargets } from "./chatCitations";
import { t } from "./i18n";

// Answers may quote arbitrary transcripts. Never load remote content on render.
function answerUrl(url: string): string {
  return /^(https?:\/\/|mailto:)/i.test(url) ? url : "";
}

export const ChatMarkdown = memo(function ChatMarkdown({ text, draft = false, citations, onCitation }: {
  text: string; draft?: boolean; citations?: CitationTargets;
  onCitation?: (target: CitationTarget, trigger: HTMLButtonElement) => void;
}) {
  const targets = !draft && onCitation && citations?.size ? citations : undefined;
  return <div className="chat-markdown" aria-live={draft ? "off" : undefined}>
    <Markdown skipHtml urlTransform={answerUrl} remarkPlugins={targets
      ? [remarkGfm, [remarkCitations, { targets }], remarkBreaks] : [remarkGfm, remarkBreaks]} components={{
      a: ({ href, children }) => href && !draft
        ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
        : <span>{children}</span>,
      img: ({ alt }) => alt ? <span>{alt}</span> : null,
      button: ({ node, children }) => {
        const id = node?.properties["data-source"];
        const target = typeof id === "string" ? targets?.get(id) : undefined;
        return target && onCitation ? <button type="button" className="chat-citation" aria-haspopup="dialog"
          aria-label={t("Open source {number}", { number: target.number })}
          onClick={(event: MouseEvent<HTMLButtonElement>) => onCitation(target, event.currentTarget)}>{children}</button> : <span>{children}</span>;
      },
      h1: ({ children }) => <h3>{children}</h3>,
      h2: ({ children }) => <h4>{children}</h4>,
      h3: ({ children }) => <h5>{children}</h5>,
      h4: ({ children }) => <h6>{children}</h6>,
      table: ({ children }) => <div className="chat-table-scroll" role="region" aria-label={t("Answer table")} tabIndex={draft ? undefined : 0}>
        <table>{children}</table>
      </div>,
    }}>{text}</Markdown>
  </div>;
});
