import { useEffect, useRef, useState } from "react";
import PrismLab from "./PrismLab";
import PeriodicRoom from "./PeriodicRoom";
import AtomJourney from "./AtomJourney";
import SceneCanvas from "./SceneCanvas";
import { cesiumTicks, CESIUM_FREQUENCY, clamp, nearestScale, SCALE_STOPS, SOURCE, STAR_STAGES } from "./science";
import { Sources, type Navigate, type Room } from "./shared";
import "./observatory.css";

const ROOMS: { id: Room; name: string; hint: string }[] = [
  { id: "prism", name: "빛의 방", hint: "회전 · 파장 · 게임" }, { id: "elements", name: "원소의 서가", hint: "118개의 이야기" },
  { id: "clock", name: "1초의 탄생", hint: "세슘 · 원자의 여정" }, { id: "stars", name: "별의 다음 장", hint: "폭발 · 펄사 · 블랙홀" },
  { id: "scale", name: "우주의 눈금", hint: "작은 것에서 큰 것으로" }, { id: "flow", name: "흐름의 비밀", hint: "질서와 무질서" },
  { id: "ccc", name: "다음 우주의 문", hint: "펜로즈의 가설" },
];
function TimeRoom({ animated, navigate }: { animated: boolean; navigate: Navigate }) {
  const [story, setStory] = useState(false);
  return <><nav className="time-room-switch" aria-label="시간 이야기 선택"><button aria-pressed={!story} onClick={() => setStory(false)}>1초의 눈금</button><span aria-hidden="true">/</span><button aria-pressed={story} onClick={() => setStory(true)}>잠시, 나라는 모양 ↗</button></nav>{story ? <AtomJourney animated={animated} /> : <ClockRoom navigate={navigate} />}</>;
}
function ClockRoom({ navigate }: { navigate: Navigate }) {
  const [seconds, setSeconds] = useState(0), [running, setRunning] = useState(false);
  useEffect(() => {
    if (!running) return;
    let frame = 0, start: number | undefined;
    const tick = (time: number) => { start ??= time; const next = clamp((time - start) / 1000, 0, 1); setSeconds(next); if (next < 1) frame = requestAnimationFrame(tick); else setRunning(false); };
    frame = requestAnimationFrame(tick); return () => cancelAnimationFrame(frame);
  }, [running]);
  const count = cesiumTicks(seconds);
  return <div className="cosmos-room"><div className="cosmos-intro"><span className="cosmos-kicker">03 / COUNTING THE INVISIBLE</span><h3>1초는,<br /><em>누가 정했을까요?</em></h3><p>지구의 회전 대신 원자의 일정한 기준을 선택했습니다. 같은 질문을 세계 어디서나 같은 수로 답하기 위해서요.</p></div>
    <div className="cosmos-card atomic-clock"><span className="cosmos-kicker">CESIUM-133 / 세슘-133</span><div className="clock-counter" aria-live={running ? "off" : "polite"}>{count.toLocaleString("en-US")}</div><p>기준 진동 <strong>{CESIUM_FREQUENCY.toLocaleString("en-US")}</strong>번 = <strong>1초</strong></p><div className="clock-track"><span style={{ width: `${seconds * 100}%` }} /></div><div className="clock-progress">{seconds.toFixed(3)} s <span>{(seconds * 100).toFixed(1)}%</span></div>
      <label className="cosmos-slider">1초를 나눠 보기 <input type="range" min="0" max="1" step="0.001" value={seconds} onChange={e => { setRunning(false); setSeconds(Number(e.target.value)); }} /></label><div className="cosmos-controls"><button className="cosmos-action" onClick={() => { setSeconds(0); setRunning(true); }} disabled={running}>1초 동안 함께 세기 ▶</button><button onClick={() => { setRunning(false); setSeconds(0); }}>다시 0으로</button></div>
    </div><div className="cosmos-split"><section className="cosmos-card"><h4>원자 안의 두 에너지 상태</h4><svg viewBox="0 0 480 220" className="atomic-levels" role="img" aria-label="세슘-133 바닥 상태의 두 초미세 에너지 준위 사이 전이와 마이크로파의 개념 그림"><path d="M65 60H240M65 160H240" stroke="#b7b3e4" strokeWidth="2" /><path d="M150 145V76m-8 10 8-10 8 10" stroke="#dbbf93" fill="none" strokeWidth="2" /><text x="260" y="65" fill="#b9c7d9" fontSize="14">바닥 상태의 초미세 준위</text><text x="260" y="164" fill="#b9c7d9" fontSize="14">전이의 기준 진동수</text><path d={Array.from({ length: 151 }, (_, i) => `${i ? "L" : "M"}${60 + i * 2.4},${205 + Math.sin(i / 5 - seconds * 8) * 9}`).join(" ")} stroke="#88c5c8" fill="none" /></svg><p>기준은 외부 교란이 없는 세슘-133 원자의 바닥 상태 초미세 전이 진동수입니다. 원자핵이 물리적으로 90억 번 흔들린다는 뜻은 아닙니다.</p><p className="cosmos-note">위 파형은 관계를 보여주기 위해 크게 느리게 그린 것입니다. 실제 마이크로파 진동을 화면에서 하나씩 세지는 않습니다. 1초 재생도 브라우저 시간으로 그린 교육용 표시이며 원자시계가 아닙니다.</p></section><section className="cosmos-card story-card"><span className="cosmos-kicker">A REPEATABLE PROMISE</span><h4>우주가 주는 반복을,<br />사람이 단위로 삼았습니다.</h4><p>하루의 길이는 조금씩 달라질 수 있습니다. 원자의 전이를 기준으로 하면 정밀한 측정과 통신, 항법에서 같은 ‘1초’를 공유할 수 있습니다.</p><p>규칙을 고른 것은 사람입니다. 그 규칙을 되풀이해 주는 것은 자연입니다. 시간이라는 거대한 질문에, 작은 원자 하나가 눈금을 빌려줍니다.</p><button onClick={() => navigate("elements")}>세슘이 사는 주기율표로 →</button><button onClick={() => navigate("stars")}>우주의 다른 시계, 펄사 만나기 →</button></section></div><Sources links={[SOURCE.second]} />
  </div>;
}
function StarRoom({ animated, navigate }: { animated: boolean; navigate: Navigate }) {
  const [stage, setStage] = useState(0), [remnant, setRemnant] = useState<"neutron" | "blackhole">("neutron"), [spin, setSpin] = useState(1), [observer, setObserver] = useState(0);
  const scenes = ["nebula", "protostar", "sun", "giant", "supernova", remnant] as const;
  const story = STAR_STAGES[stage];
  return <div className="cosmos-room"><div className="cosmos-intro"><span className="cosmos-kicker">04 / STARS DO NOT END IN ONE WAY</span><h3>별의 끝에서,<br /><em>다음 재료가 시작됩니다.</em></h3><p>무거운 별의 대표 경로를 한 장씩 넘겨 보세요. 마지막 핵에서 두 갈림길이 열립니다.</p></div>
    <div className="cosmos-visual"><SceneCanvas scene={scenes[stage]} label={`${story.title}${stage === 5 ? remnant === "neutron" ? " · 회전하며 빛줄기를 보내는 펄사" : " · 블랙홀과 강착 원반" : ""} 개념 시각화`} animated={animated} spin={spin} observer={observer} /><span className="visual-caption">개념 애니메이션 · 실제 시간과 크기는 압축했습니다</span></div>
    <div className="star-stages">{STAR_STAGES.map((item, i) => <button key={item.title} aria-pressed={stage === i} onClick={() => setStage(i)}><small>{String(i + 1).padStart(2, "0")}</small>{item.title.split(" · ")[0]}</button>)}</div>
    <div className="cosmos-card"><div className="story-heading"><h4>{story.title}</h4><div className="cosmos-controls"><button disabled={stage === 0} onClick={() => setStage(i => i - 1)}>← 이전</button><button disabled={stage === 5} onClick={() => setStage(i => i + 1)}>다음 →</button></div></div><p>{story.text}</p><p className="cosmos-note">{story.note}</p>
      {stage === 5 && <><div className="cosmos-chips"><button aria-pressed={remnant === "neutron"} onClick={() => setRemnant("neutron")}>중성자별 · 펄사</button><button aria-pressed={remnant === "blackhole"} onClick={() => setRemnant("blackhole")}>블랙홀</button></div>{remnant === "neutron" ? <><h4>별이 깜박이는 걸까요, 빛줄기가 스치는 걸까요?</h4><p>빠르게 회전하는 중성자별의 빛줄기가 지구를 스칠 때 규칙적인 펄스를 볼 수 있습니다. 등대처럼 방향이 중요합니다. 모든 중성자별을 우리가 펄사로 보는 것은 아닙니다.</p><div className="twin-controls cosmos-controls"><label>회전 속도 · 느린 시범 <input type="range" min="0.4" max="3" step="0.1" value={spin} onChange={e => setSpin(Number(e.target.value))} /></label><label>관찰 방향 <input type="range" min="-90" max="90" value={observer} onChange={e => setObserver(Number(e.target.value))} /></label></div><p className="cosmos-note">빛이 관찰선에 닿으면 아래의 펄스 표시가 올라갑니다. 실제 펄사는 훨씬 빠르게 회전할 수 있으며, 이 그림은 2차원 등대 비유입니다.</p><button onClick={() => navigate("clock")}>원자의 시계와 비교하기 →</button></> : <><h4>검은 중심과 밝은 주변</h4><p>사건의 지평선 안에서 나온 빛은 밖으로 도달하지 못합니다. 주변의 뜨거운 강착 물질은 밝게 빛날 수 있고, 중력은 빛의 길을 휘게 합니다.</p><p className="cosmos-note">원반과 휘어진 빛은 이해를 돕는 그림입니다. 일반상대론의 광선 추적 계산이나 특정 블랙홀의 관측 사진은 아닙니다.</p><button onClick={() => navigate("ccc")}>블랙홀의 먼 미래에서 다음 질문으로 →</button></>}</>}
    </div><Sources links={[SOURCE.stars, SOURCE.pulsar]} />
  </div>;
}
function ScaleRoom({ animated, navigate }: { animated: boolean; navigate: Navigate }) {
  const [log, setLog] = useState(0), field = useRef<HTMLDivElement>(null), current = nearestScale(log);
  useEffect(() => { const element = field.current!; const wheel = (event: WheelEvent) => { event.preventDefault(); const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 300 : 1); setLog(value => clamp(value + clamp(delta * .008, -1, 1), -18, 27)); }; element.addEventListener("wheel", wheel, { passive: false }); return () => element.removeEventListener("wheel", wheel); }, []);
  return <div className="cosmos-room"><div className="cosmos-intro"><span className="cosmos-kicker">05 / FORTY-FIVE ORDERS OF MAGNITUDE</span><h3>익숙한 눈금을,<br /><em>한 번 내려놓으면.</em></h3><p>그림 위에서 휠을 굴려 보세요. 아래 방향은 큰 세계로, 위 방향은 작은 세계로 갑니다. 터치와 키보드는 슬라이더로 탐험하세요.</p></div>
    <div ref={field} className="cosmos-visual scale-field" tabIndex={0} role="group" aria-label="우주 스케일 탐험 · 휠 또는 방향키로 눈금 변경" onKeyDown={e => { if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) { e.preventDefault(); setLog(value => clamp(value + (["ArrowDown", "ArrowRight"].includes(e.key) ? .5 : -.5), -18, 27)); } }}><SceneCanvas scene="scale" label={`${current.name} 대표 크기 개념 그림`} animated={animated} value={log} /><div className="scale-overlay"><span>대표 길이의 눈금</span><strong>10<sup>{log.toFixed(2)}</sup> <small>m</small></strong><span>{current.name}</span></div></div>
    <label className="cosmos-slider scale-range">우주의 눈금 <input type="range" min="-18" max="27" step="0.05" value={log} onChange={e => setLog(Number(e.target.value))} /><span>작은 것 ← <b>{current.name}</b> → 큰 것</span></label><div className="cosmos-chips scale-jumps">{SCALE_STOPS.map(stop => <button key={stop.log} aria-pressed={current.log === stop.log} onClick={() => setLog(stop.log)}>{stop.name}</button>)}</div>
    <section className="cosmos-card"><h4>{current.name}</h4><p>{current.caption}</p><p>눈금 하나의 지수 차이 1은 길이의 10배 차이입니다. 작은 규칙으로 큰 구조가 만들어져도, 그 사이의 거리와 조건은 생략할 수 없습니다.</p><p className="cosmos-note">각 단계는 대표 크기를 소개하는 도식입니다. 하나의 실제 우주 지도를 연속 확대하는 사진은 아니며, 단계 사이 크기는 읽기 좋게 재조정됩니다.</p><button onClick={() => navigate("ccc")}>절대적인 눈금이 사라진다는 가설은? →</button></section><Sources links={[SOURCE.universe, SOURCE.spectrum]} />
  </div>;
}
function FlowRoom({ animated }: { animated: boolean }) {
  const [mix, setMix] = useState(.45), [chapter, setChapter] = useState(0);
  const chapters = [
    { title: "흩어짐 안에서 길이 보일 때", text: "많은 입자가 제멋대로 움직이는 것처럼 보여도, 각 입자는 같은 속도장의 규칙을 따라갑니다. 섞임을 낮추면 소용돌이의 길이 잘 보이고, 높이면 시간에 따라 길이 겹쳐 복잡한 무늬가 생깁니다." },
    { title: "카오스와 엔트로피는 같은 말일까요?", text: "카오스는 결정적인 규칙을 가진 계에서도 작은 초기 차이가 크게 벌어질 수 있다는 이야기입니다. 엔트로피는 가능한 미시 상태와 연결되는 물리량입니다. ‘어지러워 보임’ 하나로 둘을 같은 숫자로 측정할 수는 없습니다. 이 그림도 엔트로피 측정기가 아닙니다." },
    { title: "AI는 어떤 흐름을 찾았을까요? · 2025", text: "2025년 불안정 특이점 연구는 신경망과 정밀 계산으로 IPM 모형과 경계가 있는 3차원 Euler 방정식의 특정 자기유사 해를 탐색했습니다. 복잡한 흐름에서 특별한 패턴을 찾는 연구이지, 모든 Navier–Stokes 흐름을 예측하는 일반해를 얻었다는 뜻은 아닙니다." },
    { title: "새 발표와 검증을 구분하기 · 2026", text: "2026년 9월 8일 OpenAI는 특정 외력 조건의 Navier–Stokes 특이점에 대한 해석적 증명과 Lean 형식화를 발표했습니다. 9월 11일 Clay 수학연구소는 평가 절차를 서두르지 않겠다고 안내했습니다. 이 전시는 발표와 공식 평가를 구분하며, 모든 유체의 일반적인 닫힌 형태 해법으로 소개하지 않습니다. 자료 확인 기준: 2026년 10월 2일." },
  ];
  return <div className="cosmos-room"><div className="cosmos-intro"><span className="cosmos-kicker">06 / SIMPLE RULES, UNEXPECTED PATHS</span><h3>무질서해 보여도,<br /><em>규칙은 일을 합니다.</em></h3><p>섞임을 바꾸며 입자의 길을 관찰하세요. 선이 복잡해지는 것과 법칙이 사라지는 것은 서로 다른 일입니다.</p></div>
    <div className="cosmos-visual"><SceneCanvas scene="flow" label="시간에 따라 변하는 속도장을 따르는 600개 입자의 경로" animated={animated} value={mix} /><span className="visual-caption">발산이 0인 교육용 속도장 · Navier–Stokes 해를 계산하는 시뮬레이터는 아닙니다</span></div><label className="cosmos-slider">흐름의 섞임 <input type="range" min="0" max="2" step="0.05" value={mix} onChange={e => setMix(Number(e.target.value))} /><span>정돈된 소용돌이 ← {mix.toFixed(2)} → 복잡한 경로</span></label>
    <article className="cosmos-card"><div className="chapter-dots">{chapters.map((item, i) => <button key={item.title} aria-pressed={chapter === i} onClick={() => setChapter(i)}>{["흐름", "카오스와 엔트로피", "AI · 2025", "새 연구 · 2026"][i]}</button>)}</div><h4>{chapters[chapter].title}</h4><p>{chapters[chapter].text}</p><blockquote>자유는 규칙이 없는 곳에서만 생기지 않습니다.<br />때로는 작은 규칙이, 우리가 미처 생각하지 못한 길을 열어 줍니다.</blockquote><details><summary>이 그림의 작은 규칙 보기</summary><p className="cosmos-note">유선함수 ψ = sin(x)sin(y) + a·sin(2x + 0.7t)sin(y + 0.4t)에서 u = ∂ψ/∂y, v = −∂ψ/∂x로 속도를 정합니다. 따라서 ∂u/∂x + ∂v/∂y = 0입니다. 입자는 짧은 시간 간격으로 이 속도를 따릅니다. 점성과 압력을 푸는 유체 해석은 포함하지 않습니다.</p></details></article><Sources links={[SOURCE.fluid, SOURCE.navier, SOURCE.clay]} />
  </div>;
}
const CCC_CHAPTERS = [
  { title: "한 이온의 긴 삶", text: "CCC에서 ‘이온’은 한 우주의 긴 역사를 부르는 말입니다. 지금 우주의 팽창과 별의 역사를 출발점으로, 관측할 수 없는 아주 먼 미래까지 생각을 늘립니다.", value: 0 },
  { title: "별이 지나가고, 블랙홀도 지나가면", text: "극도로 먼 미래와 블랙홀의 증발은 이 가설의 이야기에서 중요한 자리를 차지합니다. 가설은 필요한 물리적 가정들을 함께 세워야 합니다. 이 그림은 그 미래를 관측했다는 뜻이 아닙니다.", value: 25 },
  { title: "절대 눈금을 지운 접합", text: "등각 변환은 길이의 절대 눈금을 바꾸면서 각도와 빛의 인과 구조를 보존하는 방식입니다. CCC는 이전 이온의 먼 미래를 다음 이온의 빅뱅 경계와 연결하려고 합니다. 작은 원자와 큰 우주가 실제로 같은 물체라는 뜻은 아닙니다.", value: 50 },
  { title: "다음 이온의 출발", text: "재조정된 경계 너머로 다음 우주가 이어진다는 그림입니다. 실제 공간이 물리적으로 줄었다가 튕겨 나오는 폭발을 묘사하는 것이 아닙니다. 화면의 수축·확대는 눈금을 바꾸는 과정을 표현한 비유입니다.", value: 75 },
  { title: "멋진 생각과 확인된 사실 사이", text: "로저 펜로즈의 등각 순환 우주론은 연구되는 가설이며 확립된 표준 우주론이 아닙니다. 제안된 관측 흔적과 해석도 논쟁의 대상입니다. 아름다운 질문을 품되, 증거가 어디까지인지 함께 살펴봅니다.", value: 100 },
];
function CccRoom({ animated, navigate }: { animated: boolean; navigate: Navigate }) {
  const [phase, setPhase] = useState(0), index = Math.min(4, Math.floor((phase + 12.5) / 25)), chapter = CCC_CHAPTERS[index];
  return <div className="cosmos-room"><div className="cosmos-intro"><span className="cosmos-kicker">07 / A QUESTION AT THE EDGE</span><h3>가장 큰 것과 작은 것.<br /><em>눈금을 지우면 무엇이 남을까요?</em></h3><p>로저 펜로즈의 등각 순환 우주론(CCC)을 따라가는 개념 여행입니다. <span className="cosmos-badge">연구 가설</span></p></div><div className="cosmos-visual"><SceneCanvas scene="ccc" label="이전 이온의 먼 미래와 다음 이온의 시작을 등각 재조정으로 연결하는 가설의 비유 그림" animated={animated} value={phase} /><span className="visual-caption">물리적인 우주 수축이 아닌, 눈금 재조정의 비유</span></div><label className="cosmos-slider">다음 우주의 문 <input type="range" min="0" max="100" value={phase} onChange={e => setPhase(Number(e.target.value))} /></label><div className="star-stages ccc-stages">{CCC_CHAPTERS.map((item, i) => <button key={item.title} aria-pressed={index === i} onClick={() => setPhase(item.value)}><small>0{i + 1}</small>{["이온의 역사", "먼 미래", "눈금 없는 경계", "다음 시작", "열린 질문"][i]}</button>)}</div><article className="cosmos-card"><h4>{chapter.title}</h4><p>{chapter.text}</p><blockquote>끝을 다음 시작으로 읽을 수 있을까요?<br />답을 서두르지 않는 것도, 호기심의 한 방식입니다.</blockquote><button onClick={() => navigate("scale")}>다시 우리의 눈금을 되찾기 →</button></article><Sources links={[SOURCE.ccc]} />
  </div>;
}

