import { useEffect, useRef, useState } from "react";
import { ConversationPrep } from "./ConversationPrep";
import { Labels } from "./Labels";
import { Chat } from "./Chat";
import { Inbox } from "./Inbox";
import { DateNavigator } from "./Calendar";
import { Diary } from "./Diary";
import { Rewind, type RewindIntent } from "./Rewind";
import { Settings } from "./Settings";
import { Today } from "./Today";
import { UploadDialog } from "./UploadDialog";
import { errorMessage, newId, post } from "./api";
import { localDay, remainingCooldown, timeLabel } from "./format";
import { Icon } from "./Icon";
import { Navigation, pages, type Page } from "./Navigation";
import type { ChatScope, Status, Timeline } from "./types";
import { useResource } from "./useResource";
import { Notice } from "./UI";

function pageFromHash(): Page {
  const key = window.location.hash.slice(1).split("/")[0];
  return pages.some((page) => page.id === key) ? (key as Page) : "today";
}

export default function App() {
  const status = useResource<Status>("/v1/status", 30_000);
  const timeline = useResource<Timeline>("/v1/timeline", 15_000);
  const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const timezone = status.data?.timezone || browserTimezone;
  const [page, setPage] = useState<Page>(pageFromHash);
  const [day, setDay] = useState(() => localDay(browserTimezone));
  const daySelected = useRef(false);
  const [prepOpen, setPrepOpen] = useState(false);
  const [prepDirty, setPrepDirty] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [version, setVersion] = useState(0);
  const [rewindIntent, setRewindIntent] = useState<RewindIntent>();
  const [chatIntent, setChatIntent] = useState<{
    scope: ChatScope;
    question?: string;
    id: number;
  }>();
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const sync = () => setPage(pageFromHash());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  useEffect(() => {
    if (status.data?.timezone && !daySelected.current)
      setDay(localDay(status.data.timezone));
  }, [status.data?.timezone]);
  useEffect(() => {
    const update = () => {
      status.refresh();
      timeline.refresh();
    };
    window.addEventListener("omi:archive-changed", update);
    return () => window.removeEventListener("omi:archive-changed", update);
  }, [status.refresh, timeline.refresh]);
  function navigate(next: Page) {
    if (prepOpen && prepDirty && !window.confirm("Discard this preparation and unsaved context?")) return;
    setPrepOpen(false);
    setChatIntent(undefined);
    setPage(next);
    window.location.hash = next;
  }
  function selectDay(value: string) {
    if (value) {
      daySelected.current = true;
      setDay(value);
    }
  }
  function uploaded() {
    status.refresh();
    timeline.refresh();
    setVersion((value) => value + 1);
    setNotice(
      "Recording uploaded. Your local worker will transcribe it when ready.",
    );
  }
  function rewind(minutes: number) {
    setRewindIntent({ minutes, id: Date.now() });
    navigate("rewind");
  }
  function openChat(scope: ChatScope, question?: string) {
    navigate("chat");
    setChatIntent({ scope, question, id: Date.now() });
  }
  const current = pages.find((item) => item.id === page)!;
  const showDate = page === "today" || page === "diary";
  useEffect(() => { window.scrollTo(0, 0); }, [page]);
  useEffect(() => { document.title = `${prepOpen ? "Prepare" : current.label} · omi`; }, [current.label, prepOpen]);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); document.getElementById("main-content")?.focus(); }}>Skip to content</a>
      <aside className="sidebar">
        <a
          className="brand"
          href="#today"
          onClick={(event) => { event.preventDefault(); navigate("today"); }}
          aria-label="omi home"
        >
          omi<span className="brand-caption">A little more remembered.</span>
        </a>
        <Navigation page={page} navigate={navigate} />
        <div className="backend-indicator">
          <span
            className={`status-dot ${status.data && !status.error ? "online" : ""}`}
          />
          {status.error
            ? "Backend offline"
            : status.data
              ? "Local backend"
              : "Connecting…"}
        </div>
      </aside>
      <main className="main-canvas" data-page={page} id="main-content" tabIndex={-1}>
        <header className="page-header">
          <div className="topbar">
            <a
              className="brand mobile-brand"
              href="#today"
              onClick={(event) => { event.preventDefault(); navigate("today"); }}
            >
              omi
            </a>
            {showDate ? (
              <DateNavigator
                day={day}
                today={localDay(timezone)}
                onDay={selectDay}
              />
            ) : (
              <span className="desktop-spacer eyebrow">Your private memory / {current.label}</span>
            )}
            {page === "today" ? (
              <div className="desktop-actions">
                <HintActions
                  timeline={timeline.data}
                  policy={status.data?.hint_policy}
                  onTimeline={timeline.setData}
                  onMessage={setNotice}
                />
              </div>
            ) : null}
          </div>
          <div className="intro">
            <span className="eyebrow intro-eyebrow">{prepOpen ? "Look ahead" : current.label === "Today" ? "A space for your everyday" : current.label}</span>
            <h1>{prepOpen ? "Prepare for what comes next." : current.title}</h1>
            <p>{prepOpen ? "A little context, with the evidence beside it." : current.subtitle}</p>
          </div>
          {page === "today" ? (
            <div className="mobile-actions">
              <HintActions
                timeline={timeline.data}
                policy={status.data?.hint_policy}
                onTimeline={timeline.setData}
                onMessage={setNotice}
              />
            </div>
          ) : null}
        </header>
        {status.error ? (
          <Notice tone="error">
            {status.error}
            <button className="text-button" onClick={status.refresh}>
              Try again
            </button>
          </Notice>
        ) : null}
        {notice ? (
          <Notice onDismiss={() => setNotice("")}>{notice}</Notice>
        ) : null}
        {timeline.data?.active_meeting ? (
          <ActiveMeeting timeline={timeline.data} timezone={timezone} />
        ) : null}
        {prepOpen ? (
          <ConversationPrep timezone={timezone} onDirtyChange={setPrepDirty} onClose={() => { setPrepOpen(false); setPrepDirty(false); }} />
        ) : page === "today" ? (
          <Today
            key={`${day}-${version}`}
            day={day}
            timezone={timezone}
            status={status.data}
            statusError={status.error}
            timeline={timeline.data}
            onPrepare={() => setPrepOpen(true)}
            onUpload={() => setUploadOpen(true)}
            onRewind={rewind}
            onChat={openChat}
          />
        ) : page === "rewind" ? (
          <Rewind timezone={timezone} intent={rewindIntent} />
        ) : page === "diary" ? (
          <Diary
            day={day}
            timezone={timezone}
            onDay={selectDay}
            onChat={openChat}
          />
        ) : page === "inbox" ? (
          <Inbox timezone={timezone} />
        ) : page === "labels" ? (
          <Labels timezone={timezone} />
        ) : page === "chat" ? (
          <Chat
            key={chatIntent?.id}
            timezone={timezone}
            initialScope={chatIntent?.scope}
            initialQuestion={chatIntent?.question}
          />
        ) : (
          <Settings
            onSaved={() => {
              status.refresh();
              timeline.refresh();
            }}
          />
        )}
      </main>
      <Navigation page={page} navigate={navigate} mobile />
      {uploadOpen ? (
        <UploadDialog
          onClose={() => setUploadOpen(false)}
          onUploaded={uploaded}
        />
      ) : null}
    </div>
  );
}

