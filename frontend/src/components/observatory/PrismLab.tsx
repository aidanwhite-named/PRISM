import { useRef, useState } from "react";
import { clamp, LIGHT_QUESTIONS, photon, projectPoint, SOURCE, wavelengthColor, type Point3 } from "./science";
import { Sources } from "./shared";

const VERTICES: Point3[] = [[-75, 65, -55], [0, -90, -55], [75, 65, -55], [-75, 65, 55], [0, -90, 55], [75, 65, 55]];
const FACES = [[0, 1, 2], [3, 5, 4], [0, 3, 4, 1], [1, 4, 5, 2], [2, 5, 3, 0]];
const RAYS = [410, 460, 500, 550, 590, 620, 680];

export default function PrismLab() {
  const [yaw, setYaw] = useState(-22), [pitch, setPitch] = useState(12), [nm, setNm] = useState(550);
  const [game, setGame] = useState(false), [round, setRound] = useState(0), [feedback, setFeedback] = useState("");
  const [solved, setSolved] = useState(false);
  const drag = useRef<{ x: number; y: number; yaw: number; pitch: number; id: number } | null>(null);
  const points = VERTICES.map(point => projectPoint(point, pitch, yaw));
  const faces = FACES.map((indices, index) => ({ indices, index, depth: indices.reduce((n, i) => n + points[i].z, 0) / indices.length })).sort((a, b) => b.depth - a.depth);
  const light = photon(nm), color = wavelengthColor(nm), question = LIGHT_QUESTIONS[round];
  function changeWavelength(value: number) { setNm(value); if (!solved) setFeedback(""); }
  function check() {
    if (Math.abs(nm - question.target) <= question.tolerance) { setSolved(true); setFeedback(question.explanation); }
    else setFeedback(nm < question.target ? "파장을 조금 더 길게 옮겨 보세요. 숫자와 빛의 관계를 살펴보세요." : "파장을 조금 더 짧게 옮겨 보세요. 숫자와 빛의 관계를 살펴보세요.");
  }
  return <div className="cosmos-room">
    <div className="cosmos-intro"><span className="cosmos-kicker">01 / LIGHT, WITH A TWIST</span><h3>빛을 돌리면,<br /><em>질문도 돌아갑니다.</em></h3><p>유리 조각을 잡고 드래그하세요. 빛줄기를 누르면 그 빛의 파장으로 이동합니다.</p></div>
    <div className="prism-stage">
      <svg viewBox="0 0 720 380" className="prism-space" aria-label="드래그하거나 방향키로 회전하는 입체 프리즘" role="group" tabIndex={0}
        onKeyDown={e => { if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) { e.preventDefault(); if (e.key === "ArrowLeft") setYaw(v => v - 8); if (e.key === "ArrowRight") setYaw(v => v + 8); if (e.key === "ArrowUp") setPitch(v => clamp(v - 8, -85, 85)); if (e.key === "ArrowDown") setPitch(v => clamp(v + 8, -85, 85)); } }}
        onPointerDown={e => { if ((e.target as Element).closest(".spectrum-ray")) return; drag.current = { x: e.clientX, y: e.clientY, yaw, pitch, id: e.pointerId }; e.currentTarget.setPointerCapture(e.pointerId); }}
        onPointerMove={e => { const start = drag.current; if (!start || start.id !== e.pointerId) return; setYaw(start.yaw + (e.clientX - start.x) * .6); setPitch(clamp(start.pitch - (e.clientY - start.y) * .5, -85, 85)); }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
        <defs><radialGradient id="prism-halo"><stop stopColor="#adb3e9" stopOpacity=".13" /><stop offset="1" stopColor="#101521" stopOpacity="0" /></radialGradient><linearGradient id="prism-face" x2="1" y2="1"><stop stopColor="#b8c8f3" stopOpacity=".2" /><stop offset="1" stopColor="#c48dde" stopOpacity=".04" /></linearGradient></defs>
        <circle cx="350" cy="190" r="175" fill="url(#prism-halo)" /><path d="M20 190H700M350 28V340" stroke="#ffffff0a" strokeDasharray="3 8" />
        <path d="M20 190H290" stroke="#e5e6f2" strokeWidth="2" />
        {RAYS.map((wave, index) => <g key={wave} className="spectrum-ray" role="button" tabIndex={0} aria-label={`${wave} nm 빛 선택`} onClick={() => changeWavelength(wave)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); changeWavelength(wave); } }}>
          <path d={`M390 190 Q495 ${190 + (index - 3) * 15} 687 ${190 + (index - 3) * 40}`} stroke="transparent" strokeWidth="18" fill="none" />
          <path d={`M390 190 Q495 ${190 + (index - 3) * 15} 687 ${190 + (index - 3) * 40}`} stroke={wavelengthColor(wave)} strokeWidth={nm === wave ? 3 : 1.5} opacity={nm === wave ? 1 : .6} fill="none" /><circle cx="687" cy={190 + (index - 3) * 40} r="4" fill={wavelengthColor(wave)} />
        </g>)}
        {faces.map(face => <polygon key={face.index} data-face={face.index} points={face.indices.map(i => `${points[i].x.toFixed(2)},${points[i].y.toFixed(2)}`).join(" ")} fill="url(#prism-face)" stroke="#cdd7ed" strokeOpacity=".65" strokeWidth="1.1" />)}
        <text x="24" y="345" fill="#8f9bad" fontSize="10" letterSpacing="3">DRAG THE UNEXPECTED</text><text x="560" y="345" fill="#8f9bad" fontSize="10">PITCH {Math.round(pitch)}° / YAW {Math.round(yaw)}°</text>
      </svg>
      <div className="cosmos-controls twin-controls"><label>좌우 회전 <input type="range" min="-180" max="180" value={((yaw + 180) % 360 + 360) % 360 - 180} onChange={e => setYaw(Number(e.target.value))} /></label><label>위아래 회전 <input type="range" min="-85" max="85" value={pitch} onChange={e => setPitch(Number(e.target.value))} /></label><button onClick={() => { setYaw(-22); setPitch(12); }}>처음 각도로</button></div>
    </div>
    <div className="cosmos-split">
      <section className="cosmos-card spectrum-card" style={{ borderColor: `${color}55` }}><span className="cosmos-kicker">WAVELENGTH / 파장</span><div className="wavelength-readout" style={{ color }}>{nm.toLocaleString()} <small>nm</small></div><span className="cosmos-badge">{light.band}</span>
        <label className="cosmos-slider">빛의 파장 <input type="range" min="200" max="1600" step="5" value={nm} onChange={e => changeWavelength(Number(e.target.value))} /></label>
        <div className="cosmos-chips">{[300, 450, 550, 650, 1000].map(value => <button key={value} aria-pressed={nm === value} onClick={() => changeWavelength(value)}>{value} nm</button>)}</div>
        <svg viewBox="0 0 600 80" className="wave-strip" role="img" aria-label={`${nm} nm 빛의 파장을 상대적으로 표현한 파형`}><path d={Array.from({ length: 241 }, (_, i) => `${i ? "L" : "M"}${i * 2.5},${40 + 24 * Math.sin(i * 2.5 / (nm / 22) * Math.PI)}`).join(" ")} stroke={color} fill="none" strokeWidth="2" /></svg>
        <dl className="science-metrics"><div><dt>진동수 f = c / λ</dt><dd>{(light.frequency / 1e12).toFixed(1)} THz</dd></div><div><dt>광자 하나의 에너지 E = hf</dt><dd>{light.energy.toFixed(3)} eV</dd></div></dl>
        <p className="cosmos-note">빛은 진공에서 같은 속도로 갑니다. 짧은 파장은 높은 진동수와 큰 광자 에너지를 뜻합니다. 화면 파형은 상대적인 길이 그림이며, 에너지는 밝기를 뜻하지 않습니다.</p>
      </section>
      <section className="cosmos-card story-card"><h4>보이지 않는 빛도 빛입니다.</h4><p>{nm < 380 ? "자외선은 우리 눈보다 짧은 파장을 봅니다. UV를 담은 천문 관측은 뜨겁고 활동적인 별의 단서를 찾아냅니다. 화면의 보라색은 눈에 안 보이는 빛에 붙인 표시입니다." : nm > 780 ? "적외선은 눈으로 보는 빨강 너머입니다. 여기서 고른 근적외선은 열화상에 주로 쓰이는 더 긴 파장과 다릅니다. 먼지 너머 별을 살피는 일에도 적외선이 쓰입니다." : "흰빛에는 여러 파장의 빛이 섞여 있습니다. 유리의 굴절률이 파장마다 달라 프리즘에서 빛이 갈라집니다. 무지개를 일곱 칸으로 나누는 것은 우리에게 익숙한 이름 붙이기입니다."}</p><p className="cosmos-note">이 프리즘은 입체 투영과 스펙트럼의 개념 그림입니다. 회전에 따른 광선 경로를 실제로 계산하는 광학 실험은 아닙니다. 가시광 경계 380–780 nm도 개인과 조건에 따라 달라집니다.</p>
        <button className="cosmos-action" onClick={() => { setGame(v => !v); setRound(0); setSolved(false); setFeedback(""); }}>{game ? "게임 접기" : "빛의 암호 게임 시작 ↗"}</button>
        {game && <div className="light-game"><span className="cosmos-kicker">MISSION {round + 1} / {LIGHT_QUESTIONS.length}</span><h4>{question.title}</h4><p>{question.clue}</p><p className="cosmos-note">파장 슬라이더나 숫자 버튼으로 맞춰 보세요.</p><p role="status">{feedback}</p>{!solved ? <button onClick={check}>이 파장으로 확인</button> : round < LIGHT_QUESTIONS.length - 1 ? <button onClick={() => { setRound(v => v + 1); setSolved(false); setFeedback(""); }}>다음 빛 찾기 →</button> : <><p className="cosmos-success">다섯 개의 빛을 모두 찾았습니다. 오늘의 칭호: 보이지 않는 것의 관찰자.</p><button onClick={() => { setRound(0); setSolved(false); setFeedback(""); }}>다시 도전</button></>}</div>}
      </section>
    </div><Sources links={[SOURCE.spectrum]} />
  </div>;
}
