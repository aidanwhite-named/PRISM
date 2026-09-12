import type { ReportContext } from "../lib/answers";

export default function AnswerContextView({context}: {context?: ReportContext | null}) {
  if (!context) return null;
  return <details className="answer-context no-print"><summary>
    {context.enabled ? `참고 판단 ${context.examples.length}건 · 문체 ${context.style.trim() ? "적용" : "없음"} · 추가 입력 약 ${context.estimated_tokens.toLocaleString()}토큰` : "정답 라이브러리 활용 안 함"}
  </summary>
    <p className="faint">실제로 입력에 포함한 기준입니다. 모델이 올바르게 적용했는지는 보고서의 근거와 함께 확인해 주세요.</p>
    {context.examples.map((e, i) => <div key={`${e.case_id}-${i}`}><strong>{e.title} · v{e.revision}</strong><p>{e.element} → {e.judgment}</p></div>)}
    {context.style && <pre>{context.style}</pre>}
  </details>;
}
