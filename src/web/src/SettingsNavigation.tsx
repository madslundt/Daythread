import { Icon, type IconName } from "./Icon";

const categories: { id: SettingsSection; label: string; detail: string; icon: IconName }[] = [
  { id: "general", label: "Daily preferences", detail: "Appearance, recording & recaps", icon: "home" },
  { id: "gestures", label: "Flags & meetings", detail: "Important moments & context", icon: "star" },
  { id: "storage", label: "Storage & privacy", detail: "Audio & transcript retention", icon: "inbox" },
  { id: "connections", label: "Connections", detail: "iPhone, Omi import & forwarding", icon: "phone" },
  { id: "server", label: "Models & server", detail: "Local AI, API keys & advanced", icon: "settings" },
];
export type SettingsSection = "general" | "gestures" | "storage" | "connections" | "server";

export function sectionFromHash(): SettingsSection {
  const value = window.location.hash.split("/")[1];
  return categories.find((category) => category.id === value)?.id || "general";
}

const searchTerms: Record<string, string> = {
  appearance: "daily preferences appearance theme system light dark device display",
  daily: "daily preferences recording capture pause language transcription summary summaries automatic home timezone time zone diary daily recap time hour schedule daytime interval refresh evening silence noise quiet conversation grace wait",
  gestures: "flags meetings gestures important moments context speech before after cooldown time between minimum length maximum duration automatically end auto stop",
  storage: "storage privacy local archive retention keep original audio transcripts days delete remove expire history sign in access",
  connections: "connections omi iphone bluetooth pendant companion cloud import conversations transcripts official app audio forwarding receiver webhook",
};

export function matchesSettings(group: string, query: string): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/);
  return terms.every((term) => searchTerms[group]?.includes(term));
}

export function SettingsNavigation({ section, search, onSearch, onSelect, preferencesDirty, serverDirty }: {
  section: SettingsSection;
  search: string;
  onSearch: (value: string) => void;
  onSelect: (section: SettingsSection) => void;
  preferencesDirty: boolean;
  serverDirty: boolean;
}) {
  return <aside className="settings-navigation">
    <label className="settings-search" htmlFor="settings-search">
      <span className="sr-only">Search settings</span>
      <Icon name="search" size={18} />
      <input id="settings-search" type="search" placeholder="Search settings…" value={search}
        onChange={(event) => onSearch(event.target.value)} />
    </label>
    {search ? <button className="text-button settings-clear-search" type="button" onClick={() => onSearch("")}>Clear search</button> : null}
    <label className="settings-category-picker" htmlFor="settings-category">
      <span id="settings-category-label">Browse settings</span>
      <select id="settings-category" aria-labelledby="settings-category-label" value={search.trim() ? "" : section} onChange={(event) => {
        const category = categories.find((item) => item.id === event.target.value);
        if (category) onSelect(category.id);
      }}>
        {search.trim() ? <option value="" disabled>Search results</option> : null}
        {categories.map((category) => <option key={category.id} value={category.id}>{category.label}</option>)}
      </select>
    </label>
    <nav aria-label="Settings categories">
      {categories.map((category) => <a key={category.id} href={`#settings/${category.id}`}
        aria-current={!search.trim() && section === category.id ? "page" : undefined}
        onClick={(event) => { event.preventDefault(); onSelect(category.id); }}>
        <Icon name={category.icon} size={19} />
        <span><strong>{category.label}</strong><small>{category.detail}</small></span>
        {category.id === "server" && serverDirty ? <span className="settings-dirty-dot" aria-label="Unsaved server changes" /> : null}
      </a>)}
    </nav>
    <p className="settings-navigation-note">Preferences are shared with your iPhone. Appearance stays on this device.</p>
    {preferencesDirty || serverDirty ? <p className="settings-draft-note" role="status">Your unsaved changes stay here as you switch categories.</p> : null}
    {serverDirty && section !== "server" ? <button type="button" className="text-button settings-review-server" onClick={() => onSelect("server")}>Review server changes</button> : null}
  </aside>;
}
