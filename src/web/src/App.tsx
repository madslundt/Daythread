import { sendHintEvent, meetingCaptureDevice } from "./hintActions";
import { t } from "./i18n";
import { resolveProfile, savedProfile, selectProfile, type Profile, type ProfileDirectory } from "./profiles";
import { ImportanceProvider } from "./Importance";
import { useEffect, useState } from "react";
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
import { BrowserRecorder } from "./BrowserRecorder";
import { errorMessage, newId } from "./api";
import { localDay, remainingCooldown } from "./format";
import { Icon } from "./Icon";
import { Navigation, pages, type Page } from "./Navigation";
import { dayFromPath, isDayPage, pageFromPath, pagePath, profileRoute, navigatePath, subscribeNavigation } from "./routing";
import type { ChatScope, Status, Timeline } from "./types";
import { useResource } from "./useResource";
import { Notice } from "./UI";
import { useInterfaceLanguage } from "./i18n";
import { ManagedAccountBadge, ManagedAccountProvider, ManagedArchiveGate, useManagedAccount } from "./ManagedAccount";
import { managedServiceUrl } from "./managedService";

export default function App() {
  useInterfaceLanguage();
  return <ManagedAccountProvider><ManagedArchiveGate><ArchiveApp /></ManagedArchiveGate></ManagedAccountProvider>;
}

function ArchiveApp() {
  useInterfaceLanguage();
  const directory = useResource<ProfileDirectory>("/v1/profiles");
  const [selected, setSelected] = useState<string | null>();
  useEffect(() => {
    if (!directory.data || selected !== undefined) return;
    const id = resolveProfile(directory.data.profiles, savedProfile());
    selectProfile(id ?? "");
    setSelected(id);
  }, [directory.data, selected]);
  function choose(id: string | null) {
    selectProfile(id ?? "");
    setSelected(id);
  }
  function created(profile: Profile) {
    directory.setData((current) => current ? { ...current, profiles: [...current.profiles, profile] } : current);
  }
  function updated(profile: Profile) {
    directory.setData((current) => current ? { ...current, profiles: current.profiles.map((item) => item.id === profile.id ? profile : item) } : current);
  }
  if (selected === undefined && !directory.error) return <div className="app-initial-loading" role="status" aria-label={t("Loading profiles…")}>
    <img src="/icons/daythread-192.png?v=daythread-d-20261017" alt="Daythread" width="64" height="64" />
  </div>;
  return <ImportanceProvider key={selected ?? "unselected"} enabled={Boolean(selected)}>
    <ProfileApp profiles={directory.data?.profiles ?? []} selected={selected ?? null} onSelect={choose}
      onCreate={created} onUpdate={updated} profileError={directory.error} onRetryProfiles={directory.refresh} />
  </ImportanceProvider>;
}

