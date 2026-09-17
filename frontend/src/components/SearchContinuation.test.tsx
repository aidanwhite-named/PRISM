import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import SearchContinuation from "./SearchContinuation";
import { api } from "../lib/api";
import type { Job } from "../lib/types";

vi.mock("../lib/api", () => ({ api: { continueSearch: vi.fn() } }));
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { cleanup(); sessionStorage.clear(); vi.clearAllMocks(); });
const job = { id: "basic", status: "SUCCEEDED", job_kind: "similarity_search",
  search_manifest: { engine: { can_continue: true, verified_match: false } } } as unknown as Job;

it("offers precision only after basic completion and dismisses without executing", () => {
  const { rerender } = render(<SearchContinuation job={{ ...job, status: "RUNNING" }} disabled onContinued={vi.fn()} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  rerender(<SearchContinuation job={job} disabled={false} onContinued={vi.fn()} />);
  expect(screen.getByRole("dialog")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "현재 결과 보기" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.continueSearch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "정밀 검색 이어서 진행" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it("continues the saved job, suppresses duplicate clicks, and hands off its result", async () => {
  let resolve!: (value: Job) => void;
  vi.mocked(api.continueSearch).mockImplementation(() => new Promise(done => { resolve = done; }));
  const onContinued = vi.fn();
  render(<SearchContinuation job={job} disabled={false} onContinued={onContinued} />);
  fireEvent.click(screen.getByRole("button", { name: "정밀 검색 계속" }));
  expect((screen.getByRole("button", { name: "이어가는 중…" }) as HTMLButtonElement).disabled).toBe(true);
  const next = { ...job, id: "precision", status: "QUEUED" } as Job;
  resolve(next);
  await waitFor(() => expect(onContinued).toHaveBeenCalledWith(next));
  expect(api.continueSearch).toHaveBeenCalledExactlyOnceWith("basic");
});

it("keeps the dialog open after an error so continuation can be retried", async () => {
  vi.mocked(api.continueSearch).mockRejectedValueOnce(new Error("예산 부족"));
  render(<SearchContinuation job={job} disabled={false} onContinued={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "정밀 검색 계속" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "예산 부족");
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it("does not automatically prompt for verified matches or precision results", () => {
  const matched = { ...job, search_manifest: { engine: { can_continue: true, verified_match: true } } } as unknown as Job;
  const { rerender } = render(<SearchContinuation job={matched} disabled={false} onContinued={vi.fn()} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "정밀 검색 이어서 진행" })).toBeTruthy();
  const precision = { ...job, search_manifest: { engine: { can_continue: false, verified_match: false } } } as unknown as Job;
  rerender(<SearchContinuation job={precision} disabled={false} onContinued={vi.fn()} />);
  expect(screen.queryByRole("button")).toBeNull();
});
