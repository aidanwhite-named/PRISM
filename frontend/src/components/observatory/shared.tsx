import type { SourceLink } from "./science";
export function Sources({ links }: { links: SourceLink[] }) {
  return <details className="cosmos-sources"><summary>이야기의 근거 · 더 읽기</summary><ul>{links.map(link => <li key={link.url}><a href={link.url} target="_blank" rel="noopener noreferrer">{link.title} ↗</a></li>)}</ul></details>;
}
export type Room = "prism" | "elements" | "clock" | "stars" | "scale" | "flow" | "ccc";
export type Navigate = (room: Room) => void;
