import { useEffect, useId, useState } from "react";
import { atomPosition, JOURNEY_ATOMS, JOURNEY_END, JOURNEY_STAGES, journeyPosition } from "./journeyGeometry";
import { Sources } from "./shared";

const HUMAN = "M400 43a39 39 0 1 0 0 78a39 39 0 1 0 0-78ZM364 136q-25 3-33 33l-19 63q-2 14 9 16q10 1 14-12l18-50 4 66-12 65q-2 17 12 19q12 0 16-15l19-62h16l19 62q4 15 16 15q14-2 12-19l-12-65 4-66 18 50q4 13 14 12q11-2 9-16l-19-63q-8-30-33-33Z";
function Silhouette({ stage }: { stage: number }) {
  switch (stage) {
    case 0: return <><circle cx="400" cy="176" r="124" /><circle cx="400" cy="176" r="140" strokeDasharray="2 11" />{Array.from({ length: 12 }, (_, i) => <path key={i} d="M400 20V7" transform={`rotate(${i * 30} 400 176)`} />)}</>;
    case 1: return <>{[100, 166, 244].map(y => <path key={y} d={`M85 ${y}Q210 ${y - 65} 400 ${y}T700 ${y}q50 0 34-25q-13-13-24 0`} />)}</>;
    case 2: return <><path d="M400 45q-25 65-81 123q-67 102 18 136q62 39 131-1q84-38 15-134Q433 104 400 45Z" /><path d="M195 308q100-15 205 0t205 0" /></>;
    case 3: return <>{[195, 299, 403, 507, 611].map((x, i) => <g key={x}><path d={`M${x} 310Q${x + (i % 2 ? -45 : 45)} 210 ${x} 80m0 140q-50-80-75-60q0 45 75 60m0-40q58-77 76-51q-10 43-76 51`} /></g>)}</>;
    case 4: return <path d="M241 282q-34-54 9-92q30-47 91-42q101-6 127 64q3-50 31-72l-6-66q1-38 17-31q18 4 18 81l21-78q17-22 21-4q3 32-16 92q32 23 13 54l-27 28q-17 19-42 21q-13 44-61 59h-52q-27-14 2-30l31-8q-76 27-74 38h-80q-28 1-21-14Z" />;
    case 5: return <><path d="M150 282v-90q0-13 15-18l25-11-23-25q20-65-36-83q-56-12-76 38q-13 63 27 99v90m568 0v-90q0-13-15-18l-25-11 23-25q-20-65 36-83q56-12 76 38q13 63-27 99v90" />{[140, 178, 212].map(y => <path key={y} d={`M203 ${y}q78-28 137 0t127 0t128 0`} strokeDasharray="3 9" />)}</>;
    case 6: return <><path d={HUMAN} transform="translate(-57 23) scale(.8)" /><path d={HUMAN} transform="translate(203 23) scale(.8)" /><path d="M290 310q110 25 220 0" strokeDasharray="3 10" /></>;
    case 7: return <><path d={HUMAN} /><path d="M400 12v20m0 314v10" /><ellipse cx="400" cy="340" rx="94" ry="5" /></>;
    case 8: return <><path d="M103 244q87-24 163 0t178 0t166 0t94 0m-557 54q105-28 200 0t160 0t145 0" /><path d="m330 250-30 53 16 18m140-72 28 57-15 16m-108-52 24 60" strokeDasharray="3 8" /></>;
    default: return <><path d="M400 310V174q-94-2-127-59q-14-34 18-30q75 5 109 89q34-84 109-89q32-4 18 30q-33 57-127 59m0 137-25 25m25-25 25 25" /><ellipse cx="400" cy="331" rx="124" ry="10" /></>;
  }
}