export default function Observatory({ onClose }: { onClose: () => void }) {
  const [room, setRoom] = useState<Room>("prism"), [animated, setAnimated] = useState(() => !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  const panel = useRef<HTMLDivElement>(null), tabs = useRef<HTMLDivElement>(null);
  const navigate: Navigate = next => { setRoom(next); panel.current?.scrollTo?.({ top: 0 }); };
  return <div className="cosmos-shell"><header className="cosmos-header"><div><span className="cosmos-kicker">PRISM / THE HIDDEN OBSERVATORY</span><h2 id="observatory-title">질서의 틈에서 만나는 우주</h2></div><div className="cosmos-header-actions"><button aria-pressed={!animated} onClick={() => setAnimated(value => !value)}>{animated ? "움직임 멈추기 Ⅱ" : "움직임 켜기 ▶"}</button><button className="cosmos-close" onClick={onClose} aria-label="관측실 닫기" autoFocus>×</button></div></header>
    <div ref={tabs} className="cosmos-tabs" role="tablist" aria-label="관측실 탐험 장소" onKeyDown={e => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return; e.preventDefault(); const current = ROOMS.findIndex(item => item.id === room), next = e.key === "Home" ? 0 : e.key === "End" ? ROOMS.length - 1 : (current + (e.key === "ArrowRight" ? 1 : -1) + ROOMS.length) % ROOMS.length; navigate(ROOMS[next].id); (tabs.current?.querySelectorAll('[role="tab"]')[next] as HTMLButtonElement)?.focus(); }}>
      {ROOMS.map((item, index) => <button role="tab" id={`cosmos-tab-${item.id}`} aria-selected={room === item.id} aria-controls="cosmos-panel" tabIndex={room === item.id ? 0 : -1} key={item.id} onClick={() => navigate(item.id)}><small>0{index + 1}</small><span>{item.name}<i>{item.hint}</i></span></button>)}
    </div><div className="cosmos-panel" id="cosmos-panel" role="tabpanel" aria-labelledby={`cosmos-tab-${room}`} ref={panel} tabIndex={0}>
      {room === "prism" && <PrismLab />}{room === "elements" && <PeriodicRoom navigate={navigate} />}{room === "clock" && <TimeRoom animated={animated} navigate={navigate} />}{room === "stars" && <StarRoom animated={animated} navigate={navigate} />}{room === "scale" && <ScaleRoom animated={animated} navigate={navigate} />}{room === "flow" && <FlowRoom animated={animated} />}{room === "ccc" && <CccRoom animated={animated} navigate={navigate} />}
      <footer className="cosmos-colophon"><span>작은 규칙. 뜻밖의 세계.</span><span>읽고, 만지고, 질문해 주세요. <b>↗</b></span></footer>
    </div></div>;
}
