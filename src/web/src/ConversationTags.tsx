import { useEffect, useId, useRef, useState } from "react";
import { t } from "./i18n";
import { ApiError, errorMessage, request } from "./api";
import { Icon } from "./Icon";
import { profileScope } from "./profiles";
import { useResource } from "./useResource";
import { ErrorState, Loading } from "./UI";

export type ConversationTag = { id: string; name: string; source_count?: number };
export type TagDirectory = { revision: string; items: ConversationTag[] };
export type TagSelection = { revision: string; membership: string; source_count: number; tags: (ConversationTag & { source_count: number })[] };
type TagAction = { action: "add" | "remove"; tag_id?: string; name?: string };
const noTags: ConversationTag[] = [];

export function TagFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const directory = useResource<TagDirectory>(`/v1/tags?profile_id=${encodeURIComponent(profileScope().id)}`, 15_000);
  useEffect(() => {
    window.addEventListener("omi:tags-changed", directory.refresh);
    return () => window.removeEventListener("omi:tags-changed", directory.refresh);
  }, [directory.refresh]);
  return <div className="zone-filter">
    <label><span>{t("Tag")}</span><select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{t("All tags")}</option>
      {value && !directory.data?.items.some((tag) => tag.id === value) ? <option value={value}>{t("Tag unavailable")}</option> : null}
      {directory.data?.items.map((tag) => <option key={tag.id} value={tag.id}>{tag.name}</option>)}
    </select></label>
    {directory.error ? <ErrorState message={directory.error} retry={directory.refresh} /> : null}
  </div>;
}

