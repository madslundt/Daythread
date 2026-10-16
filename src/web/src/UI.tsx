import { Fragment } from "react";
import { Icon, type IconName } from "./Icon";

export function Notice({
  children,
  tone = "info",
  onDismiss,
}: {
  children: React.ReactNode;
  tone?: "info" | "error" | "success";
  onDismiss?: () => void;
}) {
  return (
    <div
      className={`notice notice-${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      <div>{children}</div>
      {onDismiss ? (
        <button
          className="icon-button"
          aria-label="Dismiss message"
          onClick={onDismiss}
        >
          <Icon name="close" size={16} />
        </button>
      ) : null}
    </div>
  );
}

export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <Notice tone="error">
      {message}
      {retry ? (
        <button className="text-button" onClick={retry}>
          Try again
        </button>
      ) : null}
    </Notice>
  );
}

export function EmptyState({
  icon = "diary",
  title,
  children,
  action,
}: {
  icon?: IconName;
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <Icon name={icon} size={27} />
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}

function inline(text: string): React.ReactNode[] {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .map((part, index) =>
      part.startsWith("**") ? (
        <strong key={index}>{part.slice(2, -2)}</strong>
      ) : (
        part
      ),
    );
}

// Render a small, safe Markdown subset. Model output never becomes HTML.
export function RichText({ text }: { text: string }) {
  return (
    <div className="prose">
      {text.split(/\n\s*\n/).map((block, index) => {
        const lines = block.trim().split("\n");
        if (!block.trim()) return null;
        if (lines.every((line) => /^[-*] /.test(line)))
          return (
            <ul key={index}>
              {lines.map((line, i) => (
                <li key={i}>{inline(line.slice(2))}</li>
              ))}
            </ul>
          );
        if (/^#{1,4} /.test(lines[0]))
          return (
            <Fragment key={index}>
              <h3>{inline(lines[0].replace(/^#+\s*/, ""))}</h3>
              {lines.length > 1 ? (
                <p>{inline(lines.slice(1).join("\n"))}</p>
              ) : null}
            </Fragment>
          );
        return <p key={index}>{inline(block.replace(/^\d+\. /gm, ""))}</p>;
      })}
    </div>
  );
}

export function Loading({
  children = "Loading…",
}: {
  children?: React.ReactNode;
}) {
  return (
    <div className="loading" role="status">
      <span className="loading-dot" />
      {children}
    </div>
  );
}