function ProfileApp({ profiles, selected, onSelect, onCreate, onUpdate, profileError, onRetryProfiles }: {
  profiles: Profile[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onCreate: (profile: Profile) => void;
  onUpdate: (profile: Profile) => void;
  profileError: string;
  onRetryProfiles: () => void;
}) {
  const language = useInterfaceLanguage();
  const { managed, account } = useManagedAccount();
  const recordingOwner = JSON.stringify([account?.id ?? "local", selected]);
  const [statusInterval, setStatusInterval] = useState(30_000);
  const status = useResource<Status>(selected ? "/v1/status" : null, selected ? statusInterval : 0);
  const processingBusy = Boolean(status.data && (
    (status.data.processing?.queued || 0) + (status.data.processing?.transcribing || 0) +
    (status.data.generation?.queued || 0) + (status.data.generation?.running || 0) > 0 ||
    status.data.phones?.some((phone) => phone.fresh && phone.capture_state === "capturing")
  ));
  useEffect(() => setStatusInterval(processingBusy ? 2_000 : 30_000), [processingBusy]);
  const timeline = useResource<Timeline>(selected ? "/v1/timeline" : null, selected ? 15_000 : 0);
  const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const timezone = status.data?.timezone || browserTimezone;
  const [page, setPage] = useState<Page>(() => pageFromPath(profileRoute(window.location.pathname, selected)));
  // An absent date means Today; resolve it in the configured timezone on every render.
  const [selectedDay, setSelectedDay] = useState(() => dayFromPath(window.location.pathname));
  const today = localDay(timezone);
  const day = selectedDay ?? today;
  const hrefForPage = (next: Page) => pagePath(next, next === "today" ? undefined : selectedDay);
  const [prepOpen, setPrepOpen] = useState(false);
  const [prepDirty, setPrepDirty] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[] | null>(null);
  const [version, setVersion] = useState(0);
  const [rewindIntent, setRewindIntent] = useState<RewindIntent>();
  const [chatIntent, setChatIntent] = useState<{
    scope: ChatScope;
    question?: string;
    id: number;
  }>();
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const sync = () => {
      const path = profileRoute(window.location.pathname, selected);
      if (path !== window.location.pathname) {
        navigatePath(path, { replace: true });
        return;
      }
      const next = pageFromPath(path);
      setPage(next);
      if (isDayPage(next)) setSelectedDay(dayFromPath(window.location.pathname));
    };
    sync();
    return subscribeNavigation(sync);
  }, [selected]);
  useEffect(() => {
    const update = () => {
      status.refresh();
      timeline.refresh();
    };
    window.addEventListener("omi:archive-changed", update);
    return () => window.removeEventListener("omi:archive-changed", update);
  }, [status.refresh, timeline.refresh]);
  function navigate(next: Page, path = hrefForPage(next)) {
    if (prepOpen && prepDirty && !window.confirm(t("Discard this preparation and unsaved context?"))) return;
    setPrepOpen(false);
    setChatIntent(undefined);
    if (next === "today") setSelectedDay(undefined);
    const allowed = profileRoute(path, selected);
    setPage(pageFromPath(allowed));
    navigatePath(allowed);
  }
  function selectDay(value: string) {
    if (value) {
      const selection = value === today ? undefined : value;
      setSelectedDay(selection);
      navigatePath(pagePath(page, selection));
    }
  }
  function uploaded() {
    status.refresh();
    timeline.refresh();
    setVersion((value) => value + 1);
    setNotice(
      t("Audio uploaded. Follow transcription progress under Capture & sync."),
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
  useEffect(() => { document.title = t("{value0} · Daythread", { value0: prepOpen ? t("Prepare") : t(current.label) }); }, [current.label, prepOpen, language]);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); document.getElementById("main-content")?.focus(); }}>{t("Skip to content")}</a>
      <aside className="sidebar">
        <a
          className="brand"
          href={hrefForPage("today")}
          onClick={(event) => { event.preventDefault(); navigate("today"); }}
          aria-label={t("Daythread home")}
        >
          <img className="brand-icon" src="/icons/daythread-192.png?v=daythread-d-20261017" alt="" width="32" height="32" />
          <span>{t("Daythread")}</span>
        </a>
        <Navigation page={page} navigate={navigate} hrefForPage={hrefForPage} />
        <div className="sidebar-footer">
          <ManagedAccountBadge placement="sidebar" />
          <div className="backend-indicator">
            <span
              className={`status-dot ${status.data && !status.error ? "online" : ""}`}
            />
            {status.error
              ? t("Backend offline")
              : !selected
                ? t("Choose a profile in settings")
                : status.data ? managed ? t("Managed hosting") : t("Local backend") : t("Connecting…")}
          </div>
        </div>
      </aside>
      <main className="main-canvas" data-page={page} id="main-content" tabIndex={-1}
        onDragOver={(event) => {
          if (page !== "today" || prepOpen || !event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = uploadFiles === null ? "copy" : "none";
        }}
        onDrop={(event) => {
          if (page !== "today" || prepOpen || !event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          if (uploadFiles === null && event.dataTransfer.files.length)
            setUploadFiles(Array.from(event.dataTransfer.files));
        }}>
        <header className={`page-header ${page === "today" && !prepOpen ? "today-header" : ""}`}>
          <div className="topbar">
            <a
              className="brand mobile-brand"
              href={hrefForPage("today")}
              onClick={(event) => { event.preventDefault(); navigate("today"); }}
            >
              <img className="brand-icon" src="/icons/daythread-192.png?v=daythread-d-20261017" alt="" width="28" height="28" />
              <span>{t("Daythread")}</span>
            </a>
            <ManagedAccountBadge placement="header" />
            {showDate ? (
              <DateNavigator
                day={day}
                today={today}
                onDay={selectDay}
              />
            ) : (
              <span className="desktop-spacer" />
            )}
            {page === "today" ? (
              <div className="desktop-actions">
                <HintActions
                  timeline={timeline.data}
                  policy={status.data?.hint_policy}
                  captureDeviceID={meetingCaptureDevice(status.data, status.error)}
                  onTimeline={timeline.setData}
                  onMessage={setNotice}
                />
              </div>
            ) : null}
          </div>
          <div className="intro">
            {page !== "today" || prepOpen ? <span className="eyebrow intro-eyebrow">{prepOpen ? t("Look ahead") : t(current.label)}</span> : null}
            <h1>{prepOpen ? t("Prepare for what comes next.") : page === "today" ? day === localDay(timezone) ? t("Today") : t("Your day") : t(current.title)}</h1>
          </div>
          {page === "today" ? (
            <div className="mobile-actions">
              <HintActions
                timeline={timeline.data}
                policy={status.data?.hint_policy}
                captureDeviceID={meetingCaptureDevice(status.data, status.error)}
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
              {t("Try again")}{" "}</button>
          </Notice>
        ) : null}
        {notice ? (
          <Notice onDismiss={() => setNotice("")}>{notice}</Notice>
        ) : null}
        {managed && account && !account.hosting_active ? <Notice>
          {t("Hosting is inactive. Your saved archive is still available.")}{" "}
          <a className="text-link" href={managedServiceUrl("/account")}>{t("Manage account")}</a>
        </Notice> : null}
        {selected ? <BrowserRecorder key={recordingOwner} owner={recordingOwner} timezone={timezone}
          visible={page === "today" && !prepOpen} onUpload={() => setUploadFiles([])} onUploaded={uploaded} /> : null}
        {timeline.data?.active_meeting ? (
          <ActiveMeeting captureDeviceID={timeline.data.active_meeting.meeting_capture_device_id} />
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
            onUpload={() => setUploadFiles([])}
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
            profiles={profiles} selectedProfile={selected} onCreateProfile={onCreate}
            onUpdateProfile={onUpdate} onSelectProfile={onSelect}
            profileError={profileError} onRetryProfiles={onRetryProfiles}
            onSaved={() => {
              status.refresh();
              timeline.refresh();
            }}
          />
        )}
      </main>
      <Navigation page={page} navigate={navigate} hrefForPage={hrefForPage} mobile />
      {uploadFiles !== null ? (
        <UploadDialog
          initialFiles={uploadFiles}
          onClose={() => setUploadFiles(null)}
          onUploaded={uploaded}
        />
      ) : null}
    </div>
  );
}

