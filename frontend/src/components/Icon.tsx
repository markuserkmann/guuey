import type { SVGProps } from "react";

const paths = {
  sidebar:
    "M9 3v18M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z",
  plus: "M12 5v14M5 12h14",
  compose:
    "M12 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-7M17 3l4 4-10 10-5 1 1-5L17 3Z",
  pin: "m15 3 6 6-4 1-4 4v4l-7-7h4l4-4 1-4ZM3 21l6-6",
  copy: "M9 9h12v12H9V9ZM15 9V3H3v12h6",
  check: "m5 12 4 4L19 6",
  down: "M12 5v14m-6-6 6 6 6-6",
  close: "m6 6 12 12M6 18 18 6",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
  send: "M12 19V5m-6 6 6-6 6 6",
  attach: "m8 13 7-7a3 3 0 0 1 4 4l-9 9a5 5 0 0 1-7-7l9-9m-6 12 9-9",
  file: "M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8m-8 4h6",
  chevron: "m8 10 4 4 4-4",
  debug: "m4 6 5 5-5 5m9 1h7",
  logout: "M9 4H4v16h5m6-12 4 4-4 4M8 12h11",
} as const;

export function Icon({
  name,
  ...props
}: SVGProps<SVGSVGElement> & { name: keyof typeof paths }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d={paths[name]} />
    </svg>
  );
}
