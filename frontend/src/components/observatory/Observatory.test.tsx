import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Observatory from "./Observatory";

let frames: Map<number, FrameRequestCallback>, frameId: number;
beforeEach(() => {
  localStorage.clear(); frames = new Map(); frameId = 0;
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { const id = ++frameId; frames.set(id, callback); return id; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function tick(time: number) { act(() => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(time)); }); }
function room(name: RegExp) { fireEvent.click(screen.getByRole("tab", { name })); }

describe("숨은 관측실 탐험", () => {
  it("게임은 잘못된 파장에 힌트를 주고 다섯 문제를 끝까지 진행한다", () => {
    render(<Observatory onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /빛의 암호 게임 시작/ }));
    fireEvent.change(screen.getByRole("slider", { name: "빛의 파장" }), { target: { value: "900" } });
    fireEvent.click(screen.getByRole("button", { name: "이 파장으로 확인" }));
    expect(screen.queryByRole("button", { name: /다음 빛 찾기/ })).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("짧게");
    for (const value of [550, 450, 300, 1000, 650]) {
      fireEvent.change(screen.getByRole("slider", { name: "빛의 파장" }), { target: { value: String(value) } });
      fireEvent.click(screen.getByRole("button", { name: "이 파장으로 확인" }));
      if (value !== 650) fireEvent.click(screen.getByRole("button", { name: /다음 빛 찾기/ }));
    }
    expect(screen.getByText(/다섯 개의 빛을 모두 찾았습니다/)).toBeTruthy();
  });
  it("기호로 원소를 찾고 이야기·근거를 읽으며 발견 기록을 중복 없이 저장한다", () => {
    render(<Observatory onClose={() => {}} />); room(/원소의 서가/);
    expect(screen.getAllByRole("button", { name: /^\d+ [A-Z]/ })).toHaveLength(118);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "바나듐" } });
    fireEvent.click(screen.getByRole("button", { name: "V · 바나듐" }));
    fireEvent.click(screen.getByRole("button", { name: /장 멍게 → 바나듐/ }));
    expect(screen.getByText(/멍게의 겉색을 모두/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /장 우회하는 칭찬/ }));
    expect(screen.getByText(/중간에서 끝내면 해산물 리뷰/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /V · 멍게에서 여신까지/ }));
    expect(JSON.parse(localStorage.getItem("prism.observatory.discoveries.v1")!)).toEqual(["V"]);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "은" } });
    expect(within(screen.getByRole("region", { name: "원소 검색 결과" })).getAllByRole("button")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Ag · 은" }));
    fireEvent.click(screen.getByRole("button", { name: /장 1503년/ }));
    expect(screen.getByText(/양인 김감불과 장례원 종 김검동/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /연산군일기/ }).getAttribute("href")).toContain("kja_10905018_003");
  });
  it("세슘 시계를 1초까지 재생하고 방을 떠나면 진행 작업을 정리한다", () => {
    render(<Observatory onClose={() => {}} />); room(/1초의 탄생/);
    fireEvent.click(screen.getByRole("button", { name: /1초 동안 함께 세기/ }));
    tick(100); tick(600); expect(screen.getByText("4,596,315,885")).toBeTruthy();
    tick(1100); expect(screen.getByText("9,192,631,770", { selector: ".clock-counter" })).toBeTruthy();
    expect(frames.size).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: /1초 동안 함께 세기/ }));
    expect(frames.size).toBe(1); room(/빛의 방/); expect(frames.size).toBe(0);
  });
  it("시간의 두 이야기를 전환하고 원자의 여정을 떠나면 재생을 정리한다", () => {
    render(<Observatory onClose={() => {}} />); room(/1초의 탄생/);
    fireEvent.click(screen.getByRole("button", { name: /잠시, 나라는 모양/ }));
    expect(screen.getByRole("slider", { name: "원자의 시간축" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /별에서부터 재생/ }));
    expect(frames.size).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "1초의 눈금" }));
    expect(frames.size).toBe(0);
    expect(screen.getByRole("button", { name: /1초 동안 함께 세기/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /잠시, 나라는 모양/ }));
    fireEvent.click(screen.getByRole("button", { name: /별에서부터 재생/ }));
    room(/원소의 서가/); expect(frames.size).toBe(0);
  });
  it("별의 마지막 장에서 펄사와 블랙홀을 비교하고 가설의 방으로 연결한다", () => {
    render(<Observatory onClose={() => {}} />); room(/별의 다음 장/);
    fireEvent.click(screen.getByRole("button", { name: /06 남은 핵/ }));
    expect(screen.getByRole("slider", { name: "관찰 방향" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "블랙홀" }));
    expect(screen.queryByRole("slider", { name: "관찰 방향" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /블랙홀의 먼 미래/ }));
    expect(screen.getByText("연구 가설", { exact: true })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /03 눈금 없는 경계/ }));
    expect(screen.getByText(/작은 원자와 큰 우주가 실제로 같은 물체라는 뜻은 아닙니다/)).toBeTruthy();
  });
  it("휠은 그림 안의 눈금만 바꾸고 양끝 범위를 넘지 않는다", () => {
    render(<Observatory onClose={() => {}} />); room(/우주의 눈금/);
    const field = screen.getByRole("group", { name: /우주 스케일 탐험/ });
    expect(fireEvent.wheel(field, { deltaY: 100, cancelable: true })).toBe(false);
    expect((screen.getByRole("slider", { name: /우주의 눈금/ }) as HTMLInputElement).value).toBe("0.8");
    fireEvent.change(screen.getByRole("slider", { name: /우주의 눈금/ }), { target: { value: "27" } });
    fireEvent.wheel(field, { deltaY: 1000 });
    expect((screen.getByRole("slider", { name: /우주의 눈금/ }) as HTMLInputElement).value).toBe("27");
  });
  it("탐정 게임은 선택한 원소를 검증하고 다음 단서를 연다", () => {
    render(<Observatory onClose={() => {}} />); room(/원소의 서가/);
    fireEvent.click(screen.getByRole("button", { name: /원소 탐정 게임/ }));
    fireEvent.click(screen.getByRole("button", { name: "선택한 원소로 확인" }));
    expect(screen.getByRole("status").textContent).toContain("아직");
    fireEvent.click(screen.getByRole("button", { name: "23 V 바나듐" }));
    fireEvent.click(screen.getByRole("button", { name: "선택한 원소로 확인" }));
    fireEvent.click(screen.getByRole("button", { name: /다음 단서/ }));
    expect(screen.getByText(/아르헨티나의 이름과 라틴어 뿌리를 공유/)).toBeTruthy();
  });
});
