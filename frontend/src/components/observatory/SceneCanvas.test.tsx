import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SceneCanvas from "./SceneCanvas";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("관측실 애니메이션의 수명", () => {
  it("일시정지와 닫기에서 다음 프레임과 크기 감시를 해제한다", () => {
    const gradient = { addColorStop: () => {} };
    const context = new Proxy({ createRadialGradient: () => gradient, createLinearGradient: () => gradient }, { get(target, key) { return key in target ? target[key as keyof typeof target] : () => {}; } });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
    const pending = new Map<number, FrameRequestCallback>(); let id = 0;
    vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => { pending.set(++id, fn); return id; });
    vi.stubGlobal("cancelAnimationFrame", (frame: number) => pending.delete(frame));
    const disconnect = vi.fn(); vi.stubGlobal("ResizeObserver", class { observe() {} disconnect = disconnect; });
    const { rerender, unmount } = render(<SceneCanvas scene="neutron" label="펄사" animated />);
    expect(pending.size).toBe(1);
    act(() => { const callbacks = [...pending.values()]; pending.clear(); callbacks.forEach(fn => fn(100)); });
    expect(pending.size).toBe(1);
    rerender(<SceneCanvas scene="neutron" label="펄사" animated={false} />);
    expect(pending.size).toBe(0); expect(disconnect).toHaveBeenCalledTimes(1);
    rerender(<SceneCanvas scene="ccc" label="우주 가설" animated />); expect(pending.size).toBe(1);
    unmount(); expect(pending.size).toBe(0); expect(disconnect).toHaveBeenCalledTimes(3);
  });
});
