import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { RunSessionProvider } from "../lib/runSession";
import WorkspaceEntry from "./WorkspaceEntry";

function Destination() {
  const location = useLocation();
  return <output>{location.pathname}{location.search}</output>;
}

function open(path: string, resume = false) {
  sessionStorage.setItem("prism.run-session.v1", JSON.stringify({ jobKind: "patent_analysis" }));
  render(<RunSessionProvider><MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
    <Routes><Route path="/" element={<WorkspaceEntry resume={resume} />} />
      <Route path="/search" element={<Destination />} /><Route path="/analysis" element={<Destination />} /></Routes>
  </MemoryRouter></RunSessionProvider>);
}

afterEach(() => { cleanup(); sessionStorage.clear(); });

describe("시작 화면", () => {
  it("마지막 작업이 분석이어도 유사검색으로 시작한다", () => {
    open("/");
    expect(screen.getByRole("status").textContent).toBe("/search");
  });
  it("실행 기록에서 이어 온 주소의 작업 ID를 보존한다", () => {
    open("/?job=previous-job");
    expect(screen.getByRole("status").textContent).toBe("/search?job=previous-job");
  });
  it("예전 실행 링크는 저장된 작업으로 연결한다", () => {
    open("/?job=previous-job", true);
    expect(screen.getByRole("status").textContent).toBe("/analysis?job=previous-job");
  });
});
