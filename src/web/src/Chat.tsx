import { statusLabel } from "./statusLabels";
import { t } from "./i18n";
import { profileStorageKey } from "./profiles";
import { useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";
import { exchangeMessages, streamChat } from "./chatStream";
import type { ChatResult } from "./chatStream";
import { dateRangeForShortcut, dateRangeShortcuts, dateTimeLabel, dayLabel, localDay } from "./format";
import { Icon } from "./Icon";
import { EvidenceFeedback } from "./CompletenessFeedback";
import { SourceList } from "./SourceList";
import type { ChatMessage, ChatScope, ChatSession, Conversation, Timeline } from "./types";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading, RichText } from "./UI";

const EMPTY_MESSAGES: ChatMessage[] = [];

function savedSession(): string | undefined {
  try {
    return localStorage.getItem(profileStorageKey("omi.chat-session.v1")) || undefined;
  } catch {
    return undefined;
  }
}

function sessionScope(session?: ChatSession): ChatScope {
  if (session?.meeting_id) return { kind: "meeting", meeting_id: session.meeting_id };
  if (session?.day) return { kind: "day", day: session.day };
  if (session?.start_day && session.end_day) {
    return { kind: "range", start_day: session.start_day, end_day: session.end_day };
  }
  return { kind: "all" };
}

