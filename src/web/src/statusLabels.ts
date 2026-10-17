import { t } from "./i18n";

/** Translate presentation labels without changing the values used by the API. */
export function statusLabel(value: string | null | undefined): string {
  if (!value) return "";
  const key = `status.${value}`;
  const label = t(key);
  return label === key ? value.replaceAll("_", " ") : label;
}
