import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import ProgressiveSearchResults from "./ProgressiveSearchResults";
import type { ProgressiveSearchSnapshot } from "../lib/types";

afterEach(cleanup);

it("shows every model-ranked finding without classification or candidate truncation", () => {
  const data: ProgressiveSearchSnapshot = {
    mode: "autonomous", version: 2, phase: "complete", stop_reason: "deadline_reserve", depth: "deep",
    elapsed_seconds: 300, first_candidate_seconds: 15, features: [], warnings: [], queries: [],
    candidates: Array.from({ length: 130 }, (_, i) => ({
      id: String(i), title: `문헌 ${130 - i}`, document_number: "", url: `https://example.org/${i}`,
      publication_date: "", date_status: "publication_date_unknown", family_id: "", data_status: "MODEL_REPORTED",
      reason: `기술 관계 ${i}`, reported_scope: "초록", evidence: [], acquisitions: [],
    })),
  };
  render(<ProgressiveSearchResults data={data} />);
  expect(screen.getAllByRole("link", { name: "문헌 보기" })).toHaveLength(130);
  expect(screen.getAllByRole("heading", { level: 3 })[0].textContent).toBe("1. 문헌 130");
  expect(screen.getByRole("heading", { name: "130. 문헌 1" })).toBeTruthy();
  expect(screen.queryByText("X분류")).toBeNull();
  expect(screen.queryByText(/미분류 문헌/)).toBeNull();
  expect(screen.getByText(/확보한 문헌과 출처를 보존/)).toBeTruthy();
});

it("does not display unsafe source links or mix observed access with model-reported scope", () => {
  const data: ProgressiveSearchSnapshot = {
    mode: "autonomous", version: 2, phase: "complete", stop_reason: "model_complete", depth: "deep",
    elapsed_seconds: 30, first_candidate_seconds: 15, features: [], warnings: [], queries: [],
    candidates: [{ id: "a", title: "자료", document_number: "", url: "javascript:alert(1)",
      publication_date: "", date_status: "publication_date_unknown", family_id: "", data_status: "MODEL_REPORTED",
      reason: "관련성", reported_scope: "본문", evidence: [], acquisitions: [],
      observed_scope: "초록 확보 · 본문 확보 기록 없음", observed_scopes: ["abstract"],
      source_receipts: [{ call_id: "x", tool: "literature_search", scope: "bibliographic_search" }],
    }],
  };
  render(<ProgressiveSearchResults data={data} />);
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText("LLM이 보고한 확인 범위: 본문")).toBeTruthy();
  expect(screen.getByText("프로그램이 확보한 자료: 초록 확보 · 본문 확보 기록 없음")).toBeTruthy();
  expect(screen.getByText("AI가 보고한 확인 범위(작성 당시)")).toBeTruthy();
  expect(screen.getByText("literature_search · bibliographic_search")).toBeTruthy();
});