function HintActions({
  timeline,
  policy,
  onTimeline,
  onMessage,
}: {
  timeline?: Timeline;
  policy?: Status["hint_policy"];
  onTimeline: (timeline: Timeline) => void;
  onMessage: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const lastMarker =
    timeline?.markers[timeline.markers.length - 1]?.occurred_at;
  const remaining = remainingCooldown(
    lastMarker,
    policy?.marker_cooldown_seconds ?? 60,
    now,
  );
  const active = timeline?.active_meeting;
  async function event(action: "mark" | "meeting_toggle") {
    setBusy(true);
    try {
      const eventId = newId();
      const result = await post<Timeline>("/v1/events", {
        event_id: eventId,
        device_id: "web",
        occurred_at: new Date().toISOString(),
        action,
      });
      onTimeline(result);
      setNow(Date.now());
      const markAccepted =
        action !== "mark" ||
        result.markers.some((marker) => marker.event_id === eventId);
      const minimum = policy?.meeting_min_seconds ?? 60;
      onMessage(
        action === "mark"
          ? markAccepted
            ? "Important moment marked. Nearby speech will be treated as context."
            : "A nearby moment was already marked. This click was ignored during the flag cooldown."
          : active
            ? Date.now() < Date.parse(active.qualifies_at)
              ? `Meeting hint ignored because it lasted less than ${minimum} seconds. Your recording is kept.`
              : "Meeting hint ended. Its timing guides interpretation of nearby speech."
            : `Meeting hint started. Hints shorter than ${minimum} seconds are ignored.`,
      );
    } catch (error) {
      onMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="hint-actions">
      <button
        className="button"
        onClick={() => void event("mark")}
        disabled={busy || !timeline || remaining > 0}
        title={
          remaining
            ? `Another flag can be added in ${remaining} seconds`
            : "Mark the surrounding conversation as important"
        }
      >
        <Icon name="star" />
        {remaining ? `Marked · wait ${remaining}s` : "Mark important"}
      </button>
      <button
        className={`button ${active ? "dark" : "accent"}`}
        onClick={() => void event("meeting_toggle")}
        disabled={busy || !timeline}
      >
        <Icon name="mic" />
        {active ? "End meeting" : "Start meeting"}
      </button>
    </div>
  );
}

function ActiveMeeting({
  timeline,
  timezone,
}: {
  timeline: Timeline;
  timezone: string;
}) {
  const active = timeline.active_meeting!;
  return (
    <div className="meeting-notice">
      <Icon name="mic" size={17} />
      <span>
        Meeting hint in progress · auto stops at{" "}
        {timeLabel(active.auto_stop_at, timezone)}. Timing is approximate; short
        meeting hints are ignored.
      </span>
    </div>
  );
}
