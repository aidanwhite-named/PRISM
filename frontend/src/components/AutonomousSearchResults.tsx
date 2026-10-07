import type { ProgressiveSearchSnapshot } from "../lib/types";
import { Fragment } from "react";
import { visibleSearchCandidates, isPromisingCandidate } from "../lib/searchCandidates";
import { componentReviewLabel, componentReviewState } from "../lib/searchComponentReview";

function safeLink(raw: string): string | undefined {
  try {
    const url = new URL(raw);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? raw : undefined;
  } catch { return undefined; }
}

export default function AutonomousResults({ data }: { data: ProgressiveSearchSnapshot }) {
  const gap = data.search_focus?.mode === "gap";
  const visible = visibleSearchCandidates(data);
  const promising = gap ? [] : visible.filter(isPromisingCandidate);
  const classified = (c: typeof visible[number]) => c.search_review?.status === "source_checked" && !!c.search_review.group;
  const candidates = promising.length ? [
    ...visible.filter(classified), ...promising,
    ...visible.filter(c => !classified(c) && !isPromisingCandidate(c)),
  ] : visible;
  const dependent = data.search_focus?.origin === "dependent_claims";
  const targets = gap ? data.search_focus!.components : [];
  const groups = { X: 0, Y: 0, Z: 0 };
  let pending = 0, unavailable = 0;
  for (const c of candidates) {
    const review = c.search_review;
    if (gap) {
      const matches = targets.map(target => review?.component_matches?.find(m => m.component_id === target.id));
      if (matches.some(m => !m || !["source_checked", "unavailable"].includes(m.status))) pending += 1;
      else if (matches.every(m => m?.status === "unavailable")) unavailable += 1;
      continue;
    }
    if (review?.status === "source_checked" && review.group) groups[review.group] += 1;
    else if (review?.status === "unavailable") unavailable += 1;
    else if (review?.status !== "source_checked" && c.triage_status !== "rejected") pending += 1;
  }
  return <div className="search-results">
    <header className="card">
      <h2>{data.phase === "complete" ? "검색 종료" : data.phase === "verifying" ? "선별 후보 원문 검증 중" : "유사 문헌 검색·원문 확인·분류 중"} · 문헌 {candidates.length}건</h2>
      <p>{data.elapsed_seconds.toFixed(1)}초 경과 · 원문 근거가 확인된 유사 후보 우선</p>
      {!gap && <p>원문 근거 검증을 통과한 분류: X {groups.X}건, Y {groups.Y}건, Z {groups.Z}건</p>}
      {gap && <>
        <p>{dependent ? "종속항만 따로 검색 · 추가 특징의 대응 정도와 원문 근거를 표시합니다. 종속항 전체의 대응 여부와는 구분합니다."
          : "선택한 미대응 구성별로 대응 정도와 원문 근거를 표시합니다."}</p>
        {dependent && data.search_focus?.target_source === "dependent_claim" && <p>별도 검색 대상 문장을 지정하지 않아 종속항 원문에서 AI가 추가 특징을 파악해 검색합니다.</p>}
        {targets.map(target => {
          const matches = candidates.map(c => c.search_review?.component_matches?.find(m => m.component_id === target.id));
          const strong = matches.filter(m => m?.status === "source_checked" && m.verdict === "strong").length;
          const partial = matches.filter(m => m?.status === "source_checked" && m.verdict === "partial").length;
          const unreviewed = matches.filter(m => componentReviewState(m) === "unreviewed").length;
          const checkedPending = matches.filter(m => componentReviewState(m) === "reviewed" && m?.status !== "source_checked").length;
          const absent = matches.filter(m => m?.status === "source_checked" && ["not_found", "mismatch"].includes(m.verdict || "")).length;
          const inaccessible = matches.filter(m => m?.status === "unavailable").length;
          return <p key={target.id}><strong>{target.symbol || target.id}</strong> · 강한 대응 {strong}건 · 부분 대응 {partial}건 · 대응 근거 없음·비대응 {absent}건 · 미검토 {unreviewed}건 · 검토함·검증 보완 {checkedPending}건 · 검토 불가 {inaccessible}건<br />{target.feature}</p>;
        })}
      </>}
      <p>근거 확인 미완료 {pending}건 · 원문 확인 불가 {unavailable}건</p>
      {!!promising.length && <p><strong>유력 후보 · 분류 보류 {promising.length}건</strong> · 초기 자료에서 전체 구조와 핵심 관계가 매우 가까워 우선 확인이 필요한 문헌입니다. X/Y/Z 분류는 아직 확정되지 않았습니다.</p>}
      {!!data.excluded_input_documents?.length && <p>입력과 동일한 문헌 {data.excluded_input_documents.length}건은 후보에서 제외하고 추가 검증을 생략했습니다.</p>}
      {data.limits?.search_seconds != null && <p>후보 탐색 최대 {data.limits.search_seconds}초 · 원문 검증 최대 {data.limits.verification_seconds}초</p>}
      {data.limits && data.limits.search_seconds == null && <p>전체 검색 최대 {data.limits.seconds}초 · 탐색·후보 검토·종료는 AI가 판단</p>}
      {data.phase === "complete" && data.stop_reason === "model_complete" && <p>검색과 후보 검토를 마무리했습니다. 미확인 사항은 해당 상태로 보존했습니다.</p>}
      {data.phase === "complete" && data.stop_reason === "model_complete" && !candidates.length && <p>이번 검색 범위에서 제시할 유사 문헌을 확보하지 못했습니다. 유사 문헌이 존재하지 않는다는 뜻은 아닙니다.</p>}
      {!gap && data.stop_reason === "x_found" && <p>원문 근거가 확인된 X 후보를 확보해 종료했습니다. 나머지 후보의 추가 검증은 생략했습니다.</p>}
      {["deadline", "deadline_reserve"].includes(data.stop_reason) && <p>설정한 시간에 도달했습니다. 확보한 문헌과 출처를 보존했습니다.</p>}
      {data.stop_reason === "cancelled" && <p>검색을 중단했습니다. 중단 전 저장한 문헌과 출처를 보존했습니다.</p>}
      {data.stop_reason === "component_review_incomplete" && <p>선택 구성의 검토를 모두 마치지 못했습니다. 구성별 상태와 사유를 확인하세요.</p>}
    </header>
    {candidates.map((c, i) => <Fragment key={c.id}>
      {!!promising.length && c === promising[0] && <h2>유력 후보 · 분류 보류</h2>}
      {!!promising.length && !classified(c) && !isPromisingCandidate(c)
        && (i === 0 || classified(candidates[i - 1]) || isPromisingCandidate(candidates[i - 1])) && <h2>기타 미분류 후보</h2>}
      <section className="card search-result-candidate">
      <h3>{i + 1}. {!gap && (c.search_review?.status === "source_checked" && c.search_review.group
        ? `[${c.search_review.group}] ` : isPromisingCandidate(c) ? "[유력 후보 · 분류 보류] " : "[미분류] ")}{c.title || c.document_number}</h3>
      {!gap && isPromisingCandidate(c) && <p><strong>초기 자료 기준의 근접 후보 · 본문 검증 미완료</strong></p>}
      {!gap && c.search_review?.status === "source_checked" && c.search_review.group && <p><strong>
        {c.search_review.group} · {({ X: "전체 구조와 핵심 특징이 모두 강하게 유사", Y: "전체 구조는 다르지만 핵심 특징 또는 핵심 관계가 강하게 유사", Z: "전체 구조는 유사하지만 핵심 대응은 부분적" })[c.search_review.group]}
      </strong></p>}
      <p>{c.document_number} · 공개일 {c.publication_date || "미확인"}</p>
      {safeLink(c.url) && <a href={c.url} target="_blank" rel="noreferrer">문헌 보기</a>}
      <p style={{ whiteSpace: "pre-wrap" }}>{!gap && c.search_review?.status === "needs_review" && "AI 잠정 판단 (원문 근거 검증 미완료): "}{c.search_review?.reason || c.reason}</p>
      {(c.search_review?.gaps || c.difference) && <p>남은 차이·확인 사항: {c.search_review?.gaps || c.difference}</p>}
      <p>잠정 선별: {({ unreviewed: "미검토", candidate: "우선 검토", promising: "유력 후보 · 분류 보류", hold: "자료 부족", rejected: "관련성 낮음", detailed: "본문 검토 후보" })[c.triage_status || "unreviewed"]}
        {" · "}검색 단계 확인 범위: {({ metadata: "서지·검색 결과", core_components: "일부 구성", full_text: "본문" })[c.review_stage || "metadata"]} (AI 보고)</p>
      {c.triage_reason && <p>선별 근거: {c.triage_reason}</p>}
      {c.core_matches && <p>확인된 핵심 구성 관계: {c.core_matches}</p>}
      {gap && <div className="search-component-matches">
        {targets.map(target => {
          const match = c.search_review?.component_matches?.find(m => m.component_id === target.id);
          const degree = componentReviewLabel(match);
          return <section key={target.id}>
            <h4>{target.symbol || target.id} · {degree}</h4>
            <p>{target.feature}</p>
            <p>{match?.reason || "이 구성은 아직 검토하지 않았습니다."}</p>
            {match?.gaps && <p>남은 차이·확인 사항: {match.gaps}</p>}
            {!!match?.issues?.length && <p>확인 미완료 사유: {[...new Set(match.issues)].join(" ")}</p>}
            {match?.reviewed_sources?.map((source, n) => <p key={n}>검토 범위: {source.scope} · 문자 {source.start}–{source.end}</p>)}
            {!!match?.passages?.length && <details><summary>이 구성의 원문 근거</summary>
              {match.passages.map((p, n) => <div key={n}>
                <p>{p.relation}</p><blockquote>{p.quote}</blockquote><p>{p.translation}</p>
                <p>{p.pages.length ? `PDF ${p.pages.join(", ")}쪽` : `${p.scope} · 문자 ${p.start}–${p.end}`}</p>
                {safeLink(p.url) && <a href={p.url} target="_blank" rel="noreferrer">원문 출처</a>}
              </div>)}
            </details>}
          </section>;
        })}
      </div>}
      {!gap && <p>검색 중 원문 대조: {c.search_review?.status === "source_checked"
        ? ({ strong: "강한 유사성", partial: "일부 핵심 대응", mismatch: "기대한 대응과 다름", unavailable: "원문 미확인" })[c.search_review.verdict || "unavailable"] + " (AI 판단 · 발췌 일치 확인)"
        : c.search_review?.status === "unavailable" ? "원문 확보·확인 불가" : "근거 확인 미완료"}</p>}
      {!!c.search_review?.issues?.length && <p>검증 미완료 사유: {[...new Set(c.search_review.issues)].join(" ")}</p>}
        {!c.search_review?.issues?.length && c.review_pending_reason && <p>분류 미완료 사유: {c.review_pending_reason}</p>}
      {!!c.search_review?.passages?.length && <details><summary>검색 중 확인한 원문 근거</summary>
        {c.search_review.passages.map((p, n) => <div key={n}>
          <p><strong>{p.feature}</strong> · {p.relation}</p>
          <blockquote>{p.quote}</blockquote><p>{p.translation}</p>
          <p>{p.pages.length ? `PDF ${p.pages.join(", ")}쪽` : `${p.scope} · 문자 ${p.start}–${p.end}`}</p>
        </div>)}
      </details>}
      {c.observed_scope && <p>프로그램이 확보한 자료: {c.observed_scope}</p>}
      <details><summary>AI가 보고한 확인 범위(작성 당시)</summary>
        <p>LLM이 보고한 확인 범위: {c.reported_scope || "미기재"}</p>
      </details>
      {!!c.source_receipts?.length && <details><summary>보존된 출처 응답</summary><ul>
        {c.source_receipts.map((r, n) => <li key={`${r.call_id}-${n}`}>{r.tool} · {r.scope}</li>)}
      </ul></details>}
    </section></Fragment>)}
    {!candidates.length && <p>아직 저장된 문헌 후보가 없습니다.</p>}
    <details className="card"><summary>검색 기록</summary>
      {data.queries.map(q => <p key={q.id}>{q.source} · {q.query} · {q.error || q.status}</p>)}
    </details>
    {data.warnings.map((warning, i) => <p className="notice" key={i}>{warning}</p>)}
  </div>;
}

