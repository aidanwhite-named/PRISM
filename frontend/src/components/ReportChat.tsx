import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { Job, ReportChatTurn } from "../lib/types";
import ResultView from "./ResultView";

interface Props {
  job: Job;
  onRevise: (instruction: string) => void;
  revisionDisabled?: boolean;
}

const pending = (turn: ReportChatTurn) => turn.status === "queued" || turn.status === "running";

export default function ReportChat({ job, onRevise, revisionDisabled }: Props) {
  const draftKey = `prism.report-chat.draft.${job.id}`;
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ReportChatTurn[]>([]);
  const [draft, setDraft] = useState(() => {
    try { return sessionStorage.getItem(draftKey) ?? ""; } catch { return ""; }
  });
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const mounted = useRef(true);
  const submitLock = useRef(false);
  const request = useRef<{ question: string; id: string } | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const active = turns.find(pending);
  const hasPending = Boolean(active);
  const busy = sending || Boolean(active);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    try { sessionStorage.setItem(draftKey, draft); } catch { /* Draft storage is optional. */ }
  }, [draftKey, draft]);

  useEffect(() => {
    if (!open && !hasPending) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    if (open) setLoading(true);
    const load = async () => {
      try {
        const history = await api.reportChat(job.id);
        if (stopped) return;
        setTurns(history);
        timer = setTimeout(load, history.some(pending) ? 1500 : 10000);
      } catch (e) {
        if (!stopped) {
          setError((e as Error).message);
          timer = setTimeout(load, 5000);
        }
      } finally { if (!stopped) setLoading(false); }
    };
    void load();
    if (open) input.current?.focus();
    return () => { stopped = true; clearTimeout(timer); };
  }, [open, hasPending, job.id, reload]);

  useEffect(() => { if (open) bottom.current?.scrollIntoView?.({ block: "nearest" }); }, [turns, open]);

  const close = () => { setOpen(false); launcher.current?.focus(); };
  const send = async (question = draft.trim()) => {
    if (!question || busy || loading || submitLock.current) return;
    submitLock.current = true;
    setSending(true);
    setError("");
    if (request.current?.question !== question) request.current = { question, id: crypto.randomUUID() };
    try {
      const turn = await api.askReport(job.id, question, request.current.id);
      if (!mounted.current) return;
      setTurns(old => old.some(t => t.id === turn.id) ? old.map(t => t.id === turn.id ? turn : t) : [...old, turn]);
      setDraft("");
      request.current = null;
      setReload(n => n + 1);
    } catch (e) { if (mounted.current) { setError((e as Error).message); setReload(n => n + 1); } }
    finally { submitLock.current = false; if (mounted.current) setSending(false); }
  };

  const cancel = async () => {
    if (!active) return;
    try {
      const turn = await api.cancelReportAnswer(job.id, active.id);
      if (mounted.current) { setTurns(old => old.map(t => t.id === turn.id ? turn : t)); setReload(n => n + 1); }
    } catch (e) { if (mounted.current) setError((e as Error).message); }
  };

  const revise = () => {
    const completed = turns.filter(t => t.status === "succeeded");
    const discussion = completed.map(t => `사용자: ${t.question}\n도우미: ${t.answer}`).join("\n\n");
    onRevise(`다음은 현재 보고서에 대한 사용자와 도우미의 대화입니다. 사용자가 요청한 수정·보완을 반영하고 도우미의 제안은 원문 근거로 다시 검토하십시오. 질문만 한 사항을 수정 지시로 간주하지 마십시오.\n\n${discussion}${draft.trim() ? `\n\n사용자의 추가 수정 요청: ${draft.trim()}` : ""}`);
    setOpen(false);
  };

  return <aside className="report-chat no-print" aria-label="현재 보고서 대화">
    {open && <section className="report-chat-panel" role="dialog" aria-modal="false" aria-labelledby="report-chat-title"
      id="report-chat-panel" onKeyDown={e => { if (e.key === "Escape") close(); }}>
      <header className="report-chat-head">
        <div><strong id="report-chat-title">보고서 도우미</strong><span>현재 보고서에 질문하고 수정 방향을 정하세요</span></div>
        <button type="button" className="report-chat-close" aria-label="대화창 접기" onClick={close}>×</button>
      </header>
      <div className="report-chat-context">이 보고서 · {job.claim_text.split("\n")[0].slice(0, 65) || "구성대비 분석"}</div>
      <div className="report-chat-messages" role="log" aria-label="보고서 대화 내용" aria-live="polite" aria-relevant="additions text">
        {!turns.length && !loading && <div className="report-chat-welcome">
          <strong>보고서에서 궁금한 부분을 물어보세요.</strong>
          <p>청구항, 구성대비, 인용발명의 대응 근거와 차이점을 함께 확인할 수 있습니다.</p>
          {["주 인용발명으로 대응되지 않은 부분을 설명해줘", "추가 인용발명이 필요한 이유가 뭐야?"].map(q =>
            <button key={q} type="button" disabled={busy} onClick={() => { setDraft(q); input.current?.focus(); }}>{q}</button>)}
        </div>}
        {loading && !turns.length && <p className="faint">대화를 불러오는 중…</p>}
        {turns.map(turn => <div className="report-chat-turn" key={turn.id}>
          <div className="report-chat-question"><span className="sr-only">내 질문: </span>{turn.question}</div>
          <div className="report-chat-answer">
            <span className="report-chat-speaker">보고서 도우미</span>
            {turn.answer && <ResultView text={turn.answer} outputMode="markdown" />}
            {pending(turn) && <p className="report-chat-status" role="status"><span className="spinner" /> {turn.status === "queued" ? "답변을 준비하고 있습니다…" : "보고서와 근거를 확인하고 있습니다…"}</p>}
            {(turn.status === "failed" || turn.status === "cancelled") && <div>
              <p className="report-chat-status">{turn.error || "답변이 중단되었습니다."}</p>
              <button type="button" className="btn small" disabled={busy || loading} onClick={() => void send(turn.question)}>다시 질문</button>
            </div>}
            {turn.status === "succeeded" && turn.context_scope === "report_evidence" && <span className="report-chat-scope">보고서와 인용된 원문 근거를 기준으로 답변했습니다.</span>}
          </div>
        </div>)}
        <div ref={bottom} />
      </div>
      {error && <div className="report-chat-error" role="alert">{error}<button type="button" onClick={() => { setError(""); setReload(n => n + 1); }}>다시 확인</button></div>}
      <form className="report-chat-compose" onSubmit={e => { e.preventDefault(); void send(); }}>
        <textarea ref={input} aria-label="보고서에 대한 질문" placeholder="이 보고서에 대해 물어보세요…" value={draft} maxLength={10000} rows={3}
          onChange={e => setDraft(e.target.value)} onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
          }} />
        <div className="report-chat-compose-actions"><span>Enter 전송 · Shift+Enter 줄바꿈</span>
          {active ? <button type="button" className="btn small" onClick={() => void cancel()}>답변 중단</button>
            : <button type="submit" className="btn primary small" disabled={sending || loading || !draft.trim()}>{sending ? "보내는 중…" : "전송"}</button>}
        </div>
      </form>
      <footer className="report-chat-footer"><button type="button" className="btn small" onClick={revise}
        disabled={busy || loading || revisionDisabled || (!turns.some(t => t.status === "succeeded") && !draft.trim())}>대화 내용으로 수정·보완</button>
        <span>수정 요청을 담아 새 보고서를 준비합니다.</span></footer>
    </section>}
    <button ref={launcher} className="report-chat-launcher" type="button" onClick={() => open ? close() : setOpen(true)}
      aria-expanded={open} aria-controls="report-chat-panel" aria-haspopup="dialog">
      <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M20 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2v-10.5A8.5 8.5 0 0 1 10.5 3h1A8.5 8.5 0 0 1 20 11.5Z" /><path d="M7 9h8M7 13h6" /></svg>
      {open ? "대화창 접기" : "보고서 수정·보완"}{!open && active && <span className="spinner" />}
    </button>
  </aside>;
}
