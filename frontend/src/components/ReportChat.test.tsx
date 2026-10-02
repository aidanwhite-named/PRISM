import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ReportChat from "./ReportChat";
import type { Job, ReportChatTurn } from "../lib/types";

vi.mock("../lib/api", () => ({ api: {
  reportChat: vi.fn(), askReport: vi.fn(), cancelReportAnswer: vi.fn(),
} }));
const { api } = await import("../lib/api");
const job = { id: "report-a", claim_text: "청구항 1. 센서를 포함하는 장치" } as Job;
const turn = { id: "turn-1", job_id: job.id, question: "주 문헌에 없는 부분은?", answer: "인용발명 1의 **E1**에서 확인되지 않습니다.",
  status: "succeeded", context_scope: "report_evidence", error: null, created_at: "2026-10-02T01:00:00", completed_at: null } as ReportChatTurn;

beforeEach(() => {
  vi.mocked(api.reportChat).mockResolvedValue([]);
  vi.mocked(api.askReport).mockResolvedValue({ ...turn, status: "queued", answer: null });
});
afterEach(() => { cleanup(); sessionStorage.clear(); vi.clearAllMocks(); vi.useRealTimers(); });

async function open() {
  await userEvent.click(screen.getByRole("button", { name: "보고서 수정·보완" }));
  await waitFor(() => expect(api.reportChat).toHaveBeenCalledWith(job.id));
  await waitFor(() => expect(screen.queryByText("대화를 불러오는 중…")).toBeNull());
}

it("opens an accessible corner conversation and collapses without losing its draft", async () => {
  render(<ReportChat job={job} onRevise={vi.fn()} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  await open();
  expect(screen.getByRole("dialog", { name: "보고서 도우미" })).toBeTruthy();
  const input = screen.getByRole("textbox", { name: "보고서에 대한 질문" });
  await userEvent.type(input, "구성 A의 차이를 설명해줘");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  await open();
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("구성 A의 차이를 설명해줘");
});

it("sends a question to the current report and blocks another send during its answer", async () => {
  vi.mocked(api.reportChat).mockResolvedValueOnce([]).mockResolvedValue([{ ...turn, answer: null, status: "running" }]);
  const revise = vi.fn();
  render(<ReportChat job={job} onRevise={revise} />);
  await open();
  await userEvent.type(screen.getByRole("textbox"), "추가 문헌이 필요한 이유?{Enter}");
  expect(api.askReport).toHaveBeenCalledWith(job.id, "추가 문헌이 필요한 이유?", expect.any(String));
  await screen.findByText("보고서와 근거를 확인하고 있습니다…");
  await userEvent.type(screen.getByRole("textbox"), "다음 질문{Enter}");
  expect(api.askReport).toHaveBeenCalledTimes(1);
  expect((screen.getByRole("button", { name: "대화 내용으로 수정·보완" }) as HTMLButtonElement).disabled).toBe(true);
  expect(revise).not.toHaveBeenCalled();
});

it("restores saved dialogue, renders a safe answer and passes dialogue to revision only on click", async () => {
  vi.mocked(api.reportChat).mockResolvedValue([turn]);
  const revise = vi.fn();
  render(<ReportChat job={job} onRevise={revise} />);
  await open();
  await screen.findByText("주 문헌에 없는 부분은?");
  expect(screen.getByText("E1").tagName).toBe("STRONG");
  expect(revise).not.toHaveBeenCalled();
  await userEvent.type(screen.getByRole("textbox"), "이 부분을 더 짧게 써줘");
  await userEvent.click(screen.getByRole("button", { name: "대화 내용으로 수정·보완" }));
  expect(revise).toHaveBeenCalledWith(expect.stringContaining(turn.question));
  expect(revise).toHaveBeenCalledWith(expect.stringContaining("사용자의 추가 수정 요청: 이 부분을 더 짧게 써줘"));
});

it("preserves an unsuccessful question and reuses its request id on network retry", async () => {
  vi.mocked(api.askReport).mockRejectedValueOnce(new Error("연결이 끊겼습니다"));
  render(<ReportChat job={job} onRevise={vi.fn()} />);
  await open();
  await userEvent.type(screen.getByRole("textbox"), "차이를 설명해줘");
  await userEvent.click(screen.getByRole("button", { name: "전송" }));
  await screen.findByRole("alert");
  await waitFor(() => expect((screen.getByRole("button", { name: "전송" }) as HTMLButtonElement).disabled).toBe(false));
  await userEvent.click(screen.getByRole("button", { name: "전송" }));
  expect(vi.mocked(api.askReport).mock.calls[1][2]).toBe(vi.mocked(api.askReport).mock.calls[0][2]);
});

it("does not send on Shift+Enter or while composing Korean text", async () => {
  render(<ReportChat job={job} onRevise={vi.fn()} />);
  await open();
  const input = screen.getByRole("textbox");
  await userEvent.type(input, "구성 A");
  await userEvent.keyboard("{Shift>}{Enter}{/Shift}");
  expect(api.askReport).not.toHaveBeenCalled();
  await act(async () => { input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, isComposing: true })); });
  expect(api.askReport).not.toHaveBeenCalled();
});

it("can stop an answer and retry a failed question", async () => {
  vi.mocked(api.reportChat).mockResolvedValue([{ ...turn, status: "running", answer: null }]);
  vi.mocked(api.cancelReportAnswer).mockResolvedValue({ ...turn, status: "cancelled", answer: null, error: "답변 작성을 중단했습니다." });
  render(<ReportChat job={job} onRevise={vi.fn()} />);
  await open();
  vi.mocked(api.reportChat).mockResolvedValue([{ ...turn, status: "cancelled", answer: null, error: "답변 작성을 중단했습니다." }]);
  await userEvent.click(screen.getByRole("button", { name: "답변 중단" }));
  expect(api.cancelReportAnswer).toHaveBeenCalledWith(job.id, turn.id);
  await screen.findByText("답변 작성을 중단했습니다.");
  await waitFor(() => expect((screen.getByRole("button", { name: "다시 질문" }) as HTMLButtonElement).disabled).toBe(false));
  await userEvent.click(screen.getByRole("button", { name: "다시 질문" }));
  expect(api.askReport).toHaveBeenCalledWith(job.id, turn.question, expect.any(String));
});

it("keeps conversations and unsent drafts separate for each report", async () => {
  const view = render(<ReportChat key={job.id} job={job} onRevise={vi.fn()} />);
  await open();
  await userEvent.type(screen.getByRole("textbox"), "보고서 A 질문");
  view.rerender(<ReportChat key="report-b" job={{ ...job, id: "report-b" }} onRevise={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "보고서 수정·보완" }));
  await waitFor(() => expect(api.reportChat).toHaveBeenCalledWith("report-b"));
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  expect(screen.queryByText("보고서 A 질문")).toBeNull();
});

it("keeps checking a pending answer while the conversation is collapsed", async () => {
  vi.mocked(api.reportChat).mockResolvedValue([{ ...turn, status: "running", answer: null }]);
  render(<ReportChat job={job} onRevise={vi.fn()} />);
  await open();
  await screen.findByText("보고서와 근거를 확인하고 있습니다…");
  vi.useFakeTimers();
  await act(async () => { fireEvent.click(document.querySelector(".report-chat-launcher")!); });
  expect(screen.queryByRole("dialog")).toBeNull();
  vi.mocked(api.reportChat).mockResolvedValue([turn]);
  await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
  expect(document.querySelector(".report-chat-launcher .spinner")).toBeNull();
});
