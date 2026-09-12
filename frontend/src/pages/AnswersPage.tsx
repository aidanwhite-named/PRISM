import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { emptyExample, type AnswerCase, type AnswerCaseSummary, type AnswerExample } from "../lib/answers";
import type { HistoryItem } from "../lib/types";

const STATUS = {draft: "검토 중", approved: "활용 중", archived: "활용 중지"};

export default function AnswersPage() {
  const [params] = useSearchParams();
  const [cases, setCases] = useState<AnswerCaseSummary[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [current, setCurrent] = useState<AnswerCase | null>(null);
  const [creating, setCreating] = useState(true);
  const [title, setTitle] = useState("");
  const [claim, setClaim] = useState("");
  const [sourceJob, setSourceJob] = useState(params.get("source") ?? "");
  const [report, setReport] = useState<File | null>(null);
  const [sources, setSources] = useState<File[]>([]);
  const [style, setStyle] = useState("");
  const [stylePath, setStylePath] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const selection = useRef(0);
  const attempt = current?.extractions.find(x => x.status === "queued" || x.status === "running");
  const locked = busy || !!attempt;

  const refreshList = () => api.answerCases().then(setCases);
  useEffect(() => {
    let alive = true;
    Promise.all([api.answerCases(), api.history(), api.answerStyle()]).then(([rows, jobs, profile]) => {
      if (!alive) return;
      setCases(rows); setHistory(jobs.filter(j => j.job_kind === "patent_analysis"));
      setStyle(profile.text); setStylePath(profile.path);
    }).catch(e => alive && setError(e.message));
    return () => {alive = false;};
  }, []);

  useEffect(() => {
    if (!attempt || !current) return;
    let alive = true;
    const timer = window.setInterval(() => {
      api.answerCase(current.id).then(row => {
        if (alive) {
          setCurrent(row);
          if (!row.extractions.some(x => x.status === "queued" || x.status === "running")) {
            refreshList().catch(e => setError(e.message));
          }
        }
      }).catch(e => alive && setError(e.message));
    }, 1500);
    return () => {alive = false; window.clearInterval(timer);};
  }, [current?.id, attempt?.id]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(""); setMessage("");
    try { await action(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const open = (id: string) => run(async () => {
    const key = ++selection.current;
    const row = await api.answerCase(id);
    if (key === selection.current) {setCurrent(row); setCreating(false); setDirty(false);}
  });
  const change = (patch: Partial<AnswerCase>) => {
    setCurrent(row => row ? {...row, ...patch} : row); setDirty(true);
  };
  const changeExample = (index: number, patch: Partial<AnswerExample>) => {
    if (!current) return;
    change({draft: {...current.draft, examples: current.draft.examples.map((e, i) => i === index ? {...e, ...patch} : e)}});
  };
  const save = async () => {
    if (!current) throw new Error("정답 사례를 선택하십시오.");
    if (!dirty) return current;
    const row = await api.updateAnswer(current.id, {title: current.title, claim_text: current.claim_text,
      report_text: current.report_text, draft: current.draft, edit_version: current.edit_version});
    setCurrent(row); setDirty(false); await refreshList(); return row;
  };
  const register = () => run(async () => {
    const body = new FormData(); body.append("title", title.trim()); body.append("claim_text", claim);
    body.append("source_job_id", sourceJob);
    if (report) body.append("report", report);
    sources.forEach(file => body.append("sources", file));
    const row = await api.registerAnswer(body);
    setCurrent(row); setCreating(false); setDirty(false);
    setTitle(""); setClaim(""); setReport(null); setSources([]); setSourceJob("");
    await refreshList(); setMessage("정답 보고서를 등록했습니다. AI로 정리한 내용을 확인하고 최종본으로 확정해 주세요.");
  });

  return <div className="page page-answers">
    <div className="page-head"><span className="eyebrow">PRISM v2 · 정답 라이브러리</span>
      <h1>내 보고서를 다음 판단의 기준으로</h1>
      <p>완성한 보고서를 등록하면 판단 사례와 문체를 정리합니다. 최종본으로 확정한 내용만 다음 구성대비 보고서에 활용합니다.</p>
    </div>
    {error && <div role="alert" className="notice danger">{error}</div>}
    {message && <div role="status" className="notice ok">{message}</div>}
    <div className="answer-layout">
      <aside className="answer-sidebar">
        <button className="btn primary" disabled={locked || dirty} onClick={() => {++selection.current; setCreating(true); setCurrent(null);}}>+ 정답 보고서 등록</button>
        <p className="faint">{cases.filter(c => c.status === "approved").length}건 활용 중 · 전체 {cases.length}건</p>
        <div className="answer-case-list">
          {cases.map(c => <button key={c.id} disabled={busy || dirty || !!attempt} className={`answer-case ${current?.id === c.id ? "selected" : ""}`} onClick={() => open(c.id)}>
            <strong>{c.title}</strong><span>{STATUS[c.status]} · 판단 {c.example_count}건{c.revision > 0 ? ` · v${c.revision}` : ""}</span>
          </button>)}
          {!cases.length && <p className="faint">등록한 정답 보고서가 여기에 표시됩니다.</p>}
        </div>
        {dirty && <p className="faint">변경 내용을 저장하면 다른 사례로 이동할 수 있습니다.</p>}
      </aside>
      <div className="answer-main">
        {creating ? <section className="card answer-card">
          <h2>정답 보고서 등록</h2>
          <label>사례 이름<input value={title} maxLength={200} onChange={e => setTitle(e.target.value)} placeholder="예: 센서 신호에 따른 모터 제어" /></label>
          <label>기존 실행에 연결 <span className="faint">선택 사항</span>
            <select value={sourceJob} onChange={e => setSourceJob(e.target.value)}>
              <option value="">새 사건으로 등록</option>
              {sourceJob && !history.some(j => j.id === sourceJob) && <option value={sourceJob}>선택한 실행</option>}
              {history.map(j => <option key={j.id} value={j.id}>{new Date(j.created_at).toLocaleDateString()} · {j.prompt_name} · {j.id.slice(0, 8)}</option>)}
            </select>
          </label>
          {sourceJob && <p className="faint">해당 실행의 구성요소와 포함된 문헌을 별도 보관합니다. 정답 파일을 생략하면 기존 결과를 검토 초안으로 가져옵니다.</p>}
          <label>정답 보고서<input type="file" accept=".pdf,.txt,.md" onChange={e => setReport(e.target.files?.[0] ?? null)} /></label>
          <p className="faint">텍스트 PDF·TXT·MD를 지원합니다. 스캔 PDF는 텍스트 PDF로 변환해 주세요.</p>
          <label>직접 분해한 구성요소<textarea rows={5} value={claim} onChange={e => setClaim(e.target.value)} placeholder={sourceJob ? "비워두면 기존 실행의 입력을 사용합니다." : "C1 …\nC2 …"} /></label>
          <label>당시 사용한 선행문헌<input type="file" multiple accept=".pdf,.txt,.md" onChange={e => setSources(Array.from(e.target.files ?? []))} /></label>
          <p className="faint">문헌을 함께 연결하면 보고서의 인용 구절을 원문과 대조할 수 있습니다. 파일은 PC에 보관하며, AI 정리를 누르면 보고서 본문·구성요소·문헌 이름이 설정된 LLM에 전달됩니다.</p>
          <button className="btn primary" disabled={busy || !title.trim() || (!report && !sourceJob)} onClick={register}>{busy ? "등록 중…" : "보고서 등록"}</button>
        </section> : current && <section className="card answer-card">
          <div className="answer-title-row"><div><span className={`answer-status ${current.status}`}>{STATUS[current.status]}</span><h2>{current.title}</h2></div>
            <span className="faint">{current.revision ? `확정 이력 v${current.revision}` : "아직 확정하지 않음"}</span></div>
          {current.extraction_error && <div className="notice info">{current.extraction_error}</div>}
          <div className="answer-actions">
            <button className="btn" disabled={locked} onClick={() => run(async () => {const row = await save(); setCurrent(await api.extractAnswer(row.id));})}>AI로 판단·문체 정리</button>
            <button className="btn" disabled={locked || !dirty} onClick={() => run(async () => {await save(); setMessage("검토 내용을 저장했습니다.");})}>수정 저장</button>
            <button className="btn primary" disabled={locked || (current.status === "approved" && !dirty)} onClick={() => run(async () => {
              const row = await save(); setCurrent(await api.approveAnswer(row.id, row.edit_version));
              await refreshList(); setMessage("최종본으로 확정했습니다. 다음 보고서부터 관련 판단 사례와 문체를 활용합니다.");
            })}>최종본 확정 · 다음 보고서에 활용</button>
            <button className="btn" disabled={locked || dirty} onClick={() => run(async () => {
              setCurrent(await api.archiveAnswer(current.id, current.edit_version)); await refreshList();
            })}>{current.status === "archived" ? "검토로 복원" : "활용 중지"}</button>
          </div>
          {attempt && <div role="status" className="notice info">{attempt.status === "queued" ? "AI 정리 대기 중" : "판단과 문체를 정리하고 있습니다"} · {attempt.provider} / {attempt.model || "기본 모델"}
            <button className="btn" disabled={busy} onClick={() => run(async () => setCurrent(await api.cancelAnswerExtraction(current.id, attempt.id)))}>정리 취소</button></div>}
          {current.extractions[0]?.error && <div role="alert" className="notice danger">{current.extractions[0].error}</div>}
          <fieldset disabled={locked} className="answer-fields">
            <label>사례 이름<input value={current.title} maxLength={200} onChange={e => change({title: e.target.value})} /></label>
            <label>구성요소<textarea rows={4} value={current.claim_text} onChange={e => change({claim_text: e.target.value})} /></label>
            <div className="answer-file-links">{current.files.map(f => <a key={f.id} href={`/api/answers/${current.id}/files/${f.id}`} target="_blank" rel="noreferrer">{f.kind === "report" ? "정답 원본" : "선행문헌"} · {f.name}</a>)}</div>
            <details><summary>추출된 보고서 본문 확인·수정</summary>
              <p className="faint">표의 열과 인용 구절이 제대로 추출됐는지 확인해 주세요. 등록 원본은 별도로 보존합니다.</p>
              <textarea aria-label="정답 보고서 본문" rows={16} value={current.report_text} onChange={e => change({report_text: e.target.value})} />
            </details>
            <label>이 보고서의 문체 기준 <span className="faint">한 줄에 하나</span>
              <textarea rows={5} value={current.draft.style_rules.join("\n")} onChange={e => change({draft: {...current.draft, style_rules: e.target.value.split("\n")}})} />
            </label>
            <h3>판단 사례 <span className="faint">{current.draft.examples.length}건</span></h3>
            {!current.draft.examples.length && <p className="faint">AI로 정리하면 보고서에 명시된 판단과 근거 문장이 여기에 표시됩니다. 코멘트를 따로 쓰지 않아도 됩니다.</p>}
            {current.draft.examples.map((example, i) => <details className="answer-example" key={i} open={i === 0}>
              <summary>{i + 1}. {example.element || "새 판단 사례"} <span className="faint">{example.judgment}</span></summary>
              <div className="answer-example-fields">
                <label>구성<input value={example.element} onChange={e => changeExample(i, {element: e.target.value})} /></label>
                <label>판단 쟁점<input value={example.issue} onChange={e => changeExample(i, {issue: e.target.value})} /></label>
                <label>정답 판단<textarea rows={2} value={example.judgment} onChange={e => changeExample(i, {judgment: e.target.value})} /></label>
                <label>보고서에 명시된 이유<textarea rows={2} value={example.reason} onChange={e => changeExample(i, {reason: e.target.value})} /></label>
                <label>정답 보고서의 근거 문장<textarea rows={3} value={example.report_quote} onChange={e => changeExample(i, {report_quote: e.target.value})} /></label>
                <label>연결 선행문헌<select value={example.source_id} onChange={e => changeExample(i, {source_id: e.target.value})}>
                  <option value="">원문 연결 없음</option>{current.files.filter(f => f.kind === "source").map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
                </select></label>
                <label>선행문헌 원문 구절 <span className="faint">선택 사항 · 입력 시 원문 대조</span><textarea rows={2} value={example.source_quote} onChange={e => changeExample(i, {source_quote: e.target.value})} /></label>
                <label>문단·페이지<input value={example.location} onChange={e => changeExample(i, {location: e.target.value})} /></label>
                <button className="btn" onClick={() => change({draft: {...current.draft, examples: current.draft.examples.filter((_, index) => i !== index)}})}>이 판단 사례 제외</button>
              </div>
            </details>)}
            <button className="btn" onClick={() => change({draft: {...current.draft, examples: [...current.draft.examples, emptyExample()]}})}>판단 사례 직접 추가</button>
          </fieldset>
          {!!current.extractions.length && <details className="answer-audit"><summary>AI 원본과 정리 기록 ({current.extractions.length}회)</summary>
            {current.extractions.map(a => <details key={a.id}><summary>{new Date(a.created_at).toLocaleString()} · {a.provider} · {a.status}</summary>
              <pre>{a.result_text || a.error || "응답 대기 중"}</pre>
              <pre>{JSON.stringify(a.execution_manifest, null, 2)}</pre>
            </details>)}
          </details>}
        </section>}
        <section className="card answer-card">
          <h2>공통 보고서 작성 기준</h2>
          <p className="faint">비워두면 확정한 정답지의 문체 기준을 모아 적용합니다. 직접 저장하면 이 기준을 우선합니다. 판단 사례는 별도로 선택됩니다.</p>
          <textarea aria-label="공통 보고서 작성 기준" rows={6} maxLength={3000} value={style} onChange={e => setStyle(e.target.value)} placeholder="예: 문장은 ‘~로 판단됨’으로 마무리한다." />
          <div className="answer-actions"><button className="btn" disabled={busy} onClick={() => run(async () => {await api.saveAnswerStyle(style); setMessage("공통 작성 기준을 저장했습니다.");})}>작성 기준 저장</button><span className="faint">{style.length.toLocaleString()} / 3,000자</span></div>
          <details><summary>저장 위치와 사용량</summary><p className="faint">{stylePath}</p><p className="faint">사례는 로컬 DB에 보관합니다. 생성 시 관련 판단 최대 5건과 문체를 합해 추정 6,000토큰 이내로 전달합니다. AI 정리는 등록한 보고서를 읽는 별도 사용량이 발생합니다.</p></details>
        </section>
      </div>
    </div>
  </div>;
}
