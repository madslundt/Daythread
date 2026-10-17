import { source, t } from "./i18n";
import type { Source } from "./types";

export type Vendor = {
  id: string;
  label: string;
  capabilities: string[];
  setup_url: string;
  import_instructions: string;
  configured: boolean;
  key_setting?: string;
};

// One display fallback also handles records from adapters added by the server.
// Stored source kinds and metadata stay independent of interface language.
export function sourceVendor(source: Pick<Source, "vendor_id" | "source_kind" | "source_metadata">): string | undefined {
  return source.vendor_id || source.source_metadata?.vendor_id || source.source_kind?.match(/^(.+)_(?:api|export)$/)?.[1];
}

export function vendorLabel(id: string, vendors: Vendor[] = []): string {
  return t(vendors.find((vendor) => vendor.id === id)?.label
    ?? ({ omi: source("Omi"), plaud: source("Plaud"), pocket: source("Pocket"), apple: source("Apple Voice Memos"), sony: source("Sony"), zoom: source("Zoom") }[id])
    ?? id.replace(/(^|[-_])\w/g, (word) => word.replace(/[-_]/, " ").toUpperCase()));
}

export function sourceVendorLabel(source: Pick<Source, "vendor_id" | "vendor_label" | "source_kind" | "source_metadata">): string {
  if (source.vendor_label) return t(source.vendor_label);
  const id = sourceVendor(source);
  return id ? vendorLabel(id) : "";
}

export function isVendorTranscript(source: Pick<Source, "source_kind">): boolean {
  return /_(?:api|export)$/.test(source.source_kind ?? "");
}

export function VendorOptions({ vendors }: { vendors: Vendor[] }) {
  return <>
    <option value="manual">{t("Other / manual upload")}</option>
    {vendors.filter((vendor) => vendor.id !== "manual" && vendor.capabilities.includes("file_upload")).map((vendor) =>
      <option key={vendor.id} value={vendor.id}>{t(vendor.label)}</option>)}
  </>;
}
