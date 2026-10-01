import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { searchCandidateLabel, visibleSearchCandidates } from "../lib/searchCandidates";
import type { Job, ProgressiveSearchSnapshot, SearchComparison } from "../lib/types";

const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "unavailable", "held"]);
const statusLabels: Record<string, string> = {
  QUEUED: "분석 대기", RUNNING: "구성대비 중", SUCCEEDED: "분석 실행 종료",
  FAILED: "분석 실패", CANCELLED: "분석 중단", unavailable: "삭제된 분석 작업", held: "본문 확보 보류",
};

export default function SearchComparisonPanel({ job }: { job: Job }) {
  const engine = job.search_manifest?.engine as ProgressiveSearchSnapshot | undefined;
  const candidates = visibleSearchCandidates(engine);
  const [selected, setSelected] = useState<string[]>([]);
  const [records, setRecords] = useState<SearchComparison[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const items = await api.searchComparisons(job.id);
        if (disposed) return;
        setRecords(items);
        if (items.some(item => !terminal.has(item.status))) timer = setTimeout(refresh, 2000);
      } catch (e) { if (!disposed) setError((e as Error).message); }
    };
    if (job.job_kind === "similarity_search") void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [job.id, job.job_kind, pending]);
  if (job.job_kind !== "similarity_search" || !candidates.length) return null;
  const ready = terminal.has(job.status);
  const analyzed = new Set(records.filter(record => record.job_id && ["QUEUED", "RUNNING", "SUCCEEDED"].includes(record.status))
    .flatMap(record => record.sources.filter(source => source.status === "ready").map(source => source.candidate_id)));
  const recommend = () => {
    // A convenience selection, not an exclusion rule. Held/partial matches remain selectable.
    const available = candidates.filter(c => !analyzed.has(c.id));
    const preferred = available.filter(c => ["candidate", "detailed"].includes(c.triage_status ?? ""));
    setSelected((preferred.length ? preferred : available.filter(c => c.triage_status !== "rejected")).slice(0, 3).map(c => c.id));
  };
  const start = async () => {
    setPending(true); setError("");
    try {
      const record = await api.compareSearchCandidates(job.id, selected);
      setRecords(old => [...old.filter(r => !record.job_id || r.job_id !== record.job_id), record]);
    } catch (e) { setError((e as Error).message); }
    finally { setPending(false); }
  };
  return <section className="card no-print" aria-labelledby="search-comparison-title">
    <h3 id="search-comparison-title">검색 후보 → 본문 확보 → 구성대비</h3>
    <p>검색의 관련성 판단은 잠정 결과입니다. 비교할 문헌을 선택하면 본문을 확보하고, 원문 근거와 위치를 제시하는 구성대비 분석을 실행합니다.</p>
    <p>전체 후보 {candidates.length}건 중 원하는 문헌을 선택하세요. 한 번에 최대 5건을 함께 분석하며, 나머지 문헌은 다음 분석에서 선택할 수 있습니다. 분석에는 추가 토큰이 사용되며, 본문을 확보하지 못한 문헌은 보류합니다. 설정의 구성대비 모델을 사용합니다.</p>
    <p>본문은 후보의 공개 웹페이지·PDF에서 자동으로 확보해 분석 자료로 저장합니다. Google Patents 설명·청구항은 텍스트로 저장하며 도면은 포함하지 않습니다.</p>
    <div className="btn-row">
      <button className="btn small" disabled={!ready || pending} onClick={recommend}>우선 검토 후보 3건 선택</button>
      <button className="btn small" disabled={pending} onClick={() => setSelected([])}>선택 해제</button>
    </div>
    <fieldset disabled={!ready || pending} className="comparison-selection">
      <legend>구성대비할 문헌 · 전체 {candidates.length}건 · 선택 {selected.length}/5건</legend>
      {candidates.map(c => <label key={c.id} style={{ display: "block", margin: "8px 0" }}>
        <input type="checkbox" checked={selected.includes(c.id)}
          disabled={!selected.includes(c.id) && selected.length >= 5}
          onChange={event => setSelected(old => event.target.checked ? [...old, c.id] : old.filter(id => id !== c.id))} />
        {searchCandidateLabel(c)}
        {analyzed.has(c.id) ? " · 구성대비 이력 있음" : ""}
        {c.triage_status === "hold" ? " · 자료 부족" : c.triage_status === "rejected" ? " · 관련성 낮음(재검토 가능)" : ""}
      </label>)}
    </fieldset>
    {selected.length === 5 && <p role="status">한 번에 분석할 수 있는 5건을 선택했습니다. 다른 문헌을 선택하려면 기존 선택을 해제하세요.</p>}
    <button className="btn primary" disabled={!ready || pending || !selected.length} onClick={start}>
      {pending ? "선택 문헌의 본문 확보 중…" : `선택한 ${selected.length}건 구성대비 시작`}
    </button>
    {!ready && <p>검색 종료 후 문헌을 선택할 수 있습니다.</p>}
    {error && <p role="alert">{error}</p>}
    <div aria-live="polite">
      {records.map((record, i) => <section key={record.job_id ?? `hold-${i}`} style={{ marginTop: 16 }}>
        <strong>{statusLabels[record.status] ?? "분석 진행 중"}</strong>
        <ul>{record.sources.map(source => <li key={source.candidate_id}>
          {source.title}: {source.status === "ready" ? "본문 확보 · 분석 대상" : "보류"} — {source.reason}
        </li>)}</ul>
        {record.job_id && record.status !== "unavailable" && <Link className="btn small" to={`/analysis?job=${encodeURIComponent(record.job_id)}`}>구성대비 진행·결과 보기</Link>}
        {record.status === "SUCCEEDED" && <p>구성별 대응 근거와 미확인 항목은 구성대비 보고서에서 확인하세요.</p>}
        {record.errors?.map((message, n) => <p role="alert" key={n}>{message}</p>)}
      </section>)}
    </div>
  </section>;
}
