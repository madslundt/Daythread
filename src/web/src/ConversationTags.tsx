import { useEffect, useState } from "react";
import { t } from "./i18n";
import { errorMessage, request } from "./api";
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
    {value ? <p className="caption">{t("Tag and location filters apply together to recordings. The day recap still covers the whole day.")}</p> : null}
  </div>;
}

export function ConversationTags({ sourceID, groupID, day, tags = noTags, onChanged }: {
  sourceID?: string; groupID?: string; day?: string; tags?: ConversationTag[]; onChanged?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [savedTags, setSavedTags] = useState<ConversationTag[]>();
  useEffect(() => setSavedTags(undefined), [tags]);
  const currentTags = savedTags ?? tags;
  const scopeID = profileScope().id;
  const params = new URLSearchParams({ profile_id: scopeID, ...(groupID && day ? { day } : {}) });
  const path = groupID ? `/v1/recording-groups/${encodeURIComponent(groupID)}/tags?${params}` : `/v1/sources/${encodeURIComponent(sourceID || "")}/tags?${params}`;
  return <section className="conversation-tags" aria-label={t("Tags")}>
    <div className="button-row">
      <strong>{t("Tags")}</strong>
      <span>{currentTags.map((tag) => tag.name).join(", ") || t("No tags yet")}</span>
      <button type="button" className="button small" aria-expanded={open} onClick={() => setOpen(!open)}>
        {currentTags.length ? t("Edit tags") : t("Add tag")}
      </button>
    </div>
    {open ? <TagEditor key={path} path={path} group={Boolean(groupID)} onChanged={(result) => {
      setSavedTags(result.tags); onChanged?.();
    }} /> : null}
  </section>;
}

export function TagEditor({ path, group, onChanged }: { path: string; group: boolean; onChanged?: (result: TagSelection) => void }) {
  const state = useResource<TagSelection>(path);
  const directory = useResource<TagDirectory>(`/v1/tags?profile_id=${encodeURIComponent(profileScope().id)}`);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [scope] = useState(profileScope);
  const [lifetime] = useState(() => new AbortController());
  useEffect(() => () => lifetime.abort(), [lifetime]);
  function reload() { state.refresh(); directory.refresh(); }
  async function change(action: TagAction) {
    if (!state.data || busy) return;
    setBusy(true); setError("");
    try {
      const result = await request<TagSelection>(path, { method: "PUT", signal: lifetime.signal, body: JSON.stringify({
        ...action, expected_revision: state.data.revision, expected_membership: state.data.membership,
      }) }, scope);
      if (lifetime.signal.aborted || scope.signal.aborted) return;
      state.setData(result); setName(""); setSelected(""); directory.refresh();
      onChanged?.(result); window.dispatchEvent(new Event("omi:tags-changed"));
    } catch (error) {
      if (!lifetime.signal.aborted && !scope.signal.aborted) setError(errorMessage(error));
    } finally { if (!lifetime.signal.aborted && !scope.signal.aborted) setBusy(false); }
  }
  const data = state.data;
  return <div>
    {group ? <p className="caption">{t("Changes apply to all currently saved recordings in this conversation, including those hidden by filters.")}</p> : null}
    <p className="caption">{t("Tags are your own categories. Adding or removing a tag keeps the recorded location and transcript intact.")}</p>
    {data ? <p className="caption">{t("{value0} recordings selected", { value0: data.source_count })}</p> : null}
    {state.error ? <ErrorState message={state.error} retry={reload} /> : null}
    {directory.error ? <ErrorState message={directory.error} retry={reload} /> : null}
    {state.loading && !data ? <Loading /> : null}
    <fieldset disabled={busy || !data || state.loading}>
      <legend>{t("Conversation tags")}</legend>
      {data?.tags.map((tag) => <div className="button-row" key={tag.id}>
        <span>{tag.name}{tag.source_count < data.source_count ? ` · ${t("{value0} of {value1} recordings", { value0: tag.source_count, value1: data.source_count })}` : ""}</span>
        {tag.source_count < data.source_count ? <button type="button" className="button small" onClick={() => void change({ action: "add", tag_id: tag.id })}>{t("Apply to all")}</button> : null}
        <button type="button" className="text-button" aria-label={t("Remove tag {value0}", { value0: tag.name })} onClick={() => void change({ action: "remove", tag_id: tag.id })}>{t("Remove tag")}</button>
      </div>)}
      {data && !data.tags.length ? <p className="caption">{t("No tags yet")}</p> : null}
      <div className="button-row">
        <label>{t("Existing tag")}<select value={selected} onChange={(event) => setSelected(event.target.value)}>
          <option value="">{t("Choose a tag")}</option>
          {directory.data?.items.map((tag) => <option key={tag.id} value={tag.id}>{tag.name}</option>)}
        </select></label>
        <button type="button" className="button small" disabled={!selected} onClick={() => void change({ action: "add", tag_id: selected })}>{t("Add tag")}</button>
      </div>
      <div className="button-row">
        <label>{t("New tag name")}<input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} /></label>
        <button type="button" className="button small" disabled={!name.trim()} onClick={() => void change({ action: "add", name: name.trim() })}>{t("Create and add tag")}</button>
      </div>
    </fieldset>
    {error ? <ErrorState message={error} retry={reload} /> : null}
  </div>;
}
