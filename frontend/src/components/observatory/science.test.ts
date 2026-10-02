import { describe, expect, it } from "vitest";
import { ELEMENTS } from "./elements";
import { cesiumTicks, CESIUM_FREQUENCY, nearestScale, photon, projectPoint } from "./science";

describe("과학 놀이터의 단위와 지도", () => {
  it("파장이 두 배가 되면 진동수와 광자 에너지가 절반으로 줄어든다", () => {
    const visible = photon(500), infrared = photon(1000);
    expect(visible.frequency).toBeCloseTo(599_584_916_000_000, -3);
    expect(visible.energy).toBeCloseTo(2.47968, 4);
    expect(infrared.frequency / visible.frequency).toBeCloseTo(.5, 10);
    expect(infrared.energy / visible.energy).toBeCloseTo(.5, 10);
    expect(photon(300).band).toBe("자외선"); expect(infrared.band).toBe("적외선");
  });
  it("정확한 1초 기준과 범위를 지킨다", () => {
    expect(cesiumTicks(.5)).toBe(4_596_315_885);
    expect(cesiumTicks(1)).toBe(CESIUM_FREQUENCY);
    expect(cesiumTicks(5)).toBe(CESIUM_FREQUENCY); expect(cesiumTicks(-1)).toBe(0);
  });
  it("앞뒤 깊이를 가진 점들이 회전하면 다른 위치에 투영된다", () => {
    expect(projectPoint([0, 0, 55], 0, 0).x).toBe(350);
    expect(projectPoint([0, 0, 55], 0, 90).x).toBeGreaterThan(350);
    expect(projectPoint([0, 0, -55], 0, 90).x).toBeLessThan(350);
    expect(projectPoint([0, 0, 55], 90, 0).y).toBeLessThan(190);
  });
  it("118개 원소가 빠짐없이 고유 위치와 개별 이야기·근거를 가진다", () => {
    expect(ELEMENTS).toHaveLength(118);
    expect(new Set(ELEMENTS.map(e => `${e.row}/${e.column}`)).size).toBe(118);
    expect(new Set(ELEMENTS.map(e => e.symbol)).size).toBe(118);
    for (const e of ELEMENTS) { expect(e.row).toBeGreaterThan(0); expect(e.column).toBeLessThanOrEqual(18); expect(e.story.length).toBeGreaterThan(30); expect(e.uses).toBeTruthy(); expect(e.joke).toBeTruthy(); expect(e.sources[0].url).toContain(`/element/${e.number}/`); }
    expect(ELEMENTS[46].symbol).toBe("Ag"); expect(ELEMENTS[54].symbol).toBe("Cs"); expect(ELEMENTS[117].symbol).toBe("Og");
    expect(ELEMENTS[112].family).toBe("주족 원소");
  });
  it("크기의 양끝을 관측 한계와 구분해서 설명한다", () => {
    expect(nearestScale(-18).caption).toContain("실제 크기를 안다는 뜻은 아닙니다");
    expect(nearestScale(27).caption).toContain("전체 우주의 크기를 뜻하지는 않습니다");
    expect(nearestScale(0).kind).toBe("human");
  });
});
