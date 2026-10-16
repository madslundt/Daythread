import { useEffect, useRef, useState } from "react";
import { errorMessage, post } from "./api";
import { dateRangeForShortcut, dateRangeShortcuts, dateTimeLabel, dayLabel, localDay } from "./format";
import { Icon } from "./Icon";
import { SourceList } from "./SourceList";
import type { ChatScope, ChatSession, Conversation, Source, Timeline } from "./types";
import { useResource } from "./useResource";
import { EmptyState, ErrorState, Loading, RichText } from "./UI";

function savedSession(): string | undefined {
  try {
    return localStorage.getItem("omi.chat-session.v1") || undefined;
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

export function Chat({ timezone, initialScope }: { timezone: string; initialScope?: ChatScope }) {
  const [sessionId, setSessionId] = useState<string | undefined>(() => initialScope ? undefined : savedSession());
  const [draftScope, setDraftScope] = useState<ChatScope>(initialScope ?? { kind: "all" });
  const sessions = useResource<{ items: ChatSession[] }>("/v1/chat/sessions");
  const timeline = useResource<Timeline>("/v1/timeline", 15_000);
  const conversation = useResource<Conversation>(
    sessionId ? `/v1/chat/sessions/${encodeURIComponent(sessionId)}` : null,
  );
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState("");
  const [error, setError] = useState("");
  const messagesEnd = useRef<HTMLDivElement>(null);
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
      ? selectedMeeting ? `Meeting · ${dateTimeLabel(selectedMeeting.started_at, timezone)}` : "Selected meeting"
      : "All diaries (all days)";
  useEffect(() => {
    try {
      if (sessionId) localStorage.setItem("omi.chat-session.v1", sessionId);
      else localStorage.removeItem("omi.chat-session.v1");
    } catch {
      /* Storage may be disabled; sessions still persist on the backend. */
    }
  }, [sessionId]);
  useEffect(() => {
    messagesEnd.current?.scrollIntoView({
      block: "nearest",
      behavior: "smooth",
    });
  }, [conversation.data?.messages.length, sending]);
  async function send(event: React.FormEvent) {
    event.preventDefault();
    const value = question.trim();
    if (!value || sending || conversation.loading || (sessionId && !conversation.data)) return;
    setSending(value);
    setError("");
    try {
      const result = await post<{
        answer: string;
        sources: Source[];
        session_id: string;
      }>("/v1/chat", {
        question: value,
        ...(sessionId ? { session_id: sessionId } : scope.kind === "day"
          ? { day: scope.day }
          : scope.kind === "range" ? { start_day: scope.start_day, end_day: scope.end_day }
          : scope.kind === "meeting" ? { meeting_id: scope.meeting_id } : {}),
      });
      setQuestion("");
      setSessionId(result.session_id);
      conversation.refresh();
      sessions.refresh();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setSending("");
    }
  }
  function select(id?: string) {
    if (!id) setDraftScope(scope);
    setSessionId(id);
    setError("");
    setQuestion("");
  }
  function changeScope(next: ChatScope) {
    setDraftScope(next);
    setSessionId(undefined);
    setError("");
    setQuestion("");
  }
  return (
    <div className="chat-layout">
      <aside className="chat-index">
        <button
          className="button"
          onClick={() => select()}
          disabled={Boolean(sending)}
        >
          <Icon name="plus" size={17} />
          New conversation
        </button>
        {sessions.error ? (
          <ErrorState message={sessions.error} retry={sessions.refresh} />
        ) : null}
        <nav aria-label="Saved conversations">
          {sessions.data?.items.map((session) => (
            <button
              className={`conversation-link ${sessionId === session.id ? "selected" : ""}`}
              key={session.id}
              onClick={() => select(session.id)}
              disabled={Boolean(sending)}
            >
              <span>{session.title}</span>
              <small>{session.day ? dayLabel(session.day, true)
                : session.start_day && session.end_day ? `${dayLabel(session.start_day, true)} – ${dayLabel(session.end_day, true)}`
                  : session.meeting_id ? "Meeting" : "All days"}</small>
              <small>{dateTimeLabel(session.updated_at, timezone)}</small>
            </button>
          ))}
        </nav>
        {!sessions.loading && !sessions.data?.items.length ? (
          <p className="caption">Your conversations stay on home-core.</p>
        ) : null}
      </aside>
      <section className="conversation-panel" aria-label="Conversation">
        <div className="chat-scope">
          <label>
            <span>Chat with</span>
            <select
              aria-label="Chat scope"
              value={scope.kind}
              disabled={Boolean(sending) || conversation.loading}
              onChange={(event) => {
                if (event.target.value === "day") changeScope({ kind: "day", day: localDay(timezone) });
                else if (event.target.value === "range") changeScope({ kind: "range", ...dateRangeForShortcut("this_week", today) });
                else if (event.target.value === "meeting" && meetings[0]) changeScope({ kind: "meeting", meeting_id: meetings[0].id });
                else changeScope({ kind: "all" });
              }}
            >
              <option value="all">All diaries (all days)</option>
              <option value="day">Specific date</option>
              <option value="range">Date range</option>
              <option value="meeting" disabled={!meetings.length && scope.kind !== "meeting"}>Meeting</option>
            </select>
          </label>
          {scope.kind === "day" ? (
            <label>
              <span>Date</span>
              <input type="date" aria-label="Chat date" value={scope.day}
                disabled={Boolean(sending) || conversation.loading}
                onChange={(event) => { if (event.target.value) changeScope({ kind: "day", day: event.target.value }); }} />
            </label>
          ) : scope.kind === "range" ? (
            <>
              <label>
                <span>From</span>
                <input type="date" aria-label="Chat start date" value={scope.start_day}
                  disabled={Boolean(sending) || conversation.loading}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value) changeScope({ kind: "range", start_day: value, end_day: value > scope.end_day ? value : scope.end_day });
                  }} />
              </label>
              <label>
                <span>Through</span>
                <input type="date" aria-label="Chat end date" value={scope.end_day}
                  disabled={Boolean(sending) || conversation.loading}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value) changeScope({ kind: "range", start_day: value < scope.start_day ? value : scope.start_day, end_day: value });
                  }} />
              </label>
              <fieldset className="chat-range-shortcuts" disabled={Boolean(sending) || conversation.loading}>
                <legend>Date shortcuts</legend>
                {dateRangeShortcuts.map((shortcut) => {
                  const range = dateRangeForShortcut(shortcut.id, today);
                  const selected = range.start_day === scope.start_day && range.end_day === scope.end_day;
                  return <button
                    type="button" className={`button small${selected ? " dark" : ""}`} key={shortcut.id}
                    aria-pressed={selected}
                    onClick={() => changeScope({ kind: "range", ...range })}
                  >{shortcut.label}</button>;
                })}
              </fieldset>
              <p className="caption">Both dates included · {timezone}. Weeks run Monday–Sunday; shortcuts select full calendar periods.</p>
            </>
          ) : scope.kind === "meeting" ? (
            <label className="chat-meeting-picker">
              <span>Meeting</span>
              <select aria-label="Chat meeting" value={scope.meeting_id}
                disabled={Boolean(sending) || conversation.loading}
                onChange={(event) => changeScope({ kind: "meeting", meeting_id: event.target.value })}>
                {!selectedMeeting ? <option value={scope.meeting_id}>Selected meeting</option> : null}
                {meetings.map((meeting) => <option key={meeting.id} value={meeting.id}>
                  {dateTimeLabel(meeting.started_at, timezone)}{meeting.ended_at ? "" : " · in progress"}
                </option>)}
              </select>
            </label>
          ) : null}
          <p className="caption">{scopeLabel}. Changing context starts a new conversation.</p>
          {timeline.error && scope.kind === "meeting" ? <ErrorState message={timeline.error} retry={timeline.refresh} /> : null}
          {!timeline.loading && !timeline.error && !meetings.length ? <p className="caption">Meetings appear here after a meeting has been recorded.</p> : null}
        </div>
        <div className="messages" aria-live="polite">
          {conversation.loading && !conversation.data ? (
            <Loading>Loading conversation…</Loading>
          ) : conversation.data?.messages.length ? (
            conversation.data.messages.map((message) => (
              <article
                className={`chat-message ${message.role}`}
                key={message.id}
              >
                <div className="message-label">
                  {message.role === "user" ? "You" : "omi"}
                </div>
                <RichText text={message.content} />
                {message.sources.length ? (
                  <details className="chat-sources">
                    <summary>
                      {message.sources.length} source recording(s)
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
          ) : (
            <EmptyState icon="chat" title={scope.kind === "day" ? "Ask about this day" : scope.kind === "range" ? "Ask about this date range" : scope.kind === "meeting" ? "Ask about this meeting" : "Ask across your days"}>
              {scope.kind === "day" ? "Revisit this date’s diary and recordings."
                : scope.kind === "range" ? "Explore diary entries and recordings between the selected dates."
                : scope.kind === "meeting" ? "Explore the recordings around this meeting."
                  : "Find a detail or connect the threads across all your diary entries and recordings."}
            </EmptyState>
          )}
          {sending ? (
            <article className="chat-message user pending">
              <div className="message-label">You</div>
              <p>{sending}</p>
              <Loading>Looking through your recordings…</Loading>
            </article>
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
            Ask your memory
          </label>
          <textarea
            id="chat-question"
            placeholder="What would you like to remember?"
            rows={2}
            value={question}
            maxLength={2000}
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
            disabled={Boolean(sending)}
          />
          <button
            className="button dark"
            disabled={
              !question.trim() || Boolean(sending) || conversation.loading
              || Boolean(sessionId && !conversation.data)
            }
            aria-label="Send question"
          >
            <Icon name="send" size={18} />
            <span>Send</span>
          </button>
          <p className="caption">
            Answers use the selected context. Open source recordings to
            check the context.
          </p>
        </form>
      </section>
    </div>
  );
}