export function Chat({ timezone, initialScope, initialQuestion }: { timezone: string; initialScope?: ChatScope; initialQuestion?: string }) {
  const storageKey = useRef(profileStorageKey("omi.chat-session.v1")).current;
  const [sessionId, setSessionId] = useState<string | undefined>(() => initialScope ? undefined : savedSession());
  const [draftScope, setDraftScope] = useState<ChatScope>(initialScope ?? { kind: "all" });
  const sessions = useResource<{ items: ChatSession[] }>("/v1/chat/sessions");
  const timeline = useResource<Timeline>("/v1/timeline", 15_000);
  const conversation = useResource<Conversation>(
    sessionId ? `/v1/chat/sessions/${encodeURIComponent(sessionId)}` : null,
  );
  const [question, setQuestion] = useState(() => (initialQuestion || "").slice(0, 2000));
  const [sending, setSending] = useState("");
  const [error, setError] = useState("");
  const [streamedText, setStreamedText] = useState("");
  const [stage, setStage] = useState("searching");
  const [completed, setCompleted] = useState<{ result: ChatResult; question: string }>();
  const activeRequest = useRef<AbortController | null>(null);
  const pendingText = useRef("");
  const flushTimer = useRef<number | undefined>(undefined);
  const messagesEnd = useRef<HTMLDivElement>(null);
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
    if (initialQuestion) composer.current?.focus();
  }, [initialQuestion]);
  const scope = sessionId
    ? sessionScope(conversation.data ?? sessions.data?.items.find((session) => session.id === sessionId))
    : draftScope;
  const meetings = timeline.data?.meetings ?? [];
  const selectedMeeting = scope.kind === "meeting" ? meetings.find((meeting) => meeting.id === scope.meeting_id) : undefined;
  const today = localDay(timezone);
  const scopeLabel = scope.kind === "day"
    ? dayLabel(scope.day, true)
    : scope.kind === "range"
      ? `${dayLabel(scope.start_day, true)} – ${dayLabel(scope.end_day, true)}`
    : scope.kind === "meeting"
      ? selectedMeeting ? t("Meeting · {value0}", { value0: dateTimeLabel(selectedMeeting.started_at, timezone) }) : t("Selected meeting")
      : t("All diaries (all days)");
  useEffect(() => {
    try {
      if (sessionId) localStorage.setItem(storageKey, sessionId);
      else localStorage.removeItem(storageKey);
    } catch {
      /* Storage may be disabled; sessions still persist on the backend. */
    }
  }, [sessionId, storageKey]);
  useEffect(() => {
    messagesEnd.current?.scrollIntoView({
      block: "nearest",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    });
  }, [visibleMessages.length, sending]);
  async function send(event: React.FormEvent) {
    event.preventDefault();
    const value = question.trim();
    if (!value || activeRequest.current || sending || conversation.loading || (sessionId && !conversation.data)) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    pendingText.current = "";
    setStreamedText("");
    setStage("searching");
    composer.current?.focus();
    setSending(value);
    setError("");
    try {
      const result = await streamChat({
        question: value,
        ...(sessionId ? { session_id: sessionId } : scope.kind === "day"
          ? { day: scope.day }
          : scope.kind === "range" ? { start_day: scope.start_day, end_day: scope.end_day }
          : scope.kind === "meeting" ? { meeting_id: scope.meeting_id } : {}),
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
      sessions.refresh();
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
    if (!id) setDraftScope(scope);
    setSessionId(id);
    setError("");
    setCompleted(undefined);
    setQuestion("");
    if (!id) composer.current?.focus();
  }
  function changeScope(next: ChatScope) {
    setDraftScope(next);
    setSessionId(undefined);
    setError("");
    setCompleted(undefined);
  }
  return (
    <div className="chat-layout">
      <details className="chat-index">
        <summary className="chat-index-heading">
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
                  : session.meeting_id ? t("Meeting") : t("All days")}</small>
              <small>{dateTimeLabel(session.updated_at, timezone)}</small>
            </button>
          ))}
        </nav>
        {!sessions.loading && !sessions.error && !sessions.data?.items.length ? (
          <p className="caption">{t("Your conversations will appear here. They stay on your home-core.")}</p>
        ) : null}
      </details>
      <section className="conversation-panel" aria-label={t("Conversation")}>
        <div className="chat-scope">
          <label>
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
            </select>
          </label>
          {scope.kind === "day" ? (
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
              <fieldset className="chat-range-shortcuts" disabled={Boolean(sending) || conversation.loading}>
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
              </fieldset>
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
        <div className="messages" role="log" aria-label={t("Conversation messages")} aria-live="polite" aria-relevant="additions text">
          {conversation.loading && !visibleMessages.length ? (
            <Loading>{t("Loading conversation…")}</Loading>
          ) : visibleMessages.length ? (
            visibleMessages.map((message) => (
              <article
                className={`chat-message ${message.role}`}
                key={message.id}
              >
                <div className="message-label">
                  {message.role === "user" ? t("You") : t("Daythread")}
                </div>
                {message.stale ? <p className="caption" role="status">{t("Historical answer: its transcript evidence has changed. Ask again for a current answer.")}</p> : null}
                <RichText text={message.content} />
                {message.role === "assistant" ? <EvidenceFeedback outputId={message.evidence ? message.id : undefined} evidence={message.evidence} outputText={message.content} /> : null}
                {message.retrieval_details?.notice ? <p className="caption">{t(message.retrieval_details.notice)}</p> : null}
                {message.sources.length ? (
                  <details className="chat-sources">
                    <summary>
                      {message.sources.length} {t("source")}{" "}{message.sources.length === 1 ? t("recording") : t("recordings")}
                    </summary>
                    <SourceList
                      sources={message.sources}
                      timezone={timezone}
                      showDate
                      onCorrect={conversation.refresh}
                      onDeleted={() => {
                        setDraftScope(scope);
                        setSessionId(undefined);
                        conversation.setData(undefined);
                        sessions.refresh();
                      }}
                    />
                  </details>
                ) : null}
              </article>
            ))
          ) : !sending && !conversation.error ? (
            <EmptyState icon="chat" title={scope.kind === "day" ? t("Ask about this day") : scope.kind === "range" ? t("Ask about this date range") : scope.kind === "meeting" ? t("Ask about this meeting") : t("A little help remembering")}
              action={<div className="chat-starters" aria-label={t("Suggested questions")}>
                {[t("What were the key decisions?"), t("What should I follow up on?"), t("Summarize the main themes.")].map((prompt) => (
                  <button type="button" className="chat-starter" key={prompt} onClick={() => {
                    setQuestion(prompt);
                    composer.current?.focus();
                  }}>{prompt}<Icon name="chevron-right" size={16} /></button>
                ))}
              </div>}>
              {scope.kind === "day" ? t("Revisit this date’s diary and recordings.")
                : scope.kind === "range" ? t("Explore diary entries and recordings between the selected dates.")
                : scope.kind === "meeting" ? t("Explore the recordings around this meeting.")
                  : t("Find a detail or connect the threads across all your diary entries and recordings.")}
            </EmptyState>
          ) : null}
          {sending ? (
            <div>
            <article className="chat-message user pending">
              <div className="message-label">{t("You")}</div>
              <p>{sending}</p>
            </article>
            <article className="chat-message assistant pending" aria-busy="true">
              <div className="message-label">{t("Daythread")}</div>
              {streamedText ? <p style={{ whiteSpace: "pre-wrap" }}>{streamedText}</p> : null}
              <Loading>{stage === "expanding" ? t("Trying alternate search wording…") : stage === "generating" ? t("Writing your answer…") : t("Looking through your recordings…")}</Loading>
              {streamedText ? <p className="caption">{t("Draft answer · sources are checked before saving.")}</p> : null}
            </article>
            </div>
          ) : null}
          {conversation.error ? (
            <ErrorState
              message={conversation.error}
              retry={conversation.refresh}
            />
          ) : null}
          {error ? <ErrorState message={error} /> : null}
          <div ref={messagesEnd} />
        </div>
        <form className="chat-form" onSubmit={send}>
          <label className="sr-only" htmlFor="chat-question">
            {t("Ask your memory")}{" "}</label>
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
          {sending ? <button key="stop" type="button" className="button" onClick={(event) => {
            event.preventDefault();
            activeRequest.current?.abort();
          }}>{t("Stop")}</button> : <button key="send" type="submit"
            className="button dark"
            disabled={
              !question.trim() || Boolean(sending) || conversation.loading
              || Boolean(sessionId && !conversation.data)
            }
            aria-label={sending ? t("Asking your memory") : t("Send question")}
          >
            <Icon name="send" size={18} />
            <span>{sending ? t("Asking…") : t("Send")}</span>
          </button>}
          <p className="caption" id="chat-composer-hint">
            {t("Enter to send · Shift + Enter for a new line. Answers use your selected context; open the sources to check the details.")}{" "}</p>
        </form>
      </section>
    </div>
  );
}
