import { t } from "./i18n";
import { useId } from "react";
import type { Profile } from "./profiles";

export function ProfileSelector({ profiles, selected, onSelect }: {
  profiles: Profile[];
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const selectorId = useId();
  return <div className="settings-profile-selector">
    <label htmlFor={selectorId}>{t("Active profile")}</label>
    <select id={selectorId} value={selected ?? ""} onChange={(event) => onSelect(event.target.value || null)}>
      <option value="">{t("Choose a profile…")}</option>
      {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
    </select>
  </div>;
}
