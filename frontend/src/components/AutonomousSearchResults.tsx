import type { ProgressiveSearchSnapshot } from "../lib/types";

function safeLink(raw: string): string | undefined {
  try {
    const url = new URL(raw);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? raw : undefined;
  } catch { return undefined; }
}

export default function AutonomousResults({ data }: { data: ProgressiveSearchSnapshot }) {
  const candidates = data.candidates.filter(c => c.date_status !== "after_cutoff");
  return <div className="search-results">
    <header className="card">
      <h2>{data.phase === "complete" ? "검색 종료" : "검색·문헌 확인 중"} · 문헌 {candidates.length}건</h2>
      <p>{data.elapsed_seconds.toFixed(1)}초 경과 · AI가 정한 관련성 순서</p>
      {["deadline", "deadline_reserve"].includes(data.stop_reason) && <p>설정한 시간에 도달했습니다. 확보한 문헌과 출처를 보존했습니다.</p>}
      {data.stop_reason === "cancelled" && <p>검색을 중단했습니다. 중단 전 저장한 문헌과 출처를 보존했습니다.</p>}
    </header>
    {candidates.map((c, i) => <section key={c.id} className="card search-result-candidate">
      <h3>{i + 1}. {c.title || c.document_number}</h3>
      <p>{c.document_number} · 공개일 {c.publication_date || "미확인"}</p>
      {safeLink(c.url) && <a href={c.url} target="_blank" rel="noreferrer">문헌 보기</a>}
      <p style={{ whiteSpace: "pre-wrap" }}>{c.reason}</p>
      {c.difference && <p>남은 차이·확인 사항: {c.difference}</p>}
      <p>선별 상태: {c.triage_status || "unreviewed"} · 검토 단계: {c.review_stage || "metadata"}</p>
      {c.triage_reason && <p>선별 근거: {c.triage_reason}</p>}
      {c.core_matches && <p>확인된 핵심 구성 관계: {c.core_matches}</p>}
      {c.observed_scope && <p>프로그램이 확보한 자료: {c.observed_scope}</p>}
      <details><summary>AI가 보고한 확인 범위(작성 당시)</summary>
        <p>LLM이 보고한 확인 범위: {c.reported_scope || "미기재"}</p>
      </details>
      {!!c.source_receipts?.length && <details><summary>보존된 출처 응답</summary><ul>
        {c.source_receipts.map((r, n) => <li key={`${r.call_id}-${n}`}>{r.tool} · {r.scope}</li>)}
      </ul></details>}
    </section>)}
    {data.summary && <section className="card"><h3>검색 설명</h3>
      <p>아래는 AI가 작성한 설명입니다. 자료 확보 범위는 위 문헌별 출처 응답 기록을 기준으로 확인하세요.</p>
      <p style={{ whiteSpace: "pre-wrap" }}>{data.summary}</p></section>}
    {!candidates.length && <p>아직 저장된 문헌 후보가 없습니다.</p>}
    <details className="card"><summary>검색 기록</summary>
      {data.queries.map(q => <p key={q.id}>{q.source} · {q.query} · {q.error || q.status}</p>)}
    </details>
    {data.warnings.map((warning, i) => <p className="notice" key={i}>{warning}</p>)}
  </div>;
}

