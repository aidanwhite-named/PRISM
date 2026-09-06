/** Read-only audit. Technical groups never depend on evidence levels. */
import type { Job, SearchManifestV14 } from "../lib/types";

export function linkableUrl(raw?: string): string | null {
  const text = (raw ?? "").trim();
  if (!text || /[\s<>"\u0000-\u001f]/.test(text)) return null;
  try {
    const url = new URL(text);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? text : null;
  } catch { return null; }
}
const LEVELS: Record<string, string> = {
  search_snippet_only: "검색 스니펫·모델 판단 / 미검증",
  source_page_reviewed: "페이지 열람 확인 / 인용 미검증",
  official_bibliographic: "공식 서지 확보", official_abstract: "공식 초록 확보",
  official_claims: "공식 청구항 확보", official_full_text: "공식 전문 확보",
};
const ISSUES: Record<string, string> = {
  identifier_unverified: "식별 미확인", identifier_invalid: "식별자 형식 오류",
  identifier_mismatch: "문헌 식별자 불일치", source_not_read: "본문 열람 미확인",
  quote_unverified: "직접 인용 검증 불가", support_unverified: "근거 문장 대조 미확인",
  duplicate_group_conflict: "중복 후보 그룹 충돌", publication_date_conflict: "공개일 출처 충돌",
  source_conflict: "출처 간 필드 내용 차이",
  publication_date_unverified: "공개일 대조 미확인",
  title_unverified: "명칭 대조 미확인", title_mismatch: "보고 명칭과 보존 원문 명칭 차이",
  applicant_unverified: "저자·출원인 대조 미확인", applicant_mismatch: "보고 저자·출원인과 보존 원문 차이",
};
const REASONS: Record<string, string> = {
  unsupported_transport: "실행별 도구 연결 미지원", not_implemented: "접속 미구현",
  disabled: "연동 꺼짐", not_configured: "인증 미설정", limit_exhausted: "호출·쿼터 한도 소진",
  access_failed: "조회 실패", timeout: "시간 한도 소진", rate_limited: "Provider 사용량 제한",
  cancelled: "취소됨", outcome_unknown: "호출 완료 여부 미확인",
  page_read_without_provenance: "페이지 열람 성공 · 보존 근거 대조 경로 없음",
};
export function SearchResults({ data }: { data: SearchManifestV14 }) {
  const candidates = data.reported?.candidates ?? [];
  const groups = ["A", "B", "C", null] as const;
  return <div className="search-results">
    <nav className="search-group-nav" aria-label="문헌 그룹">
      {groups.filter(group => group || candidates.some(item => item.group === null)).map(group =>
        <a key={group ?? "none"} href={`#search-group-${group ?? "none"}`}>
          <strong>LLM {group ?? "미분류"}</strong><span>{candidates.filter(item => item.group === group).length}건</span>
          <small>{data.group_definitions[group ?? ""] || "분류되지 않은 참고 후보"}</small>
        </a>)}
    </nav>
    <p>A/B/C는 LLM의 기술적 판단입니다. 증거 확보 수준은 별도로 표시합니다.</p>
    {data.error && <p role="alert">미완료: {data.error}</p>}
    {data.status === "verification_incomplete" && <p role="alert">검색 실행 종료 · 검증 미완료</p>}
    {data.quality && <>
      <p>근거 검증 후보: {data.quality.verified_candidate_count}/{data.quality.candidate_count}건. 검색의 충분성·누락 없음은 보증하지 않습니다.</p>
      {candidates.some(item => item.evidence_level === "source_page_reviewed" && !item.evidence_sources.length) &&
        <p>페이지 열람은 성공했지만, 웹 열람 내용을 보존 근거와 자동 대조하는 연결이 없어 미검증으로 남은 후보가 있습니다. 이 표시는 조회 실패나 예산 소진을 뜻하지 않습니다.</p>}
      <details><summary>검증 미완료 사유 및 검색 제약</summary>
      <ul>{data.quality.outstanding.map(item => <li key={item.identity}>{item.identity}: {REASONS[item.reason] || (item.reason === "not_attempted" ? "원문 조회 미시도" : "조회 후 검증 미해결")} · 대응 근거 미검증 {item.unverified_mapping_count}개</li>)}</ul>
      <ul>{data.quality.constraints.map((item, index) => <li key={index}>{item.source}: {REASONS[item.reason] || item.reason} {item.detail}</li>)}</ul></details>
      {data.verification_followup && <p>추가 확인: {data.verification_followup.reason}</p>}
    </>}
    <details><summary>검색 도구 및 기준일</summary>
    <ul>{Object.entries(data.tool_availability).map(([name, value]) =>
      <li key={name}>{name}: {value.detail}</li>)}</ul>
    <p>검색 기준일: {data.date_filter.cutoff || "없음"} · 공개일 불명: {data.date_filter.unknown_publication_date || 0}건</p>
    </details>
    {groups.filter(group => group || candidates.some(item => item.group === null)).map(group => <section key={group ?? "none"} id={`search-group-${group ?? "none"}`} className="search-result-group">
      <header><h2>LLM 그룹 {group ?? "미분류"} <span>{candidates.filter(item => item.group === group).length}건</span></h2>
      <p>{data.group_definitions[group ?? ""] || "분류되지 않은 참고 후보"}</p></header>
      {!candidates.some(item => item.group === group) && <p className="faint">이 그룹의 후보가 없습니다.</p>}
    {candidates.filter(item => item.group === group).map((item) => {
      const url = linkableUrl(item.url);
      return <section key={item.index} className="card search-result-candidate">
        <h3>{item.rank}. {item.title || item.doc_number || item.doi}</h3>
        <p>{item.doc_number || item.doi} · {item.applicant || "저자·출원인 미확인"}</p>
        {item.verified_titles?.length ? <p>보존 원문 명칭: {item.verified_titles.join(" / ")}</p> : null}
        {item.verified_applicants?.length ? <p>보존 원문 저자·출원인: {item.verified_applicants.join(" / ")}</p> : null}
        {url ? <a href={url} target="_blank" rel="noreferrer">문헌 보기</a> : <span>링크 미확인</span>}
        <p>{LEVELS[item.evidence_level]}</p><p>{item.note}</p>
        {item.verification_issues.length > 0 && <p>{item.verification_issues.map(x => ISSUES[x]).join(" / ")}</p>}
        <div className="table-scroll"><table className="search-result-mapping">
          <thead><tr><th>청구항 구성</th><th>대응 판단</th><th>유사점 / 차이점</th><th>근거 확인</th></tr></thead>
          <tbody>{item.mapping.map((row, index) => <tr key={index}>
            <td>{row.feature}</td><td>{row.counterpart || row.degree}</td>
            <td><p>{row.similar}</p><p>차이: {row.different || "미기재"}</p></td>
            <td><strong>{row.support_verified ? "보존 응답과 일치" : "근거 문장 자동 대조 미완료"}</strong><p>{row.support_text}</p>
              {row.quote_verified && row.verbatim_excerpt && <blockquote>{row.verbatim_excerpt}<p>{row.translation}</p><small>{row.source_location}</small></blockquote>}
            </td>
          </tr>)}</tbody>
        </table></div>
        <details><summary>근거 범위 상세</summary>
          <pre>{JSON.stringify({ scope: item.verification_scope, mapping: item.mapping }, null, 2)}</pre>
        </details>
      </section>;
    })}</section>)}
    {data.date_filter.excluded.length > 0 && <details><summary>기준일 이후 공개로 제외된 문헌</summary>
      <pre>{JSON.stringify(data.date_filter.excluded, null, 2)}</pre></details>}
  </div>;
}
export default function SearchManifestView({ job, auditOnly = false }: { job: Job; auditOnly?: boolean }) {
  const data = job.search_manifest;
  if (!data) return job.search_manifest_error ? <p role="alert">{job.search_manifest_error}</p> : null;
  return <details className="card search-manifest"><summary>검색 감사 기록</summary>
    {data.version === 14 ? <>
      {!auditOnly && <SearchResults data={data} />}
      <details><summary>실제 도구 호출·검색어 (PRISM 관측)</summary>
        <pre>{JSON.stringify({ observed: data.observed, journal: data.tool_journal }, null, 2)}</pre>
      </details>
      <details><summary>LLM 원출력 (미검증)</summary><pre>{JSON.stringify(data.llm_output, null, 2)}</pre></details>
    </> : <>
      <p>이전 형식(v{data.version})의 저장 기록입니다. 재분류·재검증하지 않았습니다. 저장된 보고서를 함께 확인하십시오.</p>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </>}
  </details>;
}
