import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { Job, SearchComparison } from "../lib/types";
import SearchComparisonPanel from "./SearchComparisonPanel";
import { MemoryRouter, useLocation } from "react-router-dom";
import ProgressiveSearchResults from "./ProgressiveSearchResults";
import type { ProgressiveSearchSnapshot } from "../lib/types";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function Location() {
  const location = useLocation();
  return <span aria-label="현재 화면">{location.pathname + location.search}</span>;
}

it("shows the same six candidates as results and permits selecting the sixth by patent number", async () => {
  vi.spyOn(api, "searchComparisons").mockResolvedValue([]);
  const engine = { mode: "autonomous", phase: "complete", elapsed_seconds: 1, warnings: [], queries: [],
    candidates: Array.from({ length: 6 }, (_, i) => ({ id: String(i), document_number: `US${i + 1}A1`,
      title: `후보 ${i + 1}`, url: `https://example.org/${i}`, date_status: "no_date_limit" })) } as unknown as ProgressiveSearchSnapshot;
  const data = { ...job, search_manifest: { engine } } as unknown as Job;
  render(<MemoryRouter><SearchComparisonPanel job={data} /><ProgressiveSearchResults data={engine} /></MemoryRouter>);
  expect(screen.getAllByRole("checkbox")).toHaveLength(6);
  expect(screen.getAllByRole("link", { name: "문헌 보기" })).toHaveLength(6);
  for (let i = 1; i <= 5; i++) fireEvent.click(screen.getByLabelText(`US${i}A1 · 후보 ${i}`));
  const sixth = screen.getByLabelText("US6A1 · 후보 6") as HTMLInputElement;
  expect(sixth.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("US1A1 · 후보 1"));
  expect(sixth.disabled).toBe(false);
  fireEvent.click(sixth);
  expect(sixth.checked).toBe(true);
  expect(screen.getByText("구성대비할 문헌 · 전체 6건 · 선택 5/5건")).toBeTruthy();
});
const job = { id: "search", job_kind: "similarity_search", status: "SUCCEEDED", search_manifest: {
  engine: { candidates: [{ id: "a", title: "문헌 A", triage_status: "candidate" },
    { id: "b", title: "문헌 B", triage_status: "hold" }] },
} } as unknown as Job;

it("submits only selected candidates and links the actual analysis job", async () => {
  vi.spyOn(api, "searchComparisons").mockResolvedValue([]);
  const record: SearchComparison = { candidate_ids: ["a"], job_id: "analysis", status: "QUEUED", errors: [],
    sources: [{ candidate_id: "a", title: "문헌 A", status: "ready", reason: "본문 확보" }] };
  const submit = vi.spyOn(api, "compareSearchCandidates").mockResolvedValue(record);
  render(<MemoryRouter initialEntries={["/search?job=search"]}><SearchComparisonPanel job={job} /><Location /></MemoryRouter>);
  fireEvent.click(screen.getByLabelText("문헌 A"));
  vi.mocked(api.searchComparisons).mockResolvedValue([record]);
  fireEvent.click(screen.getByRole("button", { name: "선택한 1건 구성대비 시작" }));
  await waitFor(() => expect(submit).toHaveBeenCalledWith("search", ["a"]));
  expect((await screen.findByRole("link", { name: "구성대비 진행·결과 보기" })).getAttribute("href")).toBe("/analysis?job=analysis");
  expect(screen.getByLabelText("현재 화면").textContent).toBe("/analysis?job=analysis");
});

it("shows acquisition progress immediately and keeps held sources on the search screen", async () => {
  vi.spyOn(api, "searchComparisons").mockResolvedValue([]);
  let complete!: (record: SearchComparison) => void;
  vi.spyOn(api, "compareSearchCandidates").mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  render(<MemoryRouter initialEntries={["/search?job=search"]}><SearchComparisonPanel job={job} /><Location /></MemoryRouter>);
  fireEvent.click(screen.getByLabelText(/문헌 B/));
  fireEvent.click(screen.getByRole("button", { name: "선택한 1건 구성대비 시작" }));
  expect(screen.getByRole("status").textContent).toContain("분석이 시작되면 진행 화면으로 이동합니다.");
  expect((screen.getByRole("button", { name: "선택 문헌의 본문 확보 중…" }) as HTMLButtonElement).disabled).toBe(true);
  const held: SearchComparison = { candidate_ids: ["b"], job_id: null, status: "held", errors: [],
    sources: [{ candidate_id: "b", title: "문헌 B", status: "hold", reason: "본문 없음" }] };
  vi.mocked(api.searchComparisons).mockResolvedValue([held]);
  complete(held);
  await screen.findByText(/문헌 B: 보류 — 본문 없음/);
  expect(screen.getByLabelText("현재 화면").textContent).toBe("/search?job=search");
  expect(screen.queryByRole("status")).toBeNull();
});

it("shows request errors and permits another click", async () => {
  vi.spyOn(api, "searchComparisons").mockResolvedValue([]);
  vi.spyOn(api, "compareSearchCandidates").mockRejectedValue(new Error("본문 확보 요청 실패"));
  render(<MemoryRouter initialEntries={["/search?job=search"]}><SearchComparisonPanel job={job} /><Location /></MemoryRouter>);
  fireEvent.click(screen.getByLabelText("문헌 A"));
  fireEvent.click(screen.getByRole("button", { name: "선택한 1건 구성대비 시작" }));
  expect((await screen.findByRole("alert")).textContent).toBe("본문 확보 요청 실패");
  expect((screen.getByRole("button", { name: "선택한 1건 구성대비 시작" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByLabelText("현재 화면").textContent).toBe("/search?job=search");
});

it("shows held sources without pretending a comparison completed", async () => {
  vi.spyOn(api, "searchComparisons").mockResolvedValue([{ candidate_ids: ["b"], job_id: null,
    status: "unavailable", errors: [], sources: [{ candidate_id: "b", title: "문헌 B", status: "hold", reason: "본문 없음" }] }]);
  render(<MemoryRouter><SearchComparisonPanel job={job} /></MemoryRouter>);
  await screen.findByText(/문헌 B: 보류 — 본문 없음/);
  expect(screen.queryByRole("link", { name: "구성대비 진행·결과 보기" })).toBeNull();
});
