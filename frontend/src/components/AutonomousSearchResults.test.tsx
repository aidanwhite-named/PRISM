import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import AutonomousResults from "./AutonomousSearchResults";
import type { ProgressiveSearchSnapshot } from "../lib/types";

afterEach(cleanup);

it("shows verified groups and X early stop without classifying unchecked leads", () => {
  const candidates = [
    { id: "1", title: "X 문헌", search_review: { status: "source_checked", verdict: "strong", group: "X" } },
    { id: "2", title: "미검증 문헌", search_review: { status: "needs_review", verdict: "partial", group: "Z" } },
  ].map(c => ({ ...c, url: `https://example.org/${c.id}`, date_status: "no_date_limit" }));
  const data = { candidates, phase: "complete", stop_reason: "x_found", elapsed_seconds: 40,
    limits: { seconds: 360, search_seconds: 240, verification_seconds: 120 }, queries: [], warnings: [],
    summary: "X 후보 중복 설명" } as unknown as ProgressiveSearchSnapshot;
  render(<AutonomousResults data={data} />);
  expect(screen.getByText(/X · 전체 구조와 핵심 특징이 모두 강하게 유사/)).toBeTruthy();
  expect(screen.queryByText(/Z · 전체 구조는 유사/)).toBeNull();
  expect(screen.getByText(/나머지 후보의 추가 검증은 생략/)).toBeTruthy();
  expect(screen.getByText(/후보 탐색 최대 240초 · 원문 검증 최대 120초/)).toBeTruthy();
  expect(screen.getByRole("heading", { name: "1. [X] X 문헌" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "2. [미분류] 미검증 문헌" })).toBeTruthy();
  expect(screen.queryByText("검색 설명")).toBeNull();
  expect(screen.queryByText("X 후보 중복 설명")).toBeNull();
});

it("distinguishes source-checked similarity from unavailable and unverified leads", () => {
  const candidates = [
    { id: "1", title: "확인 문헌", search_review: { status: "source_checked", verdict: "partial",
      gaps: "샘플별 마스크 차이", passages: [{ feature: "번호 기반 생성", relation: "초기값으로 사용",
        quote: "The frame number initializes the generator.", translation: "프레임 번호로 생성기를 초기화합니다.",
        scope: "claims", start: 0, end: 42, pages: [], url: "https://example.org/1" }] } },
    { id: "2", title: "미확인 문헌" },
    { id: "3", title: "접근불가 문헌", search_review: { status: "unavailable", verdict: "unavailable" } },
  ].map(c => ({ ...c, url: `https://example.org/${c.id}`, date_status: "no_date_limit" }));
  const data = { candidates, phase: "complete", elapsed_seconds: 1, queries: [], warnings: [] } as unknown as ProgressiveSearchSnapshot;
  render(<AutonomousResults data={data} />);
  expect(screen.getByText(/일부 핵심 대응.*발췌 일치 확인/)).toBeTruthy();
  expect(screen.getByText(/검색 중 원문 대조: 근거 확인 미완료/)).toBeTruthy();
  expect(screen.getByText(/원문 확보·확인 불가/)).toBeTruthy();
  expect(screen.getByText("The frame number initializes the generator.")).toBeTruthy();
  expect(screen.getByText(/샘플별 마스크 차이/)).toBeTruthy();
});

it("derives classification counts and explanation from reviews instead of optimistic model text", () => {
  const data = { candidates: [{ id: "1", title: "후보", url: "https://example.org/1",
    date_status: "no_date_limit", reason: "이전 설명", difference: "이전 차이",
    search_review: { status: "needs_review", group: "Y", verdict: "strong", reason: "최신 잠정 판단",
      gaps: "확인이 필요한 관계", issues: ["원문 발췌 불일치"] } }],
    phase: "complete", elapsed_seconds: 1, queries: [], warnings: [], summary: "Y 검증 완료",
    excluded_input_documents: [{ title: "입력 논문", url: "https://doi.org/10.1234/self", document_number: "10.1234/self" }],
  } as unknown as ProgressiveSearchSnapshot;
  render(<AutonomousResults data={data} />);
  expect(screen.getByText("원문 근거 검증을 통과한 분류: X 0건, Y 0건, Z 0건")).toBeTruthy();
  expect(screen.getByText(/근거 확인 미완료 1건/)).toBeTruthy();
  expect(screen.getByText(/AI 잠정 판단.*최신 잠정 판단/)).toBeTruthy();
  expect(screen.getByText(/검증 미완료 사유: 원문 발췌 불일치/)).toBeTruthy();
  expect(screen.getByText(/동일한 문헌 1건은 후보에서 제외/)).toBeTruthy();
  expect(screen.queryByText("이전 설명")).toBeNull();
  expect(screen.queryByText("Y 검증 완료")).toBeNull();
});
