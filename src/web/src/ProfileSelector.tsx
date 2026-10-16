import { useEffect, useId, useRef, useState } from "react";
import { errorMessage, post } from "./api";
import type { Profile } from "./profiles";
import { Icon } from "./Icon";
import { ErrorState } from "./UI";

export function ProfileSelector({ profiles, selected, onSelect, onCreate, beforeCreate }: {
  profiles: Profile[];
  selected: string;
  onSelect: (id: string) => void;
  onCreate: (profile: Profile) => void;
  beforeCreate?: () => boolean;
}) {
  const [creating, setCreating] = useState(false);
  return <div className="profile-toolbar">
    <label htmlFor="active-profile">Profile</label>
    <select id="active-profile" value={selected} onChange={(event) => onSelect(event.target.value)}>
      {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
    </select>
    <button className="text-button" onClick={() => { if (!beforeCreate || beforeCreate()) setCreating(true); }}>Create profile</button>
    {creating ? <CreateProfile onClose={() => setCreating(false)} onCreated={(profile) => { setCreating(false); onCreate(profile); }} /> : null}
  </div>;
}

function CreateProfile({ onClose, onCreated }: { onClose: () => void; onCreated: (profile: Profile) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const nameId = useId();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement;
    element?.showModal();
    element?.querySelector("input")?.focus();
    return () => { element?.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try { onCreated(await post<Profile>("/v1/profiles", { name: name.trim() })); }
    catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="profile-dialog" aria-labelledby={heading} onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={create} aria-busy={busy}>
      <div className="section-heading"><h2 id={heading}>Create a profile</h2>
        <button type="button" className="icon-button" aria-label="Close create profile" disabled={busy} onClick={onClose}><Icon name="close" /></button>
      </div>
      <p className="muted">Keep recordings, diaries, and chats in their own space. Settings are shared by all profiles. Anyone using this server can select any profile.</p>
      <label htmlFor={nameId}>Profile name</label>
      <input id={nameId} value={name} maxLength={80} required disabled={busy} autoComplete="off" onChange={(event) => setName(event.target.value)} />
      {error ? <ErrorState message={error} /> : null}
      <div className="button-row"><button className="button accent" disabled={busy || !name.trim()}>{busy ? "Creating…" : "Create profile"}</button>
        <button type="button" className="button" disabled={busy} onClick={onClose}>Cancel</button></div>
    </form>
  </dialog>;
}
