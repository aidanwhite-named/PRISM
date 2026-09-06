import { describe, expect, it } from "vitest";

import { renderMarkdown, renderReportMarkdown } from "./markdown";

function fragment(html: string) {
  const template = document.createElement("template");
  template.innerHTML = html;
  return template.content;
}

describe("보고서 가독성", () => {
  it("문장 전체의 코드 서식을 제목과 항목으로 바꾸되 원문을 보존한다", () => {
    const source = [
      "### 청구항 13", "`(A) 공간 서술 세그먼트를 생성하는 것`",
      "`대응 정도: 실질 대응 (90%)`", "`근거: 좌표 P=[X,Y,Z,1]T 및 $\\{I_i\\}_{i=1}^N$`",
      "`→ 차이점: 일부 상이합니다.`",
    ].join("\n\n");
    const result = fragment(renderReportMarkdown(source));
    expect(result.querySelector("h4")?.textContent).toBe("(A) 공간 서술 세그먼트를 생성하는 것");
    expect(result.querySelector(".report-degree")?.textContent).toBe("대응 정도: 실질 대응 (90%)");
    expect(result.querySelectorAll("code")).toHaveLength(0);
    expect(result.querySelectorAll("p > strong")).toHaveLength(3);
    expect(result.textContent).toBe(fragment(renderMarkdown(source)).textContent);
  });

  it("일반 본문에도 같은 구분을 적용하고 기존 강조·링크·인라인 코드·인용은 유지한다", () => {
    const source = "(A) 구성 내용\n\n**근거:** [문헌](https://example.com) 및 `x_y`\n\n> `(A) 인용문`\n\n```python\nx = 1\n```";
    const result = fragment(renderReportMarkdown(source));
    expect(result.querySelector("h4")).not.toBeNull();
    expect(result.querySelectorAll("p.report-field strong")).toHaveLength(1);
    expect(result.querySelector("a")?.getAttribute("href")).toBe("https://example.com");
    expect(result.querySelector("p.report-field code")?.textContent).toBe("x_y");
    expect(result.querySelector("blockquote code")?.textContent).toBe("(A) 인용문");
    expect(result.querySelector("pre code")?.textContent).toBe("x = 1\n");
  });

  it("코드 속 HTML을 실행 가능한 마크업으로 바꾸지 않는다", () => {
    const result = fragment(renderReportMarkdown('`근거: <img src=x onerror=alert(1)> <script>alert(1)</script>`\n\n<script>alert(2)</script>'));
    expect(result.querySelector("img, script")).toBeNull();
    expect(result.querySelector("p")?.textContent).toContain("<img src=x onerror=alert(1)>");
  });
});
