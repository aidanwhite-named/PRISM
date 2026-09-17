import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { Job, ProgressiveSearchSnapshot } from "../lib/types";

export default function SearchContinuation({ job, disabled, onContinued }: {
  job: Job; disabled: boolean; onContinued: (job: Job) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const engine = job.search_manifest?.engine as ProgressiveSearchSnapshot | undefined;
  const eligible = (job.status === "SUCCEEDED" ||
    (job.status === "FAILED" && engine?.stop_reason === "classification_incomplete")) && engine?.can_continue === true;
  const dismissedKey = `prism.search-continuation.${job.id}`;
  useEffect(() => {
    if (eligible && !disabled && engine?.verified_match === false && !sessionStorage.getItem(dismissedKey)) {
      dialog.current?.showModal();
    }
  }, [eligible, disabled, engine?.verified_match, dismissedKey]);
  const dismiss = () => {
    sessionStorage.setItem(dismissedKey, "dismissed");
    dialog.current?.close();
  };
  const proceed = async () => {
    setPending(true);
    setError("");
    try {
      const continued = await api.continueSearch(job.id);
      dismiss();
      onContinued(continued);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  };
  if (!eligible) return null;
  return <div className="no-print" style={{ marginBottom: 12 }}>
    <button className="btn" disabled={disabled || pending} onClick={() => dialog.current?.showModal()}>
      정밀 검색 이어서 진행
    </button>
    <dialog ref={dialog} aria-labelledby="search-continuation-title"
      onCancel={event => { event.preventDefault(); if (!pending) dismiss(); }}
      style={{ maxWidth: 520, padding: 24, borderRadius: 12, border: "1px solid #888" }}>
      <h3 id="search-continuation-title">정밀 검색을 계속할까요?</h3>
      <p>{engine?.stop_reason === "classification_incomplete" ? "분류 응답을 모두 확보하지 못했습니다. 저장된 후보와 부분 분류를 이어받을 수 있습니다." :
        engine?.verified_match ? "더 넓은 범위에서 문헌을 검토할 수 있습니다." :
        "기본 검색 범위에서 원문 근거가 확인된 X·Y 문헌을 찾지 못했습니다."}</p>
      <p>기존 후보와 원문 근거를 이어받아 검색 범위와 검토 문헌 수를 늘립니다. 추가 시간이 걸릴 수 있습니다.</p>
      {error && <p role="alert">{error}</p>}
      <div className="btn-row">
        <button className="btn" autoFocus disabled={pending} onClick={dismiss}>현재 결과 보기</button>
        <button className="btn primary" disabled={disabled || pending} onClick={proceed}>
          {pending ? "이어가는 중…" : "정밀 검색 계속"}
        </button>
      </div>
    </dialog>
  </div>;
}