function HintActions({
  timeline,
  policy,
  captureDeviceID,
  onTimeline,
  onMessage,
}: {
  timeline?: Timeline;
  policy?: Status["hint_policy"];
  captureDeviceID?: string;
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
      const result = await sendHintEvent(action, eventId, active?.id, captureDeviceID);
      onTimeline(result);
      setNow(Date.now());
      const markAccepted =
        action !== "mark" ||
        result.markers.some((marker) => marker.event_id === eventId);
      const minimum = policy?.meeting_min_seconds ?? 60;
      onMessage(
        action === "mark"
          ? markAccepted
            ? t("Important moment marked. Nearby speech will be treated as context.")
            : t("A nearby moment was already marked. This click was ignored during the flag cooldown.")
          : active
            ? Date.now() < Date.parse(active.qualifies_at)
              ? t("Meeting hint ignored because it lasted less than {value0} seconds. Your recording is kept.", { value0: minimum })
              : t("Meeting hint ended. Its timing guides interpretation of nearby speech.")
            : t("Meeting hint started. Hints shorter than {value0} seconds are ignored.", { value0: minimum }),
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
            ? t("Another flag can be added in {value0} seconds", { value0: remaining })
            : t("Mark the surrounding conversation as important")
        }
      >
        <Icon name="star" />
        {remaining ? t("Marked · wait {value0}s", { value0: remaining }) : t("Mark important")}
      </button>
      <button
        className={`button ${active ? "dark" : "accent"}`}
        onClick={() => void event("meeting_toggle")}
        title={!active && !captureDeviceID ? t("Automatic ending needs one currently recording Omi device. Otherwise, end the meeting manually.") : undefined}
        disabled={busy || !timeline}
      >
        <Icon name="mic" />
        {active ? t("End meeting") : t("Start meeting")}
      </button>
    </div>
  );
}

export function ActiveMeeting({ captureDeviceID }: { captureDeviceID?: string | null }) {
  return <div className="meeting-notice">
    <Icon name="mic" size={17} />
    <span>{t("Meeting in progress")}{" · "}{captureDeviceID ? t("End meeting when you’re done. After the first hour, recorded quiet can end it automatically. Recording continues.")
      : t("End this meeting manually. No live Omi recording was linked when it started. Recording continues.")}</span>
  </div>;
}
