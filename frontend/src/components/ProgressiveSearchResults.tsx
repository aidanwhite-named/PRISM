import { useState } from "react";
import type { ProgressiveSearchSnapshot } from "../lib/types";
import { categoryOrder, documentCategory } from "../lib/searchCategories";

const MATCH: Record<string, string> = {
  explicit: "명시적 대응", semantic: "의미상 대응", partial: "부분 대응",
  absent: "검토 구간에 대응 없음", unknown: "미확인",
};
const DATA: Record<string, string> = {
  FULL_TEXT: "전문 확보", CLAIMS_ONLY: "청구항 확보", ABSTRACT_ONLY: "초록 확보",
  METADATA_ONLY: "서지·검색 단서", PARTIAL_TEXT: "일부 본문 확보",
};
const PHASE: Record<string, string> = {
  relation_seed: "관계 중심 후보 검색 중",
  citations: "인용·피인용 검색 중",
  planning: "검색 구성 정리 중", fast: "후보 검색 중", deep: "추가 검색 중",
  exhaustive: "확장 검색 중", verification: "원문 근거 확인 중", complete: "검색 종료",
};
function safeLink(raw: string): string | undefined {
  try {
    const url = new URL(raw);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? raw : undefined;
  } catch { return undefined; }
}

export default function ProgressiveSearchResults({ data }: { data: ProgressiveSearchSnapshot }) {
  const [limit, setLimit] = useState(20);
  const [unclassifiedLimit, setUnclassifiedLimit] = useState(20);
  const eligible = data.candidates.filter(candidate => candidate.date_status !== "after_cutoff")
    .sort((a, b) => categoryOrder(a.document_classification?.group) - categoryOrder(b.document_classification?.group));
  const families = new Map<string, typeof eligible>();
  for (const candidate of eligible) {
    const key = candidate.family_id || candidate.id;
    families.set(key, [...(families.get(key) || []), candidate]);
  }
  const groups = [...families.values()];
  const classified = groups.filter(members => documentCategory(members[0].document_classification?.group));
  const unclassified = groups.filter(members => !documentCategory(members[0].document_classification?.group));
  const renderCandidate = (members: typeof eligible, index: number) => {
      const candidate = members[0];
      const url = safeLink(candidate.url);
      return <section key={candidate.id} className="card search-result-candidate">
        <h3>{index + 1}. {candidate.title || candidate.document_number}</h3>
        <p><strong>문헌 분류 {documentCategory(candidate.document_classification?.group) || "미분류"}</strong>
          {" · "}{candidate.document_classification?.reason || "아직 평가하지 않은 후보"}</p>
        <p>{candidate.document_number} · 공개일 {candidate.publication_date || "미확인"} · {DATA[candidate.data_status] || candidate.data_status}</p>
        {url && <a href={url} target="_blank" rel="noreferrer">문헌 보기</a>}
        {members.length > 1 && <details><summary>같은 family {members.length}개 문헌</summary><ul>
          {members.map(member => <li key={member.id}>{member.document_number} · {member.publication_date || "공개일 미확인"}
            {safeLink(member.url) && <> · <a href={member.url} target="_blank" rel="noreferrer">원문</a></>}</li>)}
        </ul><p>아래 근거는 대표 문헌의 내용입니다. 다른 family 문헌의 동일한 기재를 보장하지 않습니다.</p></details>}
        <div className="table-scroll"><table className="search-result-mapping">
          <thead><tr><th>구성</th><th>대응</th><th>관계·차이</th><th>확인한 원문</th></tr></thead>
          <tbody>{data.features.map(feature => {
            const evidence = candidate.evidence.find(row => row.feature === feature.id);
            return <tr key={feature.id}><th>{feature.id}</th>
              <td>{evidence ? MATCH[evidence.match] || "미확인" : "미검증"}</td>
              <td>{evidence?.relation}{evidence?.difference && <p>{evidence.difference}</p>}</td>
              <td>{evidence?.quote_verified ? <><blockquote>{evidence.quote}</blockquote>
                <p>{evidence.locator?.page ? `PDF ${evidence.locator.page}쪽` : evidence.locator?.section}
                  {evidence.locator?.url && safeLink(evidence.locator.url) && <> · <a href={evidence.locator.url} target="_blank" rel="noreferrer">근거 출처</a></>}</p></>
                : "확인된 인용문 없음"}</td></tr>;
          })}</tbody>
        </table></div>
        {candidate.acquisitions.some(row => row.status === "failed") && <details><summary>원문 확보 제약</summary>
          <ul>{candidate.acquisitions.filter(row => row.status === "failed").map((row, i) => <li key={i}>{row.scope || "원문"}: {row.error}</li>)}</ul>
        </details>}
      </section>;
  };

  return <div className="search-results">
    <header className="card">
      <h2>{PHASE[data.phase] || data.phase} · 후보 {eligible.length}건 / {groups.length}개 문헌군</h2>
      <p>{data.elapsed_seconds.toFixed(1)}초 경과{data.first_candidate_seconds != null &&
        ` · 첫 후보 ${data.first_candidate_seconds.toFixed(1)}초`}</p>
      <p>관련성은 아래 구성별 원문 근거로 확인하세요. 미확인 항목은 문헌에 없다는 뜻이 아니며, 검색 누락이 없음을 보장하지 않습니다.</p>
      <p>원문 인용을 대조한 후보: {eligible.filter(c => c.evidence.some(e => e.quote_verified)).length}건</p>
      {data.route?.map((step, index) => <p key={index}>
        {step.lane === "continuation" ? "이전 후보·근거를 이어받아 정밀 검색" : step.lane === "relation_seed" ? "관계 중심 검색" : step.lane === "citations" ? "인용·피인용 검색" : "기존 특허·논문 검색"}
        {step.outcome === "verified_x" ? " · 구성별 원문 근거가 있는 X 후보 확인" :
          step.outcome === "candidates_merged" ? " · 발견한 후보를 합쳐 원문 검증 대상으로 전달" :
          step.outcome === "skipped" ? (step.reason === "no_supported_seed" ? " · 조회할 관련 후보 미확보" : " · 남은 예산 또는 검색어 부족으로 생략") :
          step.outcome === "no_verified_x" ? " · X 미확인" : ""}
        {step.reason === "no_verified_x" && " · X 미확인으로 후속 검색 진행"}
        {step.outcome === "verified_xy" && " · 원문 근거가 있는 X·Y 후보 확인"}
        {(step.outcome === "no_verified_xy" || step.reason === "no_verified_xy") && " · X·Y 미확인으로 후속 검색 진행"}
        {step.seconds != null && ` · ${step.seconds.toFixed(1)}초`}
      </p>)}
      <dl className="search-category-legend" aria-label="문헌 분류 안내">
        <div><dt>문헌 분류 X</dt><dd>전체 구조·핵심 특징 유사</dd></div>
        <div><dt>문헌 분류 Y</dt><dd>구조는 다르나 핵심 관계 유사</dd></div>
        <div><dt>문헌 분류 Z</dt><dd>구조는 유사하나 핵심 대응 부분적</dd></div>
      </dl>
      {data.warnings.length > 0 && <p className="notice">일부 검색·검증 단계가 완료되지 않았습니다. 확보한 후보는 보존했으며, 아래 검색 기록에 원인을 표시했습니다.</p>}
      {data.stop_reason === "cancelled" && <p>검색을 중단했습니다. 중단 전 확보한 후보와 근거를 보존했습니다.</p>}
      {data.stop_reason === "deadline_reserve" && <p>검색 시간에 도달했습니다. 추가 확인이 필요한 후보도 보존했습니다.</p>}
    </header>
    {classified.slice(0, limit).map(renderCandidate)}
    {classified.length > limit && <button onClick={() => setLimit(limit + 20)}>후보 더 보기</button>}
    {unclassified.length > 0 && <details className="card search-unclassified">
      <summary>미분류 문헌 · {unclassified.length}개 문헌군</summary>
      <div className="search-unclassified-content">
        {unclassified.slice(0, unclassifiedLimit).map((members, index) => renderCandidate(members, classified.length + index))}
        {unclassified.length > unclassifiedLimit && <button onClick={() => setUnclassifiedLimit(unclassifiedLimit + 20)}>미분류 더 보기</button>}
      </div>
    </details>}
    {eligible.length === 0 && <p>아직 반환된 후보가 없습니다. 검색 진행 상태와 아래 조회 기록을 확인하세요.</p>}
    <details className="card"><summary>검색 기록 · {data.queries.length}개 질의</summary>
      <div className="table-scroll"><table><thead><tr><th>단계</th><th>출처</th><th>검색식·조회 요청</th><th>결과</th></tr></thead>
        <tbody>{data.queries.map(query => <tr key={query.id}><td>{query.lane ? PHASE[query.lane] || query.lane : "기존 기록"}</td><td>{query.source}</td><td>{query.query}</td>
          <td>{query.error || `${query.hits ?? ""} ${query.status}`}</td></tr>)}</tbody></table></div>
      <ul>{data.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>
    </details>
  </div>;
}