export function ConversationTags({ sourceID, groupID, day, tags = noTags, disabled = false, onChanged }: {
  sourceID?: string; groupID?: string; day?: string; tags?: ConversationTag[]; disabled?: boolean; onChanged?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [savedTags, setSavedTags] = useState<ConversationTag[]>();
  const sourceTags = JSON.stringify(tags);
  useEffect(() => setSavedTags(undefined), [sourceTags, sourceID, groupID, day]);
  useEffect(() => setOpen(false), [sourceID, groupID, day]);
  const trigger = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const editorID = useId();
  useEffect(() => {
    if (!open && wasOpen.current) trigger.current?.focus({ preventScroll: true });
    wasOpen.current = open;
  }, [open]);
  function close() { setOpen(false); }
  const currentTags = savedTags ?? tags;
  const scopeID = profileScope().id;
  const params = new URLSearchParams({ profile_id: scopeID, ...(groupID && day ? { day } : {}) });
  const path = groupID ? `/v1/recording-groups/${encodeURIComponent(groupID)}/tags?${params}` : `/v1/sources/${encodeURIComponent(sourceID || "")}/tags?${params}`;
  return <section className="conversation-tags" aria-label={t("Tags")}>
    <div className="recording-metadata-row">
      <strong className="recording-metadata-label"><Icon name="tag" size={17} />{t("Tags")}</strong>
      <div className="recording-tag-chips">{currentTags.length ? currentTags.map((tag) => <span className="recording-tag-chip" key={tag.id}>{tag.name}</span>) : <span className="caption">{t("No tags yet")}</span>}</div>
      <button ref={trigger} type="button" className="text-button recording-metadata-action" disabled={disabled || open} aria-expanded={open} aria-controls={editorID} onClick={() => setOpen(true)}>
        {currentTags.length ? t("Edit tags") : t("Add tag")}
      </button>
    </div>
    {open ? <div id={editorID}><TagEditor key={path} path={path} group={Boolean(groupID)} onClose={close} onChanged={(result) => {
      setSavedTags(result.tags); onChanged?.();
    }} /></div> : null}
  </section>;
}

export function TagEditor({ path, group, onChanged, onClose }: { path: string; group: boolean; onChanged?: (result: TagSelection) => void; onClose?: () => void }) {
  const state = useResource<TagSelection>(path);
  const directory = useResource<TagDirectory>(`/v1/tags?profile_id=${encodeURIComponent(profileScope().id)}`);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [needsReload, setNeedsReload] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const headingID = useId();
  const [scope] = useState(profileScope);
  const [lifetime] = useState(() => new AbortController());
  useEffect(() => () => lifetime.abort(), [lifetime]);
  useEffect(() => { if (!busy) input.current?.focus({ preventScroll: true }); }, [busy]);
  function reload() { state.refresh(); directory.refresh(); setError(""); setNeedsReload(false); }
  async function change(action: TagAction) {
    if (!state.data || busy || state.loading || needsReload) return;
    setBusy(true); setError("");
    try {
      const result = await request<TagSelection>(path, { method: "PUT", signal: lifetime.signal, body: JSON.stringify({
        ...action, expected_revision: state.data.revision, expected_membership: state.data.membership,
      }) }, scope);
      if (lifetime.signal.aborted || scope.signal.aborted) return;
      state.setData(result); setName(""); directory.refresh();
      onChanged?.(result); window.dispatchEvent(new Event("omi:tags-changed"));
    } catch (error) {
      if (!lifetime.signal.aborted && !scope.signal.aborted) {
        setError(errorMessage(error));
        setNeedsReload(error instanceof ApiError && (error.status === 409 || error.status === 404));
      }
    } finally { if (!lifetime.signal.aborted && !scope.signal.aborted) setBusy(false); }
  }
  const data = state.data;
  const query = name.trim().normalize("NFC").replace(/\s+/g, " ");
  const normalized = query.toLocaleLowerCase();
  const available = directory.data?.items.filter((tag) => !data?.tags.some((selected) => selected.id === tag.id && selected.source_count === data.source_count)) ?? [];
  const matches = available.filter((tag) => tag.name.toLocaleLowerCase().includes(normalized));
  const exact = directory.data?.items.some((tag) => tag.name.toLocaleLowerCase() === normalized);
  const disabled = busy || !data || state.loading || Boolean(state.error) || needsReload;
  return <div className="recording-tag-picker" role="region" aria-labelledby={headingID} aria-busy={busy}
    onKeyDown={(event) => { if (event.key === "Escape" && !event.nativeEvent.isComposing && !busy && onClose) { event.stopPropagation(); onClose(); } }}>
    <div className="recording-picker-heading"><h4 id={headingID}>{t("Edit tags")}</h4>
      {onClose ? <button type="button" className="text-button" disabled={busy} onClick={onClose}>{t("Done")}<Icon name="check" size={16} /></button> : null}
    </div>
    {group ? <p className="caption">{t("Changes apply to all currently saved recordings in this conversation, including those hidden by filters.")}</p> : null}
    {state.error ? <ErrorState message={state.error} retry={reload} /> : null}
    {directory.error ? <ErrorState message={directory.error} retry={reload} /> : null}
    {(state.loading && !data) || (directory.loading && !directory.data) ? <Loading /> : null}
    <label className="recording-tag-search"><span className="sr-only">{t("Search or create a tag")}</span><Icon name="search" size={17} />
      <input ref={input} type="search" value={name} maxLength={80} placeholder={t("Search or create a tag")} disabled={busy}
        onChange={(event) => setName(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Enter" && !event.nativeEvent.isComposing) {
            event.preventDefault();
            if (disabled || !directory.data || directory.loading || directory.error || !query) return;
            const match = matches.find((tag) => tag.name.toLocaleLowerCase() === normalized);
            if (match) void change({ action: "add", tag_id: match.id });
            else if (!exact) void change({ action: "add", name: query });
          }
        }} />
    </label>
    <fieldset disabled={disabled} aria-label={t("Conversation tags")}>
      {data?.tags.length ? <div className="recording-selected-tags" aria-label={t("Tags")}>{data.tags.map((tag) => <div className="recording-selected-tag" key={tag.id}>
        <span>{tag.name}{tag.source_count < data.source_count ? ` · ${t("{value0} of {value1} recordings", { value0: tag.source_count, value1: data.source_count })}` : ""}</span>
        {tag.source_count < data.source_count ? <button type="button" className="button small" onClick={() => void change({ action: "add", tag_id: tag.id })}>{t("Apply to all")}</button> : null}
        <button type="button" className="icon-button" aria-label={t("Remove tag {value0}", { value0: tag.name })} onClick={() => void change({ action: "remove", tag_id: tag.id })}><Icon name="close" size={14} /></button>
      </div>)}</div> : null}
      <div className="recording-tag-results" aria-label={t("Existing tag")}>
        {matches.map((tag) => <button type="button" className="recording-tag-option" key={tag.id} disabled={directory.loading || Boolean(directory.error)} onClick={() => void change({ action: "add", tag_id: tag.id })}>
          <Icon name="tag" size={15} /><span>{tag.name}</span><Icon name="plus" size={15} />
        </button>)}
        {query && !exact && directory.data && !directory.error ? <button type="button" className="recording-tag-option recording-tag-create" disabled={directory.loading} onClick={() => void change({ action: "add", name: query })}>
          <Icon name="plus" size={16} /><span>{t("Create tag “{name}”", { name: query })}</span>
        </button> : null}
      </div>
      {directory.data && !available.length && !query ? <p className="caption">{t("Type a name to create a tag.")}</p> : null}
      {query && exact && !matches.length ? <p className="caption" role="status">{t("This tag is already added.")}</p> : null}
    </fieldset>
    <p className="caption recording-tag-status" role="status">{busy ? t("Saving…") : t("Changes save automatically.")}</p>
    {error ? <ErrorState message={error} retry={reload} /> : null}
  </div>;
}
