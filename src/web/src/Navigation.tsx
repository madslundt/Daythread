import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export type Page = "inbox" | "today" | "rewind" | "diary" | "chat" | "settings" | "labels";
export const pages: {
  id: Page;
  label: string;
  icon: IconName;
  title: string;
  subtitle: string;
}[] = [
  {
    id: "today",
    label: "Today",
    icon: "home",
    title: "Your day, remembered.",
    subtitle: "A little context for everything you heard.",
  },
  {
    id: "rewind",
    label: "Rewind",
    icon: "rewind",
    title: "Come back to a moment.",
    subtitle: "Listen again, with a little context.",
  },
  {
    id: "diary",
    label: "Diary",
    icon: "diary",
    title: "The days add up.",
    subtitle: "Your daily notes, kept for what comes next.",
  },
  {
    id: "chat",
    label: "Chat",
    icon: "chat",
    title: "A conversation with your days.",
    subtitle: "Ask a question. Follow it back to the source.",
  },
  { id: "labels", label: "People", icon: "people", title: "People & projects.", subtitle: "Follow related recordings across your days." },
  {
    id: "inbox",
    label: "Inbox",
    icon: "inbox",
    title: "Follow through.",
    subtitle: "Promises and open questions, across your days.",
  },
  {
    id: "settings",
    label: "Settings",
    icon: "settings",
    title: "Settings",
    subtitle: "Preferences, connections, and local AI shared by all profiles.",
  },
];

const primaryPages = pages.slice(0, 4);
const morePages = pages.slice(4);

export function Navigation({ page, navigate, hrefForPage, mobile = false, profileSelector }: {
  page: Page;
  navigate: (page: Page) => void;
  hrefForPage?: (page: Page) => string;
  mobile?: boolean;
  profileSelector?: ReactNode;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!moreOpen) return;
    const element = dialog.current;
    element?.showModal();
    // Switching to a wide layout must not leave the desktop behind a modal.
    const media = window.matchMedia("(min-width: 761px)");
    const close = () => { if (media.matches) setMoreOpen(false); };
    media.addEventListener("change", close);
    return () => { element?.close(); media.removeEventListener("change", close); trigger.current?.focus(); };
  }, [moreOpen]);
  useEffect(() => { setMoreOpen(false); }, [page]);
  const moreActive = morePages.some((item) => item.id === page);
  const link = (item: typeof pages[number]) => (
    <a href={hrefForPage?.(item.id) ?? `/${item.id}`} key={item.id}
      onClick={(event) => { event.preventDefault(); setMoreOpen(false); navigate(item.id); }}
      className={page === item.id ? "active" : ""}
      aria-current={page === item.id ? "page" : undefined}>
      <Icon name={item.icon} size={21} /><span>{item.label}</span>
    </a>
  );
  return <>
    <nav className={mobile ? "mobile-nav" : "desktop-nav"} aria-label={mobile ? "Mobile navigation" : "Main navigation"}>
      {(mobile ? primaryPages : pages).map(link)}
      {mobile ? <button ref={trigger} className={moreActive || moreOpen ? "active" : ""}
        aria-label="More navigation" aria-haspopup="dialog" aria-expanded={moreOpen}
        onClick={() => setMoreOpen(true)}><Icon name="more" size={21} /><span>More</span></button> : null}
    </nav>
    {mobile && moreOpen ? <dialog ref={dialog} className="navigation-dialog" aria-labelledby="more-title"
      onCancel={(event) => { event.preventDefault(); setMoreOpen(false); }}
      onClick={(event) => { if (event.target === event.currentTarget) setMoreOpen(false); }}>
      <div className="navigation-dialog-content">
        <div className="section-heading"><h2 id="more-title">Your space</h2>
          <button className="icon-button" aria-label="Close navigation" onClick={() => setMoreOpen(false)}><Icon name="close" /></button>
        </div>
        <nav aria-label="More navigation">{morePages.map(link)}</nav>
        <p className="caption">Keep track of people, follow through, and make omi yours.</p>
        {profileSelector ? <div className="navigation-profile">{profileSelector}</div> : null}
      </div>
    </dialog> : null}
  </>;
}
