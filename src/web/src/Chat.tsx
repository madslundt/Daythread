import { t } from "./i18n";
import { profileStorageKey } from "./profiles";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { errorMessage } from "./api";
import { ChatAnswer } from "./ChatAnswer";
import { ChatMarkdown } from "./ChatMarkdown";
import { exchangeMessages, streamChat } from "./chatStream";
import type { ChatResult } from "./chatStream";
import { dateRangeForShortcut, dateRangeShortcuts, dateTimeLabel, dayLabel, localDay, timeLabel } from "./format";
import { Icon } from "./Icon";
import { EvidenceFeedback } from "./CompletenessFeedback";
import type { ChatMessage, ChatScope, ChatSession, Conversation, Timeline } from "./types";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading } from "./UI";

const EMPTY_MESSAGES: ChatMessage[] = [];
const desktopChat = "(min-width: 1101px)";

function MessageLabel({ user, timestamp, timezone }: { user?: boolean; timestamp?: string; timezone: string }) {
  return <div className="message-label">
    {!user && <span className="chat-avatar"><Icon name="chat" size={14} /></span>}
    <strong>{user ? t("You") : t("Daythread")}</strong>
    {timestamp && Number.isFinite(Date.parse(timestamp)) ? <time dateTime={timestamp} title={dateTimeLabel(timestamp, timezone)}>
      {timeLabel(timestamp, timezone)}
    </time> : null}
  </div>;
}

function savedSession(): string | undefined {
  try {
    return localStorage.getItem(profileStorageKey("omi.chat-session.v1")) || undefined;
  } catch {
    return undefined;
  }
}

function sessionScope(session?: ChatSession): ChatScope {
  if (session?.meeting_id) return { kind: "meeting", meeting_id: session.meeting_id };
  if (session?.recording_id) return { kind: "recording", recording_id: session.recording_id };
  if (session?.day) return { kind: "day", day: session.day };
  if (session?.start_day && session.end_day) {
    return { kind: "range", start_day: session.start_day, end_day: session.end_day };
  }
  return { kind: "all" };
}

