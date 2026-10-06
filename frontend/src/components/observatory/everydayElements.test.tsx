import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ELEMENTS } from "./elements";
import { EVERYDAY_ELEMENTS } from "./everydayElements";
import EverydayIllustration from "./EverydayIllustration";
import PeriodicRoom from "./PeriodicRoom";

afterEach(() => { cleanup(); localStorage.clear(); });

describe("생활 속 원소 1–54", () => {
  it("54개의 개별 이야기에는 물질 형태와 관찰 질문, 공식 원소 출처가 있다", () => {
    expect(EVERYDAY_ELEMENTS.map(e => e.number)).toEqual(Array.from({ length: 54 }, (_, i) => i + 1));
    expect(new Set(EVERYDAY_ELEMENTS.map(e => e.title)).size).toBe(54);
    for (const life of EVERYDAY_ELEMENTS) {
      for (const value of [life.title, life.caption, life.story, life.mechanism, life.lookFor, life.material]) expect(value.trim().length).toBeGreaterThan(5);
      expect(ELEMENTS[life.number - 1].sources.some(source => source.url === `https://periodic-table.rsc.org/element/${life.number}/`)).toBe(true);
    }
  });
  it("원소마다 설명과 연결되는 접근 가능한 그림과 서로 다른 도형을 렌더링한다", () => {
    const { container } = render(<>{EVERYDAY_ELEMENTS.map(life => <EverydayIllustration key={life.number} life={life} element={ELEMENTS[life.number - 1]} />)}</>);
    expect(screen.getAllByRole("img")).toHaveLength(54);
    const shapes = Array.from(container.querySelectorAll(".life-drawing"), drawing => {
      const copy = drawing.cloneNode(true) as Element;
      copy.querySelectorAll("text").forEach(label => label.remove());
      return copy.innerHTML;
    });
    expect(new Set(shapes).size).toBe(54);
    for (const life of EVERYDAY_ELEMENTS) expect(screen.getByRole("img", { name: `${life.number}번 ${ELEMENTS[life.number - 1].name} · ${life.caption} 설명 그림` })).toBeTruthy();
  });
  it("모든 원소를 순서대로 읽고 54번에서 수소로 돌아오며 이전 원소도 선택한다", () => {
    render(<PeriodicRoom navigate={() => {}} />);
    const sequence = within(screen.getByRole("navigation", { name: "생활 원소 순서" }));
    const story = within(screen.getByRole("article"));
    expect((sequence.getByRole("button", { name: /이전 원소/ }) as HTMLButtonElement).disabled).toBe(true);
    for (const life of EVERYDAY_ELEMENTS) {
      expect(story.getByRole("heading", { name: life.title })).toBeTruthy();
      expect(story.getByRole("img", { name: `${life.number}번 ${ELEMENTS[life.number - 1].name} · ${life.caption} 설명 그림` })).toBeTruthy();
      expect(story.getAllByRole("button", { name: /^\d+장 / })).toHaveLength([7, 23, 47].includes(life.number) ? 8 : 5);
      fireEvent.click(sequence.getByRole("button", { name: life.number === 54 ? /수소부터 다시/ : /다음 원소/ }));
    }
    expect(screen.getByRole("heading", { name: EVERYDAY_ELEMENTS[0].title })).toBeTruthy();
    fireEvent.click(sequence.getByRole("button", { name: /다음 원소/ }));
    fireEvent.click(sequence.getByRole("button", { name: /이전 원소/ }));
    expect(screen.getByRole("heading", { name: EVERYDAY_ELEMENTS[0].title })).toBeTruthy();
  });
  it("생활 단어와 익숙한 원소 별칭으로 찾고 기존 55번 이후 이야기도 읽는다", () => {
    render(<PeriodicRoom navigate={() => {}} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "터치 화면" } });
    fireEvent.click(within(screen.getByRole("region", { name: "원소 검색 결과" })).getByRole("button", { name: "In · 인듐" }));
    expect(screen.getByRole("heading", { name: EVERYDAY_ELEMENTS[48].title })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /2장 왜 이 원소/ }));
    expect(screen.getByText(EVERYDAY_ELEMENTS[48].mechanism)).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "나트륨" } });
    expect(within(screen.getByRole("region", { name: "원소 검색 결과" })).getAllByRole("button")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Na · 소듐" }));
    expect(screen.getByRole("heading", { name: EVERYDAY_ELEMENTS[10].title })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "55 Cs 세슘" }));
    expect(screen.queryByRole("navigation", { name: "생활 원소 순서" })).toBeNull();
    expect(screen.getByRole("button", { name: /이 원소로 1초/ })).toBeTruthy();
  });
});
