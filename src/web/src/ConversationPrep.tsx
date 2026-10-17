import { t } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, request } from "./api";
import { dateTimeLabel } from "./format";
import {
  prepIsOld,
  type PrepResult,
  type PrepSearch,
  type PrepSearchResult,
  type PrepSource,
} from "./prep";
import { PrepBrief } from "./PrepBrief";
import { SourceList } from "./SourceList";
import { ErrorState } from "./UI";

export function ConversationPrep({
  timezone,
  onClose,
  onDirtyChange,
}: {
  timezone: string;
  onClose: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [kind, setKind] = useState<PrepSearch["kind"]>("person");
  const [subject, setSubject] = useState("");
  const [aliases, setAliases] = useState("");
  const [qualifier, setQualifier] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [context, setContext] = useState("");
  const [found, setFound] = useState<PrepSearchResult>();
  const [selected, setSelected] = useState<PrepSource[]>([]);
  const [latest, setLatest] = useState("");
  const [result, setResult] = useState<PrepResult>();
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | undefined>(undefined);
  const revision = useRef(0);
  const draft = !!(subject || context || qualifier || aliases || start || end);
  useEffect(() => onDirtyChange(draft), [draft, onDirtyChange]);
  const zone = found?.timezone || timezone;
  function cancel() {
    revision.current++;
    active.current?.abort();
    setBusy(false);
    setChecking(false);
  }
  function invalidate(clearSearch = true) {
    cancel();
    setResult(undefined);
    setError("");
    if (clearSearch) {
      setFound(undefined);
      setSelected([]);
      setLatest("");
    }
  }
  useEffect(
    () => () => {
      revision.current++;
      active.current?.abort();
    },
    [],
  );
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (draft) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [draft]);
  useEffect(() => {
    const check = async () => {
      if (!found) return;
      const current = ++revision.current;
      active.current?.abort();
      setBusy(false);
      setChecking(true);
      try {
        const status = await request<{ valid: boolean }>(
          "/v1/conversation-prep/validate",
          {
            method: "POST",
            body: JSON.stringify({
              search: found.search,
              snapshot: found.snapshot,
            }),
            cache: "no-store",
          },
        );
        if (revision.current !== current) return;
        if (!status.valid) {
          setFound(undefined);
          setSelected([]);
          setLatest("");
          setResult(undefined);
          setError(
            "Evidence changed. Find discussions again; your draft is retained.",
          );
        }
      } catch (failure) {
        if (revision.current !== current) return;
        setResult(undefined);
        setFound(undefined);
        setSelected([]);
        setLatest("");
        setError(
          "Could not verify current evidence. Find discussions again. " +
            errorMessage(failure),
        );
      } finally {
        if (revision.current === current) setChecking(false);
      }
    };
    window.addEventListener("focus", check);
    window.addEventListener("omi:archive-changed", check);
    return () => {
      window.removeEventListener("focus", check);
      window.removeEventListener("omi:archive-changed", check);
    };
  }, [found]);
  async function run(generate: boolean, offset = 0) {
    cancel();
    const current = revision.current;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setResult(undefined);
    const search: PrepSearch = {
      kind,
      subject,
      aliases: aliases
        .split("\n")
        .map((v) => v.trim())
        .filter(Boolean),
      qualifier,
      start_day: start || null,
      end_day: end || null,
      offset,
    };
    try {
      if (generate && found) {
        const value = await request<PrepResult>(
          "/v1/conversation-prep/generate",
          {
            method: "POST",
            signal: controller.signal,
            cache: "no-store",
            body: JSON.stringify({
              search: found.search,
              snapshot: found.snapshot,
              selected: selected.map(({ id, version }) => ({ id, version })),
              latest_source_id: latest,
              user_context: context,
            }),
          },
        );
        if (revision.current === current) setResult(value);
      } else {
        const value = await request<PrepSearchResult>(
          "/v1/conversation-prep/search",
          {
            method: "POST",
            signal: controller.signal,
            cache: "no-store",
            body: JSON.stringify(search),
          },
        );
        if (revision.current === current) {
          if (value.snapshot !== found?.snapshot) {
            setSelected([]);
            setLatest("");
          }
          setFound(value);
        }
      }
    } catch (failure) {
      if (revision.current !== current) return;
      setError(errorMessage(failure));
      if (failure instanceof ApiError && failure.status === 409) {
        setFound(undefined);
        setSelected([]);
        setLatest("");
      }
    } finally {
      if (revision.current === current) setBusy(false);
    }
  }
  function toggle(source: PrepSource, included: boolean) {
    invalidate(false);
    setSelected((rows) =>
      included ? [...rows, source] : rows.filter((row) => row.id !== source.id),
    );
    if (!included && latest === source.id) setLatest("");
  }
  return (
    <section className="prep-screen" aria-label={t("Prepare for a conversation")}>
      <div className="section-heading">
        <h2>{t("Prepare for a conversation")}</h2>
        <button
          className="button"
          onClick={() => {
            if (
              !draft ||
              window.confirm("Discard this preparation and unsaved context?")
            )
              onClose();
          }}
        >
          {t("Close")}{" "}</button>
      </div>
      <p>
        {t("Find dated recordings, confirm their relevance, then prepare a cited brief. Names are literal phrases; a mention does not verify who participated.")}{" "}</p>
      <div className="prep-form">
        <label>
          {t("Subject type")}{" "}<select
            value={kind}
            onChange={(e) => {
              invalidate();
              setKind(e.target.value as PrepSearch["kind"]);
            }}
          >
            <option value="person">{t("Person")}</option>
            <option value="project">{t("Project")}</option>
            <option value="topic">{t("Topic")}</option>
          </select>
        </label>
        <label>
          {t("Name or phrase")}{" "}<input
            maxLength={200}
            value={subject}
            onChange={(e) => {
              invalidate();
              setSubject(e.target.value);
            }}
          />
        </label>
        <label>
          {t("Alternate names · one per line")}{" "}<textarea
            rows={2}
            value={aliases}
            maxLength={2010}
            onChange={(e) => {
              invalidate();
              setAliases(e.target.value);
            }}
          />
        </label>
        <label>
          {t("Qualifier · must also appear")}{" "}<input
            maxLength={200}
            value={qualifier}
            onChange={(e) => {
              invalidate();
              setQualifier(e.target.value);
            }}
          />
        </label>
        <div className="button-row">
          <label>
            {t("From")}{" "}<input
              type="date"
              value={start}
              onChange={(e) => {
                invalidate();
                setStart(e.target.value);
              }}
            />
          </label>
          <label>
            {t("Through")}{" "}<input
              type="date"
              value={end}
              onChange={(e) => {
                invalidate();
                setEnd(e.target.value);
              }}
            />
          </label>
        </div>
        <p className="caption">
          {start || end ? `Inclusive dates in ${zone}` : t("All retained dates")}{t(". Literal matches can miss unnamed discussions. Add an alternate phrase or browse Rewind if no match appears.")}{" "}</p>
        <label>
          {t("My context and goals · user-authored, not recorded evidence")}{" "}<textarea
            rows={3}
            value={context}
            maxLength={4000}
            onChange={(e) => {
              invalidate(false);
              setContext(e.target.value);
            }}
          />
        </label>
        <p className="caption">
          {t("This draft stays in this screen only. It is sent to your local model separately from recorded evidence.")}{" "}</p>
        <button
          className="button dark"
          disabled={busy || !subject.trim() || !!start !== !!end}
          onClick={() => void run(false)}
        >
          {t("Find discussions")}{" "}</button>
        {busy ? (
          <button className="button" onClick={cancel}>
            {t("Cancel request")}{" "}</button>
        ) : null}
      </div>
      {error ? <ErrorState message={error} /> : null}
      {checking ? <p role="status">{t("Checking current evidence…")}</p> : null}
      {found && !checking ? (
        <>
          <h3>{t("Matching recordings ·")}{" "}{found.coverage.total_matches}</h3>
          <p className="caption">
            {found.coverage.pending_chunks} {t("pending chunks ·")}{" "}
            {found.coverage.capture_gaps} {t("known capture gaps in searched dates. Phone uploads not yet received are unknown. Page omits")}{" "}
            {found.coverage.omitted_matches} {t("matches.")}{" "}</p>
          {!found.sources.length ? (
            <p>
              {t("No matching transcribed recordings. Try alternate names or different dates.")}{" "}</p>
          ) : null}
          {found.sources.map((source) => (
            <div className="prep-candidate" key={source.id}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.some((row) => row.id === source.id)}
                  disabled={
                    busy ||
                    (selected.length >= 40 &&
                      !selected.some((row) => row.id === source.id))
                  }
                  onChange={(e) => toggle(source, e.target.checked)}
                />{" "}
                {t("Include")}{" "}{dateTimeLabel(source.started_at, zone)}
                {prepIsOld(source.started_at) ? t("· over 30 days old") : ""}
              </label>
              <p>{source.text?.slice(0, 500)}</p>
              <SourceList sources={[source]} timezone={zone} showDate />
            </div>
          ))}
          <div className="button-row">
            <button
              className="button"
              disabled={busy || found.search.offset === 0}
              onClick={() =>
                void run(false, Math.max(0, found.search.offset - 10))
              }
            >
              {t("Previous")}{" "}</button>
            <button
              className="button"
              disabled={
                busy || found.search.offset + 10 >= found.coverage.total_matches
              }
              onClick={() => void run(false, found.search.offset + 10)}
            >
              {t("Next")}{" "}</button>
          </div>
          <label>
            {t("Confirm latest relevant recording")}{" "}<select
              value={latest}
              onChange={(e) => {
                invalidate(false);
                setLatest(e.target.value);
              }}
            >
              <option value="">{t("Choose after checking the evidence")}</option>
              {selected.map((source) => (
                <option key={source.id} value={source.id}>
                  {dateTimeLabel(source.started_at, zone)} ·{" "}
                  {source.text?.slice(0, 70)}
                </option>
              ))}
            </select>
          </label>
          <p className="caption">
            {selected.length} {t("selected. Excerpts are bounded to 4,000 characters each and 16,000 total. Narrow selection if the budget is exceeded.")}{" "}</p>
          <button
            className="button dark"
            disabled={busy || !latest || !selected.length}
            onClick={() => void run(true)}
          >
            {busy ? t("Working…") : t("Prepare from confirmed evidence")}
          </button>
        </>
      ) : null}
      {result && !checking ? (
        <PrepBrief key={result.generated_at} result={result} timezone={zone} />
      ) : null}
    </section>
  );
}
