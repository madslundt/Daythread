import { t, source } from "./i18n";
import { Icon, type IconName } from "./Icon";

const categories: { id: SettingsSection; label: string; detail: string; icon: IconName }[] = [
  { id: "account", label: source("Daythread account"), detail: source("Optional hosting, credits & AI providers"), icon: "people" },
  { id: "locations", label: source("Location zones"), detail: source("Home, work, gym & other places"), icon: "home" },
  { id: "profiles", label: source("Profiles"), detail: source("Choose, create & edit profiles"), icon: "people" },
  { id: "general", label: source("Daily preferences"), detail: source("Appearance, recording & recaps"), icon: "home" },
  { id: "gestures", label: source("Flags & meetings"), detail: source("Important moments & context"), icon: "star" },
  { id: "storage", label: source("Storage & privacy"), detail: source("Audio & transcript retention"), icon: "inbox" },
  { id: "connections", label: source("Connections"), detail: source("iPhone, devices & imports"), icon: "phone" },
  { id: "server", label: source("Models & server"), detail: source("STT & LLM endpoints, keys & models"), icon: "settings" },
];
export type SettingsSection = "account" | "profiles" | "general" | "gestures" | "storage" | "connections" | "server" | "locations";

export function sectionFromPath(managed = false): SettingsSection {
  const value = window.location.pathname.split("/")[2];
  if (managed && value === "server") return "account";
  return categories.find((category) => category.id === value)?.id || "general";
}

const searchTerms: Record<string, string> = {
  account: source("account sign in managed hosting subscription credits billing ai providers"),
  locations: source("location zones home work gym places coordinates radius gps"),
  profiles: source("profiles profile archive active select selection choose switch name rename edit create new manage"),
  appearance: source("daily preferences appearance theme system light dark device display"),
  daily: source("daily preferences recording capture pause language translation translate whitelist allowed transcription summary summaries automatic home timezone time zone diary daily recap time hour schedule daytime interval refresh evening silence noise quiet conversation grace wait"),
  gestures: source("flags meetings gestures important moments context speech before after cooldown time between minimum length maximum duration automatically end auto stop"),
  storage: source("storage privacy local archive retention keep original audio transcripts days delete remove expire history sign in access"),
  connections: source("connections omi plaud pocket apple sony zoom devices brands iphone bluetooth pendant companion cloud import conversations transcripts official app audio forwarding receiver webhook"),
};

export function matchesSettings(group: string, query: string): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/);
  return terms.every((term) => `${searchTerms[group] || ""} ${t(searchTerms[group] || "")}`.includes(term));
}

export function SettingsNavigation({ section, search, onSearch, onSelect, preferencesDirty, serverDirty, managed = false }: {
  section: SettingsSection;
  search: string;
  onSearch: (value: string) => void;
  onSelect: (section: SettingsSection) => void;
  preferencesDirty: boolean;
  serverDirty: boolean;
  managed?: boolean;
}) {
  const visibleCategories = categories.filter((category) => !managed || category.id !== "server").map((category) =>
    managed && category.id === "connections" ? { ...category, detail: source("iPhone & recording sync") } : category);
  return <aside className="settings-navigation">
    <label className="settings-search" htmlFor="settings-search">
      <span className="sr-only">{t("Search settings")}</span>
      <Icon name="search" size={18} />
      <input id="settings-search" type="search" placeholder={t("Search settings…")} value={search}
        onChange={(event) => onSearch(event.target.value)} />
    </label>
    {search ? <button className="text-button settings-clear-search" type="button" onClick={() => onSearch("")}>{t("Clear search")}</button> : null}
    <label className="settings-category-picker" htmlFor="settings-category">
      <span id="settings-category-label">{t("Browse settings")}</span>
      <select id="settings-category" aria-labelledby="settings-category-label" value={search.trim() ? "" : section} onChange={(event) => {
        const category = visibleCategories.find((item) => item.id === event.target.value);
        if (category) onSelect(category.id);
      }}>
        {search.trim() ? <option value="" disabled>{t("Search results")}</option> : null}
        {visibleCategories.map((category) => <option key={category.id} value={category.id}>{t(category.label)}</option>)}
      </select>
    </label>
    <nav aria-label={t("Settings categories")}>
      {visibleCategories.map((category) => <a key={category.id} href={`/settings/${category.id}`}
        aria-current={!search.trim() && section === category.id ? "page" : undefined}
        onClick={(event) => { event.preventDefault(); onSelect(category.id); }}>
        <Icon name={category.icon} size={19} />
        <span><strong>{t(category.label)}</strong><small>{t(category.detail)}</small></span>
        {category.id === "server" && serverDirty ? <span className="settings-dirty-dot" aria-label={t("Unsaved server changes")} /> : null}
      </a>)}
    </nav>
    <p className="settings-navigation-note">{t("Preferences are shared with your iPhone. Appearance stays on this device.")}</p>
    {preferencesDirty || serverDirty ? <p className="settings-draft-note" role="status">{t("Your unsaved changes stay here as you switch categories.")}</p> : null}
    {serverDirty && section !== "server" ? <button type="button" className="text-button settings-review-server" onClick={() => onSelect("server")}>{t("Review server changes")}</button> : null}
  </aside>;
}
