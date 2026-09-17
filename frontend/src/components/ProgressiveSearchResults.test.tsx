import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ProgressiveSearchResults from "./ProgressiveSearchResults";
import type { ProgressiveSearchSnapshot } from "../lib/types";

describe("progressive search evidence", () => {
  afterEach(cleanup);
  it('shows provisional scope and separates missing data, negative review and omitted responses', () => {
    const base = { document_number: '', title: 'Candidate', url: '', publication_date: '', family_id: '',
      data_status: 'ABSTRACT_ONLY', date_status: 'no_date_limit', acquisitions: [], evidence: [] };
    const data: ProgressiveSearchSnapshot = { version: 1, phase: 'complete', stop_reason: 'classification_incomplete',
      depth: 'deep', elapsed_seconds: 120, first_candidate_seconds: 25, features: [], queries: [], warnings: [],
      classification: { status: 'incomplete', target_count: 4, reviewed_count: 3, unreviewed_count: 1 },
      candidates: [
        { ...base, id: 'a', document_classification: { group: 'X', status: 'classified', reason: '초록의 관계 유사', basis: 'abstract', evidence_status: 'unverified', provisional: true } },
        { ...base, id: 'b', document_classification: { group: null, status: 'insufficient_information', reason: '본문 필요', basis: 'search_metadata', evidence_status: 'unverified' } },
        { ...base, id: 'c', document_classification: { group: null, status: 'low_relevance', reason: '다른 분야', basis: 'abstract', evidence_status: 'unverified' } },
        { ...base, id: 'd' },
      ] };
    render(<ProgressiveSearchResults data={data} />);
    expect(screen.getByRole('alert').textContent).toContain('분류 미완료');
    expect(screen.getByRole('alert').textContent).toContain('검토 3/4건');
    expect(screen.getAllByText(/초록 기준 · 잠정 판단/)).toHaveLength(2);
    expect(screen.getByText(/자료 부족/)).toBeTruthy();
    expect(screen.getByText(/검토 결과 관련성 낮음/)).toBeTruthy();
    expect(screen.getByText('미평가')).toBeTruthy();
    expect(screen.getByText('X분류', { selector: 'strong' })).toBeTruthy();
  });
  it('explains fallback as unverified X rather than document absence and shows actual queries', () => {
    const data: ProgressiveSearchSnapshot = { version: 1, phase: 'complete', stop_reason: 'fast_budget_complete',
      depth: 'deep', elapsed_seconds: 30, first_candidate_seconds: 3, features: [], candidates: [], warnings: [],
      route: [{ lane: 'relation_seed', outcome: 'no_verified_x', seconds: 10 },
              { lane: 'existing_search', reason: 'no_verified_x' }],
      queries: [{ id: 'q', source: 'epo', query: 'point cloud connectivity geometry',
                  lane: 'relation_seed', status: 'completed', hits: 9 }] };
    render(<ProgressiveSearchResults data={data} />);
    expect(screen.getByText(/관계 중심 검색 · X 미확인/)).toBeTruthy();
    expect(screen.getByText(/기존 특허·논문 검색 · X 미확인으로 후속 검색 진행/)).toBeTruthy();
    expect(screen.getByText('point cloud connectivity geometry')).toBeTruthy();
  });
  it("keeps feature evidence and unknown results separate, grouping only known families", () => {
    const candidate = { id: "a", document_number: "WO123A1", title: "Gaussian method", url: "https://example.org/",
      publication_date: "2024-01-01", family_id: "family", data_status: "FULL_TEXT", date_status: "no_date_limit",
      document_classification: { group: "A" as const, reason: "전체 구조와 핵심 특징 유사", basis: "retrieved_passages", evidence_status: "quotes_available" },
      acquisitions: [], evidence: [{ feature: "B", match: "partial", relation: "KL distance and gradient decide cloning",
        difference: "Center distance only chooses a neighbor", quote: "KL divergence guides cloning", quote_verified: true,
        locator: { page: 5, url: "https://example.org/" } }] };
    const data: ProgressiveSearchSnapshot = { version: 1, phase: "complete", stop_reason: "deadline_reserve", depth: "deep",
      elapsed_seconds: 115, first_candidate_seconds: 4, features: [{ id: "A", text: "SVD covariance", relation: "" },
        { id: "B", text: "distance cloning", relation: "" }], candidates: [candidate,
        { ...candidate, id: "b", document_number: "US123A1" }], queries: [], warnings: [] };
    render(<ProgressiveSearchResults data={data} />);
    expect(screen.getByText("미검증")).toBeTruthy();
    expect(screen.getByText("부분 대응")).toBeTruthy();
    expect(screen.getByText("KL divergence guides cloning")).toBeTruthy();
    expect(screen.getByText("같은 family 2개 문헌")).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(1);
    expect(screen.queryByRole('combobox', { name: '문헌 분류 보기' })).toBeNull();
    expect(screen.queryByText('SVD covariance')).toBeNull();
    expect(screen.queryByText('distance cloning')).toBeNull();
  });

  it('displays X Y Z before unclassified while preserving order within a category', () => {
    const candidates: ProgressiveSearchSnapshot['candidates'] = ['C', 'B', 'X', 'Y', null].map((group, i) => ({
      id: String(i), document_number: String(i), title: `Document ${i}`, url: '', family_id: '',
      publication_date: '', data_status: 'ABSTRACT_ONLY', date_status: 'no_date_limit', acquisitions: [], evidence: [],
      document_classification: { group: group as 'C' | 'B' | 'X' | 'Y' | null, reason: '', basis: '', evidence_status: '' },
    }));
    const data: ProgressiveSearchSnapshot = { version: 1, phase: 'complete', stop_reason: 'done', depth: 'deep',
      elapsed_seconds: 1, first_candidate_seconds: 1, features: [{ id: 'A', text: 'claim feature', relation: '' }],
      candidates, queries: [], warnings: [] };
    render(<ProgressiveSearchResults data={data} />);
    expect(screen.getAllByRole('heading', { level: 3, hidden: true }).map(h => h.textContent)).toEqual([
      '1. Document 2', '2. Document 1', '3. Document 3', '4. Document 0', '5. Document 4',
    ]);
    expect(data.candidates[0].id).toBe('0');
    expect(screen.getByText('X분류', { selector: 'strong' })).toBeTruthy();
    const folded = screen.getByText('미분류 문헌 · 1개 문헌군').closest('details');
    expect(folded?.open).toBe(false);
    expect(folded?.textContent).toContain('Document 4');
    expect(folded?.textContent).not.toContain('Document 2');
  });
});
