import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AtomJourney from "./AtomJourney";
import { atomPosition, JOURNEY_ATOMS, JOURNEY_END, JOURNEY_STAGES, journeyPosition } from "./journeyGeometry";

let pending: Map<number, FrameRequestCallback>, frameId: number;
beforeEach(() => {
  pending = new Map(); frameId = 0;
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => { pending.set(++frameId, fn); return frameId; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => pending.delete(id));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function tick(time: number) { act(() => { const callbacks = [...pending.values()]; pending.clear(); callbacks.forEach(fn => fn(time)); }); }

describe("잠시, 나라는 모양", () => {
  it("모든 원자의 위치는 그림 안에 있고 장면 경계에서도 연속적으로 이어진다", () => {
    for (let id = 0; id < JOURNEY_ATOMS; id++) {
      for (let stage = 0; stage <= JOURNEY_END; stage++) {
        const p = atomPosition(id, stage);
        expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
        expect(p.x).toBeGreaterThan(0); expect(p.x).toBeLessThan(800);
        expect(p.y).toBeGreaterThan(0); expect(p.y).toBeLessThan(360);
        if (stage > 0 && stage < JOURNEY_END) {
          const left = journeyPosition(id, stage - .001), right = journeyPosition(id, stage + .001);
          expect(Math.hypot(left.x - right.x, left.y - right.y)).toBeLessThan(.01);
        }
      }
    }
  });
  it("장면이 바뀌어도 같은 96개 점과 선택한 점을 유지하며 설명을 갱신한다", () => {
    const { container } = render(<AtomJourney animated />);
    const initial = [...container.querySelectorAll("[data-atom-id]")];
    expect(initial).toHaveLength(96);
    fireEvent.change(screen.getByRole("slider", { name: "따라갈 금빛 점" }), { target: { value: "43" } });
    for (const [index, stage] of JOURNEY_STAGES.entries()) {
      fireEvent.click(screen.getByRole("button", { name: `${index + 1}장 ${stage.name}` }));
      expect(screen.getByRole("heading", { name: stage.title })).toBeTruthy();
      expect(screen.getByRole("img", { name: new RegExp(`${stage.name} 모습.*선택한 점 43$`) })).toBeTruthy();
      expect([...container.querySelectorAll("[data-atom-id]")]).toEqual(initial);
    }
    fireEvent.click(initial[23]);
    expect((screen.getByRole("slider", { name: "따라갈 금빛 점" }) as HTMLInputElement).value).toBe("24");
    fireEvent.click(screen.getByRole("button", { name: /지금의 나로 돌아오기/ }));
    expect(screen.getByRole("heading", { name: JOURNEY_STAGES[7].title })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "시간의 눈으로" }));
    expect(screen.getByText("한 원자의 길을 이어 보면")).toBeTruthy();
  });
  it("재생은 전역 일시정지·끝·직접 조작·닫기에서 프레임을 정리한다", () => {
    const { rerender, unmount } = render(<AtomJourney animated />);
    fireEvent.click(screen.getByRole("button", { name: /별에서부터 재생/ }));
    expect(pending.size).toBe(1); tick(0); tick(3800);
    expect((screen.getByRole("slider", { name: "원자의 시간축" }) as HTMLInputElement).value).toBe("1");
    rerender(<AtomJourney animated={false} />); expect(pending.size).toBe(0);
    expect((screen.getByRole("button", { name: /별에서부터 재생/ }) as HTMLButtonElement).disabled).toBe(true);
    rerender(<AtomJourney animated />); expect(pending.size).toBe(1);
    tick(10000); tick(48000); expect(pending.size).toBe(0);
    expect(screen.getByRole("heading", { name: JOURNEY_STAGES[9].title })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /별에서부터 재생/ }));
    fireEvent.change(screen.getByRole("slider", { name: "원자의 시간축" }), { target: { value: "2" } });
    expect(pending.size).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: /별에서부터 재생/ }));
    unmount(); expect(pending.size).toBe(0);
  });
  it("시적 사유와 과학적 설명의 범위, 공식 근거를 함께 제공한다", () => {
    render(<AtomJourney animated={false} />);
    fireEvent.click(screen.getByRole("button", { name: /조상$/ }));
    expect(screen.getByText(/특정 원자가 내 조상의 몸을 거쳤다고 확인한 기록은 없습니다/)).toBeTruthy();
    expect(screen.getByText(/우리 몸의 수소 대부분은 별보다 앞선/)).toBeTruthy();
    expect(screen.getByText(/인격의 재탄생을 과학적으로 증명한다는 뜻은 아닙니다/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /NOAA/ }).getAttribute("href")).toBe("https://oceanservice.noaa.gov/facts/carbon-cycle.html");
    expect(screen.getByRole("link", { name: /Stanford/ }).getAttribute("href")).toBe("https://plato.stanford.edu/entries/shankara/");
  });
});
