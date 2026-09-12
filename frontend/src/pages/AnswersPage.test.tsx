import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { AnswerCase } from "../lib/answers";
import AnswersPage from "./AnswersPage";

vi.mock("../lib/api", () => ({api: {
  answerCases: vi.fn(), history: vi.fn(), answerStyle: vi.fn(), answerCase: vi.fn(),
  updateAnswer: vi.fn(), approveAnswer: vi.fn(), registerAnswer: vi.fn(),
  extractAnswer: vi.fn(), saveAnswerStyle: vi.fn(), archiveAnswer: vi.fn(),
}}));

const example = {element: "C1 센서", issue: "제어 관계", judgment: "부분 대응", reason: "관계 미확인",
  report_quote: "센서는 있으나 제어 관계는 확인되지 않음.", source_id: "", source_quote: "", location: ""};
const row: AnswerCase = {id: "case-1", title: "센서 관계 보고서", status: "draft", revision: 0,
  edit_version: 2, example_count: 1, source_job_id: null, source_job_label: "", extraction_error: null,
  created_at: "2026-09-12", updated_at: "2026-09-12", claim_text: "C1 센서",
  report_text: example.report_quote, original_report: example.report_quote,
  files: [], extractions: [], draft: {style_rules: ["간결한 문장"], examples: [example]}};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.answerCases).mockResolvedValue([row]);
  vi.mocked(api.history).mockResolvedValue([]);
  vi.mocked(api.answerStyle).mockResolvedValue({text: "", path: "local/report-style.md", max_chars: 3000, token_budget: 6000});
  vi.mocked(api.answerCase).mockResolvedValue(structuredClone(row));
  vi.mocked(api.approveAnswer).mockResolvedValue({...row, status: "approved", revision: 1});
});
afterEach(cleanup);

async function openCase() {
  render(<MemoryRouter><AnswersPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", {name: /센서 관계 보고서/}));
  await screen.findByRole("button", {name: "AI로 판단·문체 정리"});
}

describe("정답 보고서 검토 흐름", () => {
  it("수정하지 않아도 최종본 확정 한 번으로 승인하며 코멘트를 요구하지 않는다", async () => {
    await openCase();
    expect(api.approveAnswer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "최종본 확정 · 다음 보고서에 활용"}));
    await waitFor(() => expect(api.approveAnswer).toHaveBeenCalledWith("case-1", 2));
    expect(api.updateAnswer).not.toHaveBeenCalled();
    await screen.findByText(/최종본으로 확정했습니다/);
  });

  it("수정본 저장을 완료한 버전으로 확정한다", async () => {
    await openCase();
    vi.mocked(api.updateAnswer).mockImplementation(async (_id, body) => ({...row, ...body, edit_version: 3}));
    fireEvent.change(screen.getByLabelText("정답 판단"), {target: {value: "대응 보류"}});
    fireEvent.click(screen.getByRole("button", {name: "최종본 확정 · 다음 보고서에 활용"}));
    await waitFor(() => expect(api.approveAnswer).toHaveBeenCalledWith("case-1", 3));
    expect(vi.mocked(api.updateAnswer).mock.calls[0][1].draft.examples[0].judgment).toBe("대응 보류");
  });

  it("기존 실행 연결은 PDF 없이 초안으로 등록하고 자동 확정하지 않는다", async () => {
    vi.mocked(api.registerAnswer).mockResolvedValue(row);
    render(<MemoryRouter initialEntries={["/answers?source=old-job"]}><AnswersPage /></MemoryRouter>);
    await screen.findByText("선택한 실행");
    fireEvent.change(screen.getByLabelText("사례 이름"), {target: {value: "내 최종 보고서"}});
    fireEvent.click(screen.getByRole("button", {name: "보고서 등록"}));
    await waitFor(() => expect(api.registerAnswer).toHaveBeenCalled());
    const body = vi.mocked(api.registerAnswer).mock.calls[0][0];
    expect(body.get("source_job_id")).toBe("old-job");
    expect(body.get("report")).toBeNull();
    expect(api.approveAnswer).not.toHaveBeenCalled();
    await screen.findByText(/정답 보고서를 등록했습니다/);
  });
});
