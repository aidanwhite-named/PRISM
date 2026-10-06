/** 이 프로그램이 하는 두 가지 일.
 *
 *  구성대비는 직접 첨부한 문헌 또는 검색에서 선택해 확보한 본문으로 시작한다.
 *  검색과 분석은 연결되지만 자료·도구 정책·결과가 달라 별도 주소를 유지한다.
 *
 *  화면에 쓰는 말도 여기 한 곳에 둔다. 상단 전환기와 각 작업 화면이 서로 다른
 *  이름으로 같은 것을 부르면, 옮겨 다닐 때마다 다른 프로그램처럼 보인다.
 */

import type { JobKind } from "./types";

export type Workspace = {
  id: JobKind;
  path: string;
  /** 상단 전환기와 화면 머리말에 쓰는 이름. */
  label: string;
  /** 이름 밑에 붙는 한 줄. 무엇을 넣고 무엇이 나오는지. */
  note: string;
};

export const WORKSPACES: readonly Workspace[] = [
  {
    id: "similarity_search",
    path: "/search",
    label: "유사문헌 검색",
    note: "특허 · 논문 탐색 → 선택 문헌 구성대비",
  },
  {
    id: "patent_analysis",
    path: "/analysis",
    label: "구성대비 분석",
    note: "청구항 × 인용발명",
  },
] as const;

export const WORKSPACE_BY_ID: Record<JobKind, Workspace> = {
  similarity_search: WORKSPACES[0],
  patent_analysis: WORKSPACES[1],
};

/** 그 작업의 주소. */
export function workspacePath(kind: JobKind): string {
  return WORKSPACE_BY_ID[kind].path;
}