export default function AtomJourney({ animated }: { animated: boolean }) {
  const [phase, setPhase] = useState(7), [playing, setPlaying] = useState(false), [selected, setSelected] = useState(17);
  const [perspective, setPerspective] = useState<"moment" | "time">("moment");
  const glow = useId(), index = Math.round(phase), stage = JOURNEY_STAGES[index];
  const current = journeyPosition(selected, phase);
  useEffect(() => {
    if (!playing || !animated) return;
    let frame = 0, previous: number | undefined;
    const tick = (time: number) => {
      const elapsed = previous === undefined ? 0 : (time - previous) / 3800; previous = time;
      setPhase(value => Math.min(JOURNEY_END, value + elapsed));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, animated]);
  useEffect(() => { if (phase >= JOURNEY_END) setPlaying(false); }, [phase]);
  const move = (value: number) => { setPlaying(false); setPhase(value); };
  const selectedPath = JOURNEY_STAGES.map((_, i) => atomPosition(selected, i));
  return <div className="cosmos-room atom-journey">
    <div className="cosmos-intro"><span className="cosmos-kicker">03 / BORROWED MATTER, A PASSING NAME</span><h3>우리는 잠시,<br /><em>나라는 모양.</em></h3><p>시간의 눈금을 움직여 보세요. 같은 점들이 다른 자리로 흩어지고 모입니다. 금빛 점 하나를 따라가면, 경계보다 오래 이어지는 재료의 길이 보입니다.</p></div>
    <section className="cosmos-card journey-explorer" aria-label="원자의 시간 여행">
      <div className="journey-heading"><span className="cosmos-kicker">C / 탄소의 가능한 여정</span><div className="cosmos-controls" role="group" aria-label="원자를 바라보는 관점"><button aria-pressed={perspective === "moment"} onClick={() => setPerspective("moment")}>지금의 모양</button><button aria-pressed={perspective === "time"} onClick={() => setPerspective("time")}>시간의 눈으로</button></div></div>
      <div className="journey-scene-layout"><div className="journey-visual"><span className="journey-view-label">{perspective === "moment" ? "한 순간에 머물러 보면" : "한 원자의 길을 이어 보면"}</span><svg viewBox="0 0 800 360" role="img" aria-label={`${stage.name} 모습으로 모인 탄소 원자 96개의 개념 그림 · 선택한 점 ${selected + 1}`}>
        <defs><radialGradient id={glow}><stop stopColor="#a98fd2" stopOpacity=".14" /><stop offset="1" stopColor="#101521" stopOpacity="0" /></radialGradient></defs><ellipse cx="400" cy="183" rx="230" ry="176" fill={`url(#${glow})`} />
        {Array.from({ length: 30 }, (_, i) => <circle key={i} cx={(i * 137 + 41) % 790} cy={(i * 61 + 27) % 350} r={i % 3 ? .6 : 1.1} fill="#a8b9cf" opacity=".22" />)}
        <g className="journey-silhouettes" fill="none" stroke="#aca0c2" strokeWidth="1.3">{JOURNEY_STAGES.map((item, i) => <g key={item.name} style={{ opacity: Math.max(0, 1 - Math.abs(phase - i)), transition: animated && !playing ? "opacity .9s ease" : "none" }}><Silhouette stage={i} /></g>)}</g>
        {perspective === "time" && <g fill="none" stroke="#d6b783" opacity=".35"><path d={selectedPath.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.y}`).join(" ")} strokeDasharray="3 7" />{selectedPath.map((p, i) => <circle key={i} cx={p.x} cy={p.y} r="5" />)}</g>}
        <g aria-hidden="true">{Array.from({ length: JOURNEY_ATOMS }, (_, i) => { const p = journeyPosition(i, phase); return <g key={i} data-atom-id={i} className="journey-atom" onClick={() => setSelected(i)} style={{ transform: `translate(${p.x}px, ${p.y}px)`, transition: animated && !playing ? "transform .9s ease" : "none" }}><circle r="12" fill="transparent" /><circle r={i === selected ? 7 : 3.5} fill={i === selected ? "#edd3a2" : i % 3 ? "#b4c6d7" : "#bc9ed6"} opacity={i === selected ? 1 : .72} />{i === selected && <circle r="12" fill="none" stroke="#edd3a2" strokeOpacity=".55" />}</g>; })}</g>
        <g className="journey-atom-tag" aria-hidden="true" style={{ transform: `translate(${current.x}px, ${current.y}px)`, transition: animated && !playing ? "transform .9s ease" : "none" }}><text x="15" y="-15">C · {String(selected + 1).padStart(3, "0")}</text></g>
      </svg></div><article className="journey-stage-story"><span className="cosmos-kicker">{perspective === "moment" ? "A MOMENT / 지금의 모양" : "A CONTINUITY / 이어지는 재료"}</span><h4>{stage.title}</h4><p className="journey-verse">{stage.verse.replace(" / ", "\n")}</p><p>{stage.detail}</p></article></div>
      <div className="journey-readout" aria-live={playing ? "off" : "polite"}><span>{String(index + 1).padStart(2, "0")} / {JOURNEY_STAGES.length}</span><strong>{stage.name}</strong><p>{stage.form}</p></div>
      <label className="cosmos-slider">원자의 시간축 <input aria-label="원자의 시간축" type="range" min="0" max={JOURNEY_END} step="0.01" value={phase} onChange={e => move(Number(e.target.value))} aria-valuetext={stage.name} /><span>먼 별의 과거 <b>‘지금’은 길 위의 한 눈금</b> 열린 미래</span></label>
      <div className="journey-stops">{JOURNEY_STAGES.map((item, i) => <button key={item.name} aria-label={`${i + 1}장 ${item.name}`} aria-pressed={index === i} onClick={() => move(i)}><small>{String(i + 1).padStart(2, "0")}</small>{item.name}</button>)}</div>
      <div className="cosmos-controls journey-play"><button className="cosmos-action" disabled={!animated} onClick={() => { setPhase(0); setPerspective("time"); setPlaying(true); }}>별에서부터 재생 ▶</button><button disabled={!playing} onClick={() => setPlaying(false)}>여정 잠시 멈추기 Ⅱ</button><button onClick={() => move(7)}>지금의 나로 돌아오기 ↗</button></div>
      {!animated && <p className="cosmos-note">움직임이 멈춰 있습니다. 시간축과 장면 버튼으로 직접 넘겨 볼 수 있습니다.</p>}
      <div className="journey-tracker"><label>따라갈 금빛 점 <input type="range" min="1" max={JOURNEY_ATOMS} value={selected + 1} onChange={e => setSelected(Number(e.target.value) - 1)} /></label><span>C · {String(selected + 1).padStart(3, "0")} / 같은 점, 다른 자리</span></div>
      <p className="cosmos-note journey-caption">점들은 탄소를 상징합니다. 장면 사이에는 서로 다른 시간 규모와 많은 과정이 생략되어 있습니다. 눈금은 연대가 아닌 이야기의 순서이며, 한 사람의 원자를 실제로 추적한 기록은 아닙니다.</p>
    </section>
    <div className="journey-reading">
      <article className="cosmos-card journey-poem"><span className="cosmos-kicker">범아일여를 떠올리며 · 창작</span><h4>지금으로 읽으면 나.<br />시간으로 읽으면 세계.</h4><p>나는 잠시, 나라는 모양.<br />별의 재료가 바람을 건너,<br />물의 흐름과 풀의 초록을 지나,<br />누군가의 숨에서 내 숨으로 온다.</p><p>할아버지와 할머니도<br />이 세계의 재료로 잠시 살았다.<br />내가 떠난 자리에는<br />아직 이름 없는 생명의 가능성이 남는다.</p><p className="journey-poem-ending">너와 나의 경계를,<br />시간은 다른 눈으로 읽는다.</p></article></div>
    <details className="journey-context"><summary>별의 재료, 생명의 순환, 범아일여를 함께 읽기</summary><p>‘별에서 왔다’는 말에는 탄소·산소 같은 원소의 역사가 담겨 있습니다. 우리 몸의 수소 대부분은 별보다 앞선 초기 우주에서 유래합니다. 지구의 탄소 순환에서는 결합 상대와 물질의 형태가 달라져도 탄소가 다른 원소로 바뀌지는 않습니다.</p><p>범아일여는 개인의 참된 자아인 아트만과 궁극적 실재인 브라만의 동일성을 말하는 철학적 사유입니다. 여기의 시는 그 사유에서 영감을 받아 시간과 연결을 바라봅니다. 물질의 순환이 그 사유나 인격의 재탄생을 과학적으로 증명한다는 뜻은 아닙니다.</p></details>
    <Sources links={[{ title: "NASA · 우리를 이루는 별의 재료", url: "https://science.nasa.gov/astrobiology/learning-resources/alp/are-we-really-made-of-star-stuff/" }, { title: "NOAA · 탄소의 순환", url: "https://oceanservice.noaa.gov/facts/carbon-cycle.html" }, { title: "Stanford Encyclopedia of Philosophy · Śaṅkara", url: "https://plato.stanford.edu/entries/shankara/" }]} />
  </div>;
}
