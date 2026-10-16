import type { CSSProperties } from "react";

export type IconName =
  | "people"
  | "home"
  | "rewind"
  | "skip-back"
  | "diary"
  | "chat"
  | "settings"
  | "star"
  | "mic"
  | "upload"
  | "chevron-left"
  | "chevron-right"
  | "clock"
  | "phone"
  | "close"
  | "send"
  | "download"
  | "search"
  | "check"
  | "plus"
  | "pause"
  | "play"
  | "refresh";
const paths: Record<IconName, string> = {
  people: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87M15 3.13a4 4 0 0 1 0 7.75M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
  home: "m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9",
  rewind: "M3 9V4m0 5h5M3 9a9 9 0 1 1 1 9",
  "skip-back": "m11 5-9 7 9 7V5Zm10 0-9 7 9 7V5Z",
  diary:
    "M14 2H5a1 1 0 0 0-1 1v18a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8l-6-6Zm0 0v6h6M8 12h8M8 16h6",
  chat: "M21 11.5a8.5 8.5 0 0 1-12.6 7.4L3 21l1.9-5.4A8.5 8.5 0 1 1 21 11.5Z",
  settings:
    "m10 3 1-2h2l1 2 3 1 2-1 2 3-1 2v4l1 2-2 3-2-1-3 1-1 2h-2l-1-2-3-1-2 1-2-3 1-2V8L3 6l2-3 2 1 3-1ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  star: "m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z",
  mic: "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5Zm-3 5v2a6 6 0 0 0 12 0v-2M12 18v4m-4 0h8",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6",
  "chevron-left": "m14 5-7 7 7 7",
  "chevron-right": "m10 5 7 7-7 7",
  clock: "M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  phone: "M7 2h10v20H7V2Zm4 17h2",
  close: "m6 6 12 12M6 18 18 6",
  send: "m21 3-7 18-4-8-8-4L21 3Zm0 0L10 13",
  download: "M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4",
  search: "m16 16 5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
  check: "m5 12 4 4L19 6",
  plus: "M12 4v16M4 12h16",
  pause: "M8 4v16M16 4v16",
  play: "m7 3 14 9-14 9V3Z",
  refresh:
    "M21 8V3m0 5h-5M3 16v5m0-5h5M3 8a9 9 0 0 1 15-4l3 4M3 16l3 4a9 9 0 0 0 15-4",
};
export function Icon({
  name,
  size = 20,
  style,
}: {
  name: IconName;
  size?: number;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  );
}
