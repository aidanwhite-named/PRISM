import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PrismSecret from "./PrismSecret";

beforeEach(() => {
  // jsdom does not implement native modal dialogs.
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("프리즘의 작은 관측실", () => {
  it("일상 화면에는 숨겨 두고, 발견하면 입체 프리즘을 조작하고 닫을 수 있다", async () => {
    render(<PrismSecret />);
    const fragment = screen.getByRole("button", { name: "프리즘 조각" });
    fireEvent.click(fragment);
    fireEvent.click(fragment);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(fragment);
    await screen.findByRole("group", { name: /입체 프리즘/ }, { timeout: 5000 });
    expect(screen.getByRole("dialog").hasAttribute("open")).toBe(true);
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.change(screen.getByRole("slider", { name: /좌우 회전/ }), { target: { value: "20" } });
    expect(screen.getByText(/YAW 20°/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "300 nm" }));
    expect(screen.getByText("자외선", { exact: true })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "관측실 닫기" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(document.activeElement).toBe(fragment);
  });
  it("Escape 취소로도 관측실을 닫고 원래 화면으로 돌아온다", async () => {
    render(<PrismSecret />);
    const fragment = screen.getByRole("button", { name: "프리즘 조각" });
    for (let i = 0; i < 3; i++) fireEvent.click(fragment);
    await screen.findByRole("group", { name: /입체 프리즘/ }, { timeout: 5000 });
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { bubbles: true }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(fragment);
  });
  it("오래 떨어진 클릭은 발견 동작으로 세지 않는다", () => {
    const now = vi.spyOn(Date, "now");
    render(<PrismSecret />);
    const fragment = screen.getByRole("button", { name: "프리즘 조각" });
    now.mockReturnValue(1000); fireEvent.click(fragment);
    now.mockReturnValue(4000); fireEvent.click(fragment);
    now.mockReturnValue(4100); fireEvent.click(fragment);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