export function Chat({ timezone, initialScope, initialQuestion, dayChat }: {
  timezone: string; initialScope?: ChatScope; initialQuestion?: string;
  dayChat?: { day: string; filtered: boolean; onClose: () => void };
}) {
  const embedded = Boolean(dayChat);
  const storageKey = useRef(profileStorageKey("omi.chat-session.v1")).current;
  const [sessionId, setSessionId] = useState<string | undefined>(() => initialScope ? undefined : savedSession());
  const [draftScope, setDraftScope] = useState<ChatScope>(initialScope ?? { kind: "all" });
  const sessions = useResource<{ items: ChatSession[] }>(dayChat ? null : "/v1/chat/sessions");
  const timeline = useResource<Timeline>(dayChat ? null : "/v1/timeline", dayChat ? 0 : 15_000);
  const conversation = useResource<Conversation>(
    sessionId ? `/v1/chat/sessions/${encodeURIComponent(sessionId)}` : null,
  );
  const [question, setQuestion] = useState(() => (initialQuestion || "").slice(0, 2000));
  const [sending, setSending] = useState("");
  const [error, setError] = useState("");
  const [streamedText, setStreamedText] = useState("");
  const [stage, setStage] = useState("searching");
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  const [completed, setCompleted] = useState<{ result: ChatResult; question: string }>();
  const activeRequest = useRef<AbortController | null>(null);
  const pendingText = useRef("");
  const flushTimer = useRef<number | undefined>(undefined);
  const history = useRef<HTMLDetailsElement>(null);
  const scopeDetails = useRef<HTMLDetailsElement>(null);
  const messageViewport = useRef<HTMLDivElement>(null);
  const messageList = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const pauseFollowing = useCallback(() => { followLatest.current = false; }, []);
  const composer = useRef<HTMLTextAreaElement>(null);
  useEffect(() => () => {
    activeRequest.current?.abort();
    window.clearTimeout(flushTimer.current);
  }, []);
  const messages = conversation.data?.messages ?? EMPTY_MESSAGES;
  const visibleMessages = completed && completed.result.session_id === sessionId
    && !messages.some((message) => message.id === completed.result.message_id)
    ? [...messages, ...exchangeMessages(completed.result, completed.question)] : messages;
  useEffect(() => {
    if (completed && messages.some((message) => message.id === completed.result.message_id)) setCompleted(undefined);
  }, [completed, messages]);
  useEffect(() => {
    if (initialQuestion) composer.current?.focus({ preventScroll: true });
  }, [initialQuestion]);
  const activeSession = conversation.data ?? sessions.data?.items.find((session) => session.id === sessionId);
  const scope = sessionId && (activeSession || !dayChat) ? sessionScope(activeSession) : draftScope;
  const meetings = timeline.data?.meetings ?? [];
  const selectedMeeting = scope.kind === "meeting" ? meetings.find((meeting) => meeting.id === scope.meeting_id) : undefined;
  const today = localDay(timezone);
  const scopeLabel = scope.kind === "day"
    ? dayLabel(scope.day, true)
    : scope.kind === "range"
      ? `${dayLabel(scope.start_day, true)} – ${dayLabel(scope.end_day, true)}`
    : scope.kind === "meeting"
      ? selectedMeeting ? t("Meeting · {value0}", { value0: dateTimeLabel(selectedMeeting.started_at, timezone) }) : t("Selected meeting")
      : scope.kind === "recording" ? t("This recording only") : t("All diaries (all days)");
  useEffect(() => {
    if (embedded) return;
    try {
      if (sessionId) localStorage.setItem(storageKey, sessionId);
      else localStorage.removeItem(storageKey);
    } catch {
      /* Storage may be disabled; sessions still persist on the backend. */
    }
  }, [sessionId, storageKey, embedded]);
  useLayoutEffect(() => {
    if (history.current) history.current.open = window.matchMedia(desktopChat).matches;
  }, []);
  // Follow streaming replies only while the reader is already at the bottom.
  // Observe size changes as the answer grows or the composer resizes.
  useLayoutEffect(() => {
    const viewport = messageViewport.current;
    const list = messageList.current;
    if (!viewport || !list) return;
    const observer = new ResizeObserver(() => {
      if (followLatest.current) viewport.scrollTop = viewport.scrollHeight;
      else {
        followLatest.current = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= 48;
        setAwayFromLatest(!followLatest.current);
      }
    });
    observer.observe(viewport);
    observer.observe(list);
    return () => observer.disconnect();
  }, []);
  function jumpToLatest() {
    followLatest.current = true;
    setAwayFromLatest(false);
    const viewport = messageViewport.current;
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }
  async function send(event: React.FormEvent) {
    event.preventDefault();
    const value = question.trim();
    if (!value || activeRequest.current || sending || conversation.loading || (sessionId && !conversation.data)) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    pendingText.current = "";
    setStreamedText("");
    setStage("searching");
    if (scopeDetails.current) scopeDetails.current.open = false;
    composer.current?.focus({ preventScroll: true });
    jumpToLatest();
    setSending(value);
    setError("");
    try {
      const result = await streamChat({
        question: value,
        ...(sessionId ? { session_id: sessionId } : scope.kind === "day"
          ? { day: scope.day }
          : scope.kind === "range" ? { start_day: scope.start_day, end_day: scope.end_day }
          : scope.kind === "meeting" ? { meeting_id: scope.meeting_id }
          : scope.kind === "recording" ? { recording_id: scope.recording_id } : {}),
      }, (event) => {
        if (controller.signal.aborted) return;
        if (event.type === "status") setStage(event.stage);
        else {
          pendingText.current += event.text;
          if (flushTimer.current === undefined) flushTimer.current = window.setTimeout(() => {
            setStreamedText(pendingText.current);
            flushTimer.current = undefined;
          }, 40);
        }
      }, controller.signal);
      if (controller.signal.aborted) return;
      setCompleted({ result, question: value });
      if (conversation.data && sessionId === result.session_id) {
        conversation.setData({ ...conversation.data, messages: [...messages, ...exchangeMessages(result, value)] });
      }
      setQuestion("");
      setSessionId(result.session_id);
      if (!dayChat) sessions.refresh();
    } catch (error) {
      if (!controller.signal.aborted) setError(errorMessage(error));
    } finally {
      window.clearTimeout(flushTimer.current);
      flushTimer.current = undefined;
      pendingText.current = "";
      setStreamedText("");
      setSending("");
      activeRequest.current = null;
    }
  }
  function select(id?: string) {
    if (history.current && !window.matchMedia(desktopChat).matches) history.current.open = false;
    if (id && id === sessionId) {
      messageViewport.current?.focus({ preventScroll: true });
      return;
    }
    if (scopeDetails.current) scopeDetails.current.open = false;
    jumpToLatest();
    if (!id) setDraftScope(scope);
    setSessionId(id);
    setError("");
    setCompleted(undefined);
    setQuestion("");
    if (id) messageViewport.current?.focus({ preventScroll: true });
    else composer.current?.focus({ preventScroll: true });
  }
  function changeScope(next: ChatScope) {
    if (JSON.stringify(next) === JSON.stringify(scope)) return;
    jumpToLatest();
    setDraftScope(next);
    setSessionId(undefined);
    setError("");
    setCompleted(undefined);
  }
  return (
    <div className={dayChat ? "chat-layout chat-layout-embedded" : "chat-layout"}>
      {!dayChat ? <details className="chat-index" ref={history}>
        <summary className="chat-index-heading">
          <Icon name="chat" size={18} />
          <h2>{t("Conversations")}</h2>
          {sessions.data?.items.length ? <span aria-label={t("{value0} saved conversations", { value0: sessions.data.items.length })}>{sessions.data.items.length}</span> : null}
          <Icon name="chevron-right" size={16} />
        </summary>
        <button
          type="button"
          className="button"
          onClick={() => select()}
          disabled={Boolean(sending)}
        >
          <Icon name="plus" size={17} />
          {t("New conversation")}{" "}</button>
        {sessions.error ? (
          <ErrorState message={sessions.error} retry={sessions.refresh} />
        ) : null}
        {sessions.loading && !sessions.data ? <Loading>{t("Loading conversations…")}</Loading> : null}
        <nav aria-label={t("Saved conversations")}>
          {sessions.data?.items.map((session) => (
            <button
              type="button"
              className={`conversation-link ${sessionId === session.id ? "selected" : ""}`}
              aria-current={sessionId === session.id ? "true" : undefined}
              key={session.id}
              onClick={() => select(session.id)}
              disabled={Boolean(sending)}
            >
              <span>{session.title}</span>
              <small>{session.day ? dayLabel(session.day, true)
                : session.start_day && session.end_day ? `${dayLabel(session.start_day, true)} – ${dayLabel(session.end_day, true)}`
                  : session.meeting_id ? t("Meeting") : session.recording_id ? t("Recording") : t("All days")}</small>
              <small>{dateTimeLabel(session.updated_at, timezone)}</small>
            </button>
          ))}
        </nav>
        {!sessions.loading && !sessions.error && !sessions.data?.items.length ? (
          <p className="caption">{t("Your conversations will appear here. They stay on your home-core.")}</p>
        ) : null}
      </details> : null}
      <section className="conversation-panel" aria-labelledby="chat-title">
        <header className="chat-thread-header">
          <div className="chat-thread-title">
            <h2 id="chat-title">{dayChat ? t("Ask Daythread") : activeSession?.title || (sessionId && conversation.loading ? t("Loading conversation…") : t("New conversation"))}</h2>
            {dayChat ? <button type="button" className="text-button day-chat-close" aria-label={t("Close")} onClick={dayChat.onClose}>
              <Icon name="close" size={18} />
            </button> : null}
          </div>
          <details className="chat-context" ref={scopeDetails}>
            <summary className="chat-context-summary">
              <Icon name="filter" size={16} />
              <span>{scopeLabel}</span>
              <Icon name="chevron-right" size={14} />
              <span className="sr-only">{t("Chat scope")}</span>
            </summary>
        <div className="chat-scope">
          {dayChat ? <fieldset className="chat-range-shortcuts" disabled={Boolean(sending) || conversation.loading}>
            <legend>{t("Chat with")}</legend>
            <button type="button" className="button small" onClick={() => changeScope({ kind: "day", day: dayChat.day })}>{t("This day")}</button>
            <button type="button" className="button small" onClick={() => changeScope({ kind: "range", ...dateRangeForShortcut("this_week", dayChat.day) })}>{t("This week")}</button>
            {scope.kind !== "range" ? <button type="button" className="button small" onClick={() => changeScope({ kind: "range",
              start_day: dayChat.day, end_day: dayChat.day })}>{t("Date range")}</button> : null}
          </fieldset> : null}
          {!dayChat ? <label>
            <span>{t("Chat with")}</span>
            <select
              aria-label={t("Chat scope")}
              value={scope.kind}
              disabled={Boolean(sending) || conversation.loading}
              onChange={(event) => {
                if (event.target.value === "day") changeScope({ kind: "day", day: localDay(timezone) });
                else if (event.target.value === "range") changeScope({ kind: "range", ...dateRangeForShortcut("this_week", today) });
                else if (event.target.value === "meeting" && meetings[0]) changeScope({ kind: "meeting", meeting_id: meetings[0].id });
                else changeScope({ kind: "all" });
              }}
            >
              <option value="all">{t("All diaries (all days)")}</option>
              <option value="day">{t("Specific date")}</option>
              <option value="range">{t("Date range")}</option>
              <option value="meeting" disabled={!meetings.length && scope.kind !== "meeting"}>{t("Meeting")}</option>
              {scope.kind === "recording" ? <option value="recording">{t("Recording")}</option> : null}
            </select>
          </label> : null}
          {!dayChat && scope.kind === "day" ? (
            <label>
              <span>{t("Date")}</span>
              <input type="date" aria-label={t("Chat date")} value={scope.day}
                disabled={Boolean(sending) || conversation.loading}
                onChange={(event) => { if (event.target.value) changeScope({ kind: "day", day: event.target.value }); }} />
            </label>
          ) : scope.kind === "range" ? (
            <>
              <label>
                <span>{t("From")}</span>
                <input type="date" aria-label={t("Chat start date")} value={scope.start_day}
                  disabled={Boolean(sending) || conversation.loading}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value) changeScope({ kind: "range", start_day: value, end_day: value > scope.end_day ? value : scope.end_day });
                  }} />
              </label>
              <label>
                <span>{t("Through")}</span>
                <input type="date" aria-label={t("Chat end date")} value={scope.end_day}
                  disabled={Boolean(sending) || conversation.loading}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value) changeScope({ kind: "range", start_day: value < scope.start_day ? value : scope.start_day, end_day: value });
                  }} />
              </label>
              {!dayChat ? <fieldset className="chat-range-shortcuts" disabled={Boolean(sending) || conversation.loading}>
                <legend>{t("Date shortcuts")}</legend>
                {dateRangeShortcuts.map((shortcut) => {
                  const range = dateRangeForShortcut(shortcut.id, today);
                  const selected = range.start_day === scope.start_day && range.end_day === scope.end_day;
                  return <button
                    type="button" className={`button small${selected ? " dark" : ""}`} key={shortcut.id}
                    aria-pressed={selected}
                    onClick={() => changeScope({ kind: "range", ...range })}
                  >{t(shortcut.label)}</button>;
                })}
              </fieldset> : null}
              <p className="caption">{t("Both dates included ·")}{" "}{timezone}{t(". Weeks run Monday–Sunday; shortcuts select full calendar periods.")}</p>
            </>
          ) : scope.kind === "meeting" ? (
            <label className="chat-meeting-picker">
              <span>{t("Meeting")}</span>
              <select aria-label={t("Chat meeting")} value={scope.meeting_id}
                disabled={Boolean(sending) || conversation.loading}
                onChange={(event) => changeScope({ kind: "meeting", meeting_id: event.target.value })}>
                {!selectedMeeting ? <option value={scope.meeting_id}>{t("Selected meeting")}</option> : null}
                {meetings.map((meeting) => <option key={meeting.id} value={meeting.id}>
                  {dateTimeLabel(meeting.started_at, timezone)}{meeting.ended_at ? "" : t("· in progress")}
                </option>)}
              </select>
            </label>
          ) : null}
          <p className="caption">{scopeLabel}{t(". Changing context starts a new conversation and keeps your draft question.")}</p>
          {timeline.error && scope.kind === "meeting" ? <ErrorState message={timeline.error} retry={timeline.refresh} /> : null}
          {scope.kind === "meeting" && !timeline.loading && !timeline.error && !meetings.length ? <p className="caption">{t("Meetings appear here after a meeting has been recorded.")}</p> : null}
        </div>
          </details>
          {dayChat?.filtered ? <p className="caption day-chat-filter-note">{t("Chat uses all entries in the chosen period, regardless of these filters.")}</p> : null}
        </header>
        <div className="chat-transcript">
        <div className="messages" ref={messageViewport} role="log" aria-label={t("Conversation messages")} aria-relevant="additions text" tabIndex={0} onScroll={(event) => {
          const viewport = event.currentTarget;
          followLatest.current = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= 48;
          setAwayFromLatest(!followLatest.current);
        }}>
          <div className="chat-message-list" ref={messageList}>
          {conversation.loading && !visibleMessages.length ? (
            <Loading>{t("Loading conversation…")}</Loading>
          ) : visibleMessages.length ? (
            visibleMessages.map((message) => (
              <article
                className={`chat-message ${message.role}`}
                key={message.id}
              >
                <MessageLabel user={message.role === "user"} timestamp={message.created_at} timezone={timezone} />
                {message.role === "assistant" ? <ChatAnswer message={message} timezone={timezone} onInspect={pauseFollowing} />
                  : <p className="chat-question">{message.content}</p>}
                {message.role === "assistant" ? <details className="chat-evidence">
                  <summary onClick={pauseFollowing}>{t("Evidence")}</summary>
                  <EvidenceFeedback outputId={message.evidence ? message.id : undefined} evidence={message.evidence} outputText={message.content} />
                </details> : null}
              </article>
            ))
          ) : !sending && !conversation.error ? (
            <EmptyState icon="chat" title={scope.kind === "recording" ? t("Chat with this recording") : scope.kind === "day" ? t("Ask about this day") : scope.kind === "range" ? t("Ask about this date range") : scope.kind === "meeting" ? t("Ask about this meeting") : t("A little help remembering")}
              action={<div className="chat-starters" aria-label={t("Suggested questions")}>
                {[t("What were the key decisions?"), t("What should I follow up on?"), t("Summarize the main themes.")].map((prompt) => (
                  <button type="button" className="chat-starter" key={prompt} onClick={() => {
                    setQuestion(prompt);
                    composer.current?.focus();
                  }}>{prompt}<Icon name="chevron-right" size={16} /></button>
                ))}
              </div>}>
              {scope.kind === "recording" ? t("This recording only") : scope.kind === "day" ? t("Revisit this date’s diary and recordings.")
                : scope.kind === "range" ? t("Explore diary entries and recordings between the selected dates.")
                : scope.kind === "meeting" ? t("Explore the recordings around this meeting.")
                  : t("Find a detail or connect the threads across all your diary entries and recordings.")}
            </EmptyState>
          ) : null}
          {sending ? (
            <>
            <article className="chat-message user pending">
              <MessageLabel user timezone={timezone} />
              <p className="chat-question">{sending}</p>
            </article>
            <article className="chat-message assistant pending">
              <MessageLabel timezone={timezone} />
              {streamedText ? <ChatMarkdown text={streamedText} draft /> : null}
              <Loading>{stage === "expanding" ? t("Trying alternate search wording…") : stage === "generating" ? t("Writing your answer…") : t("Looking through your recordings…")}</Loading>
              {streamedText ? <p className="caption">{t("Draft answer · sources are checked before saving.")}</p> : null}
            </article>
            </>
          ) : null}
          {conversation.error ? (
            <ErrorState
              message={conversation.error}
              retry={conversation.refresh}
            />
          ) : null}
          {error ? <ErrorState message={error} /> : null}
          </div>
        </div>
          {awayFromLatest ? <button type="button" className="button small chat-jump" onClick={() => {
            jumpToLatest();
            messageViewport.current?.focus({ preventScroll: true });
          }}>
            <Icon name="chevron-right" size={15} />{t("Jump to latest")}
          </button> : null}
        </div>
        <form className="chat-form" onSubmit={send}>
          <label className="sr-only" htmlFor="chat-question">
            {t("Ask your memory")}</label>
          <textarea
            ref={composer}
            id="chat-question"
            placeholder={t("What would you like to remember?")}
            rows={1}
            value={question}
            maxLength={2000}
            aria-describedby="chat-composer-hint"
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            readOnly={Boolean(sending)}
          />
          {sending ? <button key="stop" type="button" className="button" onClick={() => {
            activeRequest.current?.abort();
            composer.current?.focus({ preventScroll: true });
          }}>{t("Stop")}</button> : <button key="send" type="submit"
            className="button dark"
            disabled={
              !question.trim() || Boolean(sending) || conversation.loading
              || Boolean(sessionId && !conversation.data)
            }
            aria-label={t("Send question")}
          >
            <Icon name="send" size={18} />
            <span>{t("Send")}</span>
          </button>}
          <p className="caption" id="chat-composer-hint">
            {t("Enter to send · Shift + Enter for a new line. Answers use your selected context; open the sources to check the details.")}{" "}</p>
        </form>
      </section>
    </div>
  );
}
