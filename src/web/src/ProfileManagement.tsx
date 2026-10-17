import { t } from "./i18n";
import { useEffect, useId, useRef, useState } from "react";
import { errorMessage, request } from "./api";
import { Icon } from "./Icon";
import { ProfileSelector } from "./ProfileSelector";
import type { Profile } from "./profiles";
import { ErrorState } from "./UI";

type ProfileAction = { kind: "create" } | { kind: "edit"; profile: Profile };

export function ProfileManagement({ profiles, selected, onSelect, onCreate, onUpdate, error, onRetry }: {
  profiles: Profile[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onCreate: (profile: Profile) => void;
  onUpdate: (profile: Profile) => void;
  error?: string;
  onRetry?: () => void;
}) {
  const [action, setAction] = useState<ProfileAction>();
  const [notice, setNotice] = useState("");
  return <section aria-labelledby="profiles-heading">
    <div className="settings-section-heading">
      <h2 id="profiles-heading">{t("Profiles")}</h2>
      <p>{t("Keep recordings, diaries, and chats in their own space. Settings are shared by all profiles.")}</p>
    </div>
    {!selected ? <p className="notice" role="status">{t("Choose a profile to open your recordings, diary, and other pages. You can browse all settings without a profile.")}</p> : null}
    {error ? <ErrorState message={error} retry={onRetry} /> : null}
    <ProfileSelector profiles={profiles} selected={selected} onSelect={onSelect} />
    <button type="button" className="button accent" onClick={() => setAction({ kind: "create" })}>
      <Icon name="plus" size={18} /> {t("Create profile")}{" "}</button>
    {notice ? <p className="caption" role="status">{notice}</p> : null}
    <ul className="settings-profile-list" aria-label={t("Profiles")}>
      {profiles.map((profile) => <li key={profile.id}>
        <div className="settings-profile-name"><strong>{profile.name}</strong>
          <small>{profile.id === selected ? t("Current profile") : profile.id === "default" ? t("Default archive") : t("Separate archive")}</small>
        </div>
        <div className="button-row">
          <button type="button" className="button" aria-label={`Edit ${profile.name}`} onClick={() => setAction({ kind: "edit", profile })}>{t("Edit")}</button>
        </div>
      </li>)}
    </ul>
    <p className="caption">{t("Anyone using this server can select any profile.")}</p>
    {action ? <ProfileDialog action={action} onClose={() => setAction(undefined)} onSaved={(profile) => {
      setAction(undefined);
      setNotice(action.kind === "create" ? "Profile created." : "Profile updated.");
      if (action.kind === "create") onCreate(profile); else onUpdate(profile);
    }} /> : null}
  </section>;
}

function ProfileDialog({ action, onClose, onSaved }: {
  action: ProfileAction;
  onClose: () => void;
  onSaved: (profile: Profile) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const nameId = useId();
  const [name, setName] = useState(action.kind === "create" ? "" : action.profile.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const title = action.kind === "edit" ? "Edit profile" : "Create a profile";
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement;
    element?.showModal();
    element?.querySelector<HTMLInputElement>("input")?.focus();
    return () => { element?.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const path = action.kind === "create" ? "/v1/profiles" : `/v1/profiles/${encodeURIComponent(action.profile.id)}`;
      onSaved(await request<Profile>(path, { method: action.kind === "create" ? "POST" : "PATCH", body: JSON.stringify({ name: name.trim() }) }));
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="profile-dialog" aria-labelledby={heading}
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={submit} aria-busy={busy}>
      <div className="section-heading"><h2 id={heading}>{title}</h2>
        <button type="button" className="icon-button" aria-label={t("Close profile dialog")} disabled={busy} onClick={onClose}><Icon name="close" /></button>
      </div>
      <p className="muted">{action.kind === "edit" ? t("Change the profile name. Its recordings, diaries, and chats stay together.") : t("Start an empty archive for your recordings, diaries, and chats.")}</p>
      <label htmlFor={nameId}>{t("Profile name")}</label>
      <input id={nameId} value={name} maxLength={80} required disabled={busy} autoComplete="off" onChange={(event) => setName(event.target.value)} />
      {error ? <ErrorState message={error} /> : null}
      <div className="button-row">
        <button type="button" className="button" disabled={busy} onClick={onClose}>{t("Cancel")}</button>
        <button className="button accent" disabled={busy || !name.trim()}>
          {busy ? t("Saving…") : action.kind === "edit" ? t("Save changes") : t("Create profile")}
        </button>
      </div>
    </form>
  </dialog>;
}
