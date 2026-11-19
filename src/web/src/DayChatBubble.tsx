import { useEffect, useId, useRef, useState } from "react";
import { Chat } from "./Chat";
import { Icon } from "./Icon";
import { t } from "./i18n";

export function DayChatBubble({ day, timezone, filtered }: { day: string; timezone: string; filtered: boolean }) {
  const [open, setOpen] = useState(false);
  const [started, setStarted] = useState(false);
  const id = useId();
  const launcher = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) panel.current?.focus({ preventScroll: true }); }, [open]);

  function close() {
    setOpen(false);
    launcher.current?.focus({ preventScroll: true });
  }

  return <div className="day-chat-dock">
    <button type="button" className="button dark day-chat-launcher" ref={launcher}
      aria-label={t("Ask Daythread")} title={t("Ask Daythread")} aria-controls={id} aria-expanded={open} aria-haspopup="dialog"
      onClick={() => { if (open) close(); else { setStarted(true); setOpen(true); } }}>
      <Icon name={open ? "close" : "chat"} size={24} />
    </button>
    <div id={id} className="day-chat-window" role="dialog" aria-label={t("Ask Daythread")} tabIndex={-1} ref={panel} hidden={!open}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !event.defaultPrevented && !(event.target instanceof Element && event.target.closest("dialog[open]"))) {
          event.preventDefault(); close();
        }
      }}>
      {started ? <Chat timezone={timezone} initialScope={{ kind: "day", day }} dayChat={{ day, filtered, onClose: close }} /> : null}
    </div>
  </div>;
}
