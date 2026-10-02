import { useEffect, useRef, useState, lazy, Suspense } from "react";

const Observatory = lazy(() => import("./observatory/Observatory"));

/** 작업 상태를 건드리지 않는, 작은 광학 장난감. */
export default function PrismSecret() {
  const [open, setOpen] = useState(false);
  const [glints, setGlints] = useState(0);
  const taps = useRef({ count: 0, at: 0 });
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const element = dialog.current!;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element.showModal();
    return () => {
      element.close();
      document.body.style.overflow = previousOverflow;
      trigger.current?.focus();
    };
  }, [open]);

  const discover = () => {
    const now = Date.now();
    taps.current.count = now - taps.current.at < 1400 ? taps.current.count + 1 : 1;
    taps.current.at = now;
    setGlints(taps.current.count);
    if (taps.current.count === 3) {
      taps.current.count = 0;
      setGlints(0);
      setOpen(true);
    }
  };

  return (
    <>
      <footer className="app-copyright no-print">
        <button ref={trigger} className="prism-fragment" type="button" onClick={discover}
          aria-label="프리즘 조각" title="세 번의 작은 호기심." data-glints={glints}>
          <svg viewBox="0 0 34 24" aria-hidden="true">
            <path d="M3 16h7M17 4 9 20h17Z" />
            <path className="fragment-light" d="m24 12 7-4m-7 6 7 1m-7 1 7 5" />
          </svg>
        </button>
        <span>All rights reserved by Aidan</span>
      </footer>
      {open && (
        <dialog ref={dialog} className="prism-observatory cosmos-dialog no-print" aria-labelledby="observatory-title"
          onCancel={() => setOpen(false)} onClick={e => { if (e.target === e.currentTarget) setOpen(false); }}>
          <Suspense fallback={<div className="observatory-loading"><h2 id="observatory-title">관측실을 여는 중…</h2><button autoFocus onClick={() => setOpen(false)} aria-label="관측실 닫기">닫기</button></div>}>
            <Observatory onClose={() => setOpen(false)} />
          </Suspense>
        </dialog>
      )}
    </>
  );
}
