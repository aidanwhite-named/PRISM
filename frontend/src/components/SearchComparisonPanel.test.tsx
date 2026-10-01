import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { Job, SearchComparison } from "../lib/types";
import SearchComparisonPanel from "./SearchComparisonPanel";
import { MemoryRouter } from "react-router-dom";
import ProgressiveSearchResults from "./ProgressiveSearchResults";
import type { ProgressiveSearchSnapshot } from "../lib/types";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
  render(<MemoryRouter><SearchComparisonPanel job={job} /></MemoryRouter>);
  fireEvent.click(screen.getByLabelText("문헌 A"));
  vi.mocked(api.searchComparisons).mockResolvedValue([record]);
  fireEvent.click(screen.getByRole("button", { name: "선택한 1건 구성대비 시작" }));
  await waitFor(() => expect(submit).toHaveBeenCalledWith("search", ["a"]));
  expect((await screen.findByRole("link", { name: "구성대비 진행·결과 보기" })).getAttribute("href")).toBe("/analysis?job=analysis");
});

it("shows held sources without pretending a comparison completed", async () => {
  vi.spyOn(api, "searchComparisons").mockResolvedValue([{ candidate_ids: ["b"], job_id: null,
    status: "unavailable", errors: [], sources: [{ candidate_id: "b", title: "문헌 B", status: "hold", reason: "본문 없음" }] }]);
  render(<MemoryRouter><SearchComparisonPanel job={job} /></MemoryRouter>);
  await screen.findByText(/문헌 B: 보류 — 본문 없음/);
  expect(screen.queryByRole("link", { name: "구성대비 진행·결과 보기" })).toBeNull();
});
