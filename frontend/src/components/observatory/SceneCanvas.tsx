import { useEffect, useRef } from "react";
import { clamp, nearestScale } from "./science";

type Scene = "nebula" | "protostar" | "sun" | "giant" | "supernova" | "neutron" | "blackhole" | "scale" | "flow" | "ccc";
type Props = { scene: Scene; label: string; animated: boolean; value?: number; spin?: number; observer?: number };
const seed = (i: number) => { const n = Math.sin(i * 127.1 + 311.7) * 43758.5453; return n - Math.floor(n); };
const TAU = Math.PI * 2;

function glow(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, inner: string, outer = "#0d112000") {
  const gradient = ctx.createRadialGradient(x, y, 0, x, y, r);
  gradient.addColorStop(0, inner); gradient.addColorStop(1, outer);
  ctx.fillStyle = gradient; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
}
function stars(ctx: CanvasRenderingContext2D, w: number, h: number, time: number) {
  for (let i = 0; i < 155; i++) {
    const a = .18 + seed(i + 1024) * .55 + Math.sin(time * .6 + i) * .06;
    ctx.fillStyle = `rgba(209,221,248,${a})`; ctx.beginPath();
    ctx.arc(seed(i) * w, seed(i + 201) * h, .45 + seed(i + 52) * 1.05, 0, TAU); ctx.fill();
  }
}
function star(ctx: CanvasRenderingContext2D, w: number, h: number, time: number, giant: boolean) {
  const x = w / 2, y = h / 2, r = Math.min(w, h) * (giant ? .26 : .15);
  glow(ctx, x, y, r * 3.1, giant ? "#c7623f60" : "#efad454a");
  glow(ctx, x, y, r, giant ? "#ffe5aa" : "#fff3ce", giant ? "#a93428" : "#e19f45");
  ctx.save(); ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.clip();
  for (let i = 0; i < 60; i++) {
    const px = x + (seed(i + 381) * 2 - 1) * r, py = y + (seed(i + 871) * 2 - 1) * r;
    glow(ctx, px + Math.sin(time * .25 + i) * 3, py, r * (.07 + seed(i) * .18), "#a44a2628");
  }
  ctx.restore();
  for (let i = 0; i < 18; i++) {
    const angle = i * TAU / 18 + time * .02;
    ctx.strokeStyle = "#f2bd6045"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(x + Math.cos(angle) * r, y + Math.sin(angle) * r, r * .14, angle, angle + Math.PI); ctx.stroke();
  }
}
function galaxy(ctx: CanvasRenderingContext2D, w: number, h: number, time: number, many = false) {
  const count = many ? 22 : 1;
  for (let j = 0; j < count; j++) {
    const x = many ? seed(j + 488) * w : w / 2, y = many ? seed(j + 12) * h : h / 2;
    const size = many ? 12 + seed(j + 139) * 30 : Math.min(w, h) * .42;
    ctx.save(); ctx.translate(x, y); ctx.rotate(-.35 + seed(j) * .5); ctx.scale(1, .55);
    glow(ctx, 0, 0, size * .9, "#afa0e737");
    for (let i = 0; i < (many ? 100 : 1500); i++) {
      const radius = Math.sqrt(seed(i + j * 211)) * size;
      const angle = radius / size * 9 + (i % 3) * TAU / 3 + (seed(i + 622) - .5) * .5 + time * .025;
      ctx.fillStyle = i % 5 === 0 ? "#ebc995ba" : "#9fbbe98c";
      ctx.beginPath(); ctx.arc(Math.cos(angle) * radius, Math.sin(angle) * radius, many ? .6 : .5 + seed(i) * .9, 0, TAU); ctx.fill();
    }
    glow(ctx, 0, 0, size * .16, "#fff0cfed"); ctx.restore();
  }
}
function neutron(ctx: CanvasRenderingContext2D, w: number, h: number, time: number, spin: number, observer: number) {
  const x = w / 2, y = h / 2, r = Math.min(w, h) * .055, angle = time * spin;
  for (let i = 0; i < 2; i++) {
    const a = angle + i * Math.PI;
    ctx.save(); ctx.translate(x, y); ctx.rotate(a); ctx.scale(1, .7);
    const gradient = ctx.createLinearGradient(0, 0, w * .4, 0);
    gradient.addColorStop(0, "#9fe2ff88"); gradient.addColorStop(1, "#9fe2ff00");
    ctx.fillStyle = gradient; ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(w * .43, -h * .16); ctx.lineTo(w * .43, h * .16); ctx.closePath(); ctx.fill(); ctx.restore();
  }
  for (let i = 1; i <= 4; i++) {
    ctx.strokeStyle = "#94c6fb27"; ctx.lineWidth = 1; ctx.beginPath(); ctx.ellipse(x, y, r * (2 + i), r * (1.5 + i * 2), .4, 0, TAU); ctx.stroke();
  }
  glow(ctx, x, y, r * 4, "#9ad9ff66"); glow(ctx, x, y, r, "#fffefb", "#80b3d1");
  const view = observer * Math.PI / 180, aligned = Math.abs(Math.cos(angle - view)) > .96;
  const ox = x + Math.cos(view) * w * .35, oy = y + Math.sin(view) * h * .32;
  ctx.strokeStyle = "#cce2e980"; ctx.setLineDash([3, 5]); ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(ox, oy); ctx.stroke(); ctx.setLineDash([]);
  glow(ctx, ox, oy, aligned ? 20 : 8, aligned ? "#eeffef" : "#779db2");
  ctx.font = "12px sans-serif"; ctx.fillStyle = "#c4d2e5"; ctx.fillText(aligned ? "지구에 빛이 도착!" : "지구의 관측 방향", ox - 50, oy + 30);
  ctx.strokeStyle = "#9edee6"; ctx.beginPath();
  for (let i = 0; i < 220; i++) { const phase = time * spin - (220 - i) / 25; const pulse = Math.pow(Math.abs(Math.cos(phase - view)), 60);
    const px = 28 + i * (w - 56) / 220, py = h - 50 - pulse * 33; if (!i) ctx.moveTo(px, py); else ctx.lineTo(px, py); }
  ctx.stroke();
}
function blackhole(ctx: CanvasRenderingContext2D, w: number, h: number, time: number) {
  const x = w / 2, y = h / 2, r = Math.min(w, h) * .14;
  glow(ctx, x, y, r * 3.5, "#b7805750");
  ctx.save(); ctx.translate(x, y); ctx.rotate(-.16);
  for (let j = 0; j < 38; j++) {
    const radius = r * (1.35 + j * .042); ctx.strokeStyle = `rgba(255,${Math.round(185 - j * 1.8)},${Math.round(102 - j)},${.14 + (38 - j) / 80})`;
    ctx.lineWidth = 1.4; ctx.beginPath(); ctx.ellipse(0, 0, radius * 1.75, radius * .24, 0, Math.PI, TAU); ctx.stroke();
  }
  ctx.fillStyle = "#05070f"; ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.fill();
  ctx.strokeStyle = "#fff0c8bd"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(0, 0, r * 1.03, 0, TAU); ctx.stroke();
  for (let j = 0; j < 30; j++) {
    ctx.strokeStyle = `rgba(255,${210 - j * 3},${160 - j * 3},${.28 - j * .006})`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.ellipse(0, 0, r * (1.13 + j * .035), r * (1.13 + j * .026), 0, Math.PI, TAU); ctx.stroke();
    ctx.beginPath(); ctx.ellipse(0, 0, r * (1.4 + j * .047) * 1.75, r * (1.4 + j * .047) * .24, 0, 0, Math.PI); ctx.stroke();
  }
  for (let i = 0; i < 140; i++) { const a = seed(i) * TAU + time * (.12 + seed(i + 8) * .1), radius = r * (1.5 + seed(i + 111) * .8);
    ctx.fillStyle = "#ffd19370"; ctx.fillRect(Math.cos(a) * radius * 1.75, Math.sin(a) * radius * .24, 1.5, 1.5); }
  ctx.restore();
}
function particleCloud(ctx: CanvasRenderingContext2D, w: number, h: number, time: number, collapse: boolean) {
  const x = w / 2, y = h / 2, size = Math.min(w, h) * .37;
  for (let i = 0; i < 180; i++) {
    const angle = seed(i + 451) * TAU + time * .018;
    const radius = Math.sqrt(seed(i + 383)) * size * (collapse ? .68 : 1);
    const px = x + Math.cos(angle) * radius * 1.4, py = y + Math.sin(angle) * radius * .75;
    glow(ctx, px, py, 16 + seed(i) * 42, i % 3 ? "#9260b308" : "#4c8dad16");
  }
  if (collapse) { glow(ctx, x, y, size * .6, "#eda06395"); glow(ctx, x, y, size * .12, "#fff4d6", "#ee906480"); }
}
function supernova(ctx: CanvasRenderingContext2D, w: number, h: number, time: number) {
  const x = w / 2, y = h / 2, size = Math.min(w, h) * .35, phase = .25 + ((time * .13 + .55) % 1) * .9;
  for (let i = 0; i < 700; i++) {
    const angle = seed(i + 851) * TAU, r = size * (.25 + seed(i + 514) * .8) * phase;
    const px = x + Math.cos(angle) * r * 1.25, py = y + Math.sin(angle) * r;
    ctx.strokeStyle = i % 3 ? "#89bade40" : "#e9aa764f"; ctx.lineWidth = .5 + seed(i + 416) * 2;
    ctx.beginPath(); ctx.moveTo(x + (px - x) * .8, y + (py - y) * .8); ctx.lineTo(px, py); ctx.stroke();
  }
  glow(ctx, x, y, size * .35, "#fff1deed"); glow(ctx, x, y, size * .15, "#ffffff");
}
function earth(ctx: CanvasRenderingContext2D, w: number, h: number, time: number) {
  const x = w / 2, y = h / 2, r = Math.min(w, h) * .27;
  glow(ctx, x, y, r * 1.15, "#478ce850"); glow(ctx, x, y, r, "#77b8d7", "#20365e");
  ctx.save(); ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.clip();
  for (let i = 0; i < 130; i++) { const px = x + ((seed(i + 87) + time * .004) % 1 * 2 - 1) * r, py = y + (seed(i + 292) * 2 - 1) * r;
    glow(ctx, px, py, 8 + seed(i + 34) * 27, i % 3 === 0 ? "#c6ddde65" : "#57998375"); }
  const shade = ctx.createLinearGradient(x - r, y, x + r, y); shade.addColorStop(0, "#00000000"); shade.addColorStop(1, "#030918c0"); ctx.fillStyle = shade; ctx.fillRect(x - r, y - r, r * 2, r * 2); ctx.restore();
}
function micro(ctx: CanvasRenderingContext2D, w: number, h: number, time: number, kind: string) {
  const x = w / 2, y = h / 2, r = Math.min(w, h) * .28;
  if (kind === "atom") {
    for (let i = 0; i < 1200; i++) { const angle = seed(i + 883) * TAU + time * .012, radius = Math.sqrt(seed(i + 984)) * r;
      ctx.fillStyle = `rgba(129,167,227,${.04 + (1 - radius / r) * .13})`; ctx.fillRect(x + Math.cos(angle) * radius * 1.2, y + Math.sin(angle) * radius, 1.8, 1.8); }
    glow(ctx, x, y, 9, "#ffcfad");
  } else if (kind === "probe") {
    ctx.setLineDash([3, 8]); ctx.strokeStyle = "#ad9edb70"; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = "#abc7e866"; ctx.beginPath(); ctx.moveTo(x - r * 1.2, y); ctx.lineTo(x + r * 1.2, y); ctx.moveTo(x, y - r * 1.2); ctx.lineTo(x, y + r * 1.2); ctx.stroke();
    glow(ctx, x, y, r * .15, "#c9b4e9a0"); ctx.font = "11px sans-serif"; ctx.fillStyle = "#b1bdd2"; ctx.textAlign = "center"; ctx.fillText("탐사 눈금 · 실제 크기나 내부 구조를 그린 것이 아님", x, y + r + 30); ctx.textAlign = "left";
  } else if (kind === "virus") {
    glow(ctx, x, y, r * .72, "#c4aedaaa", "#686ba733");
    for (let i = 0; i < 24; i++) { const a = i / 24 * TAU + time * .04; ctx.strokeStyle = "#b9addb"; ctx.beginPath(); ctx.moveTo(x + Math.cos(a) * r * .7, y + Math.sin(a) * r * .7); ctx.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r); ctx.stroke(); glow(ctx, x + Math.cos(a) * r, y + Math.sin(a) * r, 4, "#c4aedb"); }
    ctx.strokeStyle = "#a4d5d0"; ctx.beginPath(); for (let i = 0; i < 110; i++) { const a = i * .1; const px = x + Math.cos(a) * r * .36, py = y + Math.sin(a * 2.5) * r * .18; if (!i) ctx.moveTo(px, py); else ctx.lineTo(px, py); } ctx.stroke();
  } else if (kind === "cell") {
    glow(ctx, x, y, r, "#77cba24d", "#2b795031"); ctx.strokeStyle = "#98dcbe80"; ctx.beginPath();
    for (let i = 0; i <= 100; i++) { const a = i / 100 * TAU, rad = r * (1 + Math.sin(a * 7 + time * .2) * .035); const px = x + Math.cos(a) * rad * 1.2, py = y + Math.sin(a) * rad; if (!i) ctx.moveTo(px, py); else ctx.lineTo(px, py); } ctx.stroke();
    glow(ctx, x - r * .25, y + r * .12, r * .36, "#b7a5d7b0", "#7a5d8f22");
  } else {
    for (let i = 0; i < 3; i++) { const a = i / 3 * TAU + time * .03; const px = x + Math.cos(a) * r * .26, py = y + Math.sin(a) * r * .26;
      glow(ctx, px, py, r * .5, ["#ad95e380", "#82cad180", "#eab27e80"][i]); }
    ctx.font = "11px sans-serif"; ctx.fillStyle = "#b1bdd2"; ctx.textAlign = "center"; ctx.fillText("내부 구성의 개념 표현", x, y + r + 35); ctx.textAlign = "left";
  }
}
function scaleScene(ctx: CanvasRenderingContext2D, w: number, h: number, time: number, value: number) {
  const stop = nearestScale(value);
  const factor = clamp(Math.pow(10, (stop.log - value) * .17), .18, 2.5);
  ctx.save(); ctx.translate(w / 2, h / 2); ctx.scale(factor, factor); ctx.translate(-w / 2, -h / 2);
  if (stop.kind === "sun") star(ctx, w, h, time, false);
  else if (stop.kind === "earth") earth(ctx, w, h, time);
  else if (stop.kind === "galaxy" || stop.kind === "universe") galaxy(ctx, w, h, time, stop.kind === "universe");
  else if (stop.kind === "solar") {
    glow(ctx, w / 2, h / 2, 25, "#ffdf93");
    for (let i = 1; i < 7; i++) { const r = i * Math.min(w, h) * .06; ctx.strokeStyle = "#a8b0d232"; ctx.beginPath(); ctx.ellipse(w / 2, h / 2, r * 1.6, r * .8, -.2, 0, TAU); ctx.stroke();
      const a = i * 3 + time * .1 / i; glow(ctx, w / 2 + Math.cos(a) * r * 1.6, h / 2 + Math.sin(a) * r * .8, 4, "#bfd1e8"); }
  } else if (stop.kind === "stars") { glow(ctx, w * .25, h / 2, 11, "#fff5b2"); glow(ctx, w * .75, h / 2, 8, "#d1ecff"); ctx.strokeStyle = "#d3dae360"; ctx.setLineDash([4, 7]); ctx.beginPath(); ctx.moveTo(w * .25, h / 2); ctx.lineTo(w * .75, h / 2); ctx.stroke(); ctx.setLineDash([]); }
  else if (stop.kind === "human") { const x = w / 2, y = h / 2; ctx.strokeStyle = "#d4cbe9"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y - 74, 14, 0, TAU); ctx.moveTo(x, y - 58); ctx.lineTo(x, y + 12); ctx.moveTo(x - 35, y - 30); ctx.lineTo(x + 35, y - 30); ctx.moveTo(x, y + 12); ctx.lineTo(x - 25, y + 78); ctx.moveTo(x, y + 12); ctx.lineTo(x + 25, y + 78); ctx.stroke(); }
  else micro(ctx, w, h, time, stop.kind);
  ctx.restore();
}
function ccc(ctx: CanvasRenderingContext2D, w: number, h: number, time: number, value: number) {
  const progress = value / 100, join = Math.abs(progress - .5) * 2, x = w / 2, y = h / 2;
  const radius = Math.min(w, h) * (.04 + join * .35);
  // Two differently scaled copies expose what the central metaphor preserves.
  if (join < .65) {
    const points = [[-.6, -.3], [.4, -.6], [.7, .3], [-.1, .7], [-.6, -.3]];
    for (let side = 0; side < 2; side++) {
      const cx = w * (side ? .82 : .18), scale = Math.min(w, h) * (side ? .15 : .085);
      ctx.strokeStyle = side ? "#93d4dfaa" : "#c3a9e9aa"; ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = 1;
      ctx.beginPath(); points.forEach(([px, py], i) => { if (!i) ctx.moveTo(cx + px * scale, y + py * scale); else ctx.lineTo(cx + px * scale, y + py * scale); }); ctx.stroke();
      points.slice(0, 4).forEach(([px, py]) => { ctx.beginPath(); ctx.arc(cx + px * scale, y + py * scale, 3, 0, TAU); ctx.fill(); });
      ctx.font = "10px sans-serif"; ctx.textAlign = "center"; ctx.fillText(side ? "다른 눈금 · 같은 각도" : "절대 눈금 재조정", cx, y + scale + 23); ctx.textAlign = "left";
    }
    ctx.strokeStyle = "#c0c0df44"; ctx.setLineDash([2, 7]); ctx.beginPath(); ctx.moveTo(w * .29, y); ctx.lineTo(w * .41, y); ctx.moveTo(w * .59, y); ctx.lineTo(w * .69, y); ctx.stroke(); ctx.setLineDash([]);
  }
  for (let i = 0; i < 320; i++) {
    const a = seed(i + 45) * TAU + time * .04, r = Math.sqrt(seed(i + 727)) * radius;
    ctx.fillStyle = progress < .5 ? "#c7b3ef99" : "#8fd4dc99"; ctx.beginPath(); ctx.arc(x + Math.cos(a) * r * 1.6, y + Math.sin(a) * r, .7 + join * .7, 0, TAU); ctx.fill();
  }
  glow(ctx, x, y, radius * .8 + 20, progress < .5 ? "#8d6fcc50" : "#e4bd7750");
  ctx.strokeStyle = "#c4b6e962"; ctx.lineWidth = 1;
  for (let i = 0; i < 8; i++) { const a = i / 8 * TAU; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + Math.cos(a) * radius * 1.6, y + Math.sin(a) * radius); ctx.stroke(); }
  ctx.setLineDash([3, 6]); ctx.strokeStyle = "#d1d7e443"; ctx.beginPath(); ctx.moveTo(x, 25); ctx.lineTo(x, h - 25); ctx.stroke(); ctx.setLineDash([]);
  ctx.textAlign = "center"; ctx.fillStyle = "#d1d5e6"; ctx.font = "12px sans-serif";
  ctx.fillText(join < .16 ? "눈금이 사라지는 접합 · 가설" : progress < .5 ? "이전 이온의 먼 미래" : "다음 이온의 시작", x, h - 52); ctx.textAlign = "left";
}

export default function SceneCanvas({ scene, label, animated, value = 0, spin = 1, observer = 0 }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const clock = useRef(0);
  useEffect(() => {
    const element = canvas.current!;
    const ctx = element.getContext("2d");
    if (!ctx) return;
    let frame = 0, width = 800, height = 420, previous = 0, disposed = false;
    const particles = Array.from({ length: 600 }, (_, i) => ({ x: (seed(i + 320) * 2 - 1) * 3.2, y: (seed(i + 749) * 2 - 1) * 2.1 }));
    const draw = (stamp: number) => {
      if (disposed) return;
      const dt = previous ? Math.min((stamp - previous) / 1000, .04) : 0; previous = stamp;
      if (animated) clock.current += dt;
      const t = clock.current;
      ctx.fillStyle = scene === "flow" && animated && dt ? "#10152112" : "#101521"; ctx.fillRect(0, 0, width, height);
      if (scene !== "flow") stars(ctx, width, height, t);
      if (scene === "nebula" || scene === "protostar") particleCloud(ctx, width, height, t, scene === "protostar");
      else if (scene === "sun" || scene === "giant") star(ctx, width, height, t, scene === "giant");
      else if (scene === "supernova") supernova(ctx, width, height, t);
      else if (scene === "neutron") neutron(ctx, width, height, t, spin, observer);
      else if (scene === "blackhole") blackhole(ctx, width, height, t);
      else if (scene === "scale") scaleScene(ctx, width, height, t, value);
      else if (scene === "ccc") ccc(ctx, width, height, t, value);
      else if (scene === "flow") {
        for (let i = 0; i < particles.length; i++) {
          const point = particles[i], oldX = point.x, oldY = point.y;
          const mixing = value, u = Math.sin(oldX) * Math.cos(oldY) + mixing * Math.sin(2 * oldX + t * .7) * Math.cos(oldY + t * .4);
          const v = -Math.cos(oldX) * Math.sin(oldY) - 2 * mixing * Math.cos(2 * oldX + t * .7) * Math.sin(oldY + t * .4);
          const step = animated ? dt * .9 : .015;
          point.x += u * step; point.y += v * step;
          if (Math.abs(point.x) > 3.4 || Math.abs(point.y) > 2.3) { point.x = oldX * .96; point.y = oldY * .96; continue; }
          ctx.strokeStyle = ["#a494df99", "#8ccfd299", "#e3b17d99", "#9db89699"][i % 4]; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(width / 2 + oldX * width / 7, height / 2 + oldY * height / 4.8); ctx.lineTo(width / 2 + point.x * width / 7, height / 2 + point.y * height / 4.8); ctx.stroke();
          if (!animated) { ctx.fillStyle = ctx.strokeStyle; ctx.fillRect(width / 2 + point.x * width / 7, height / 2 + point.y * height / 4.8, 2, 2); }
        }
      }
      if (animated) frame = requestAnimationFrame(draw);
    };
    const resize = () => {
      const rect = element.getBoundingClientRect(), dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width || 800; height = rect.height || 420;
      element.width = Math.round(width * dpr); element.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); if (!animated) draw(0);
    };
    resize();
    const resizeObserver = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    resizeObserver?.observe(element);
    if (animated) frame = requestAnimationFrame(draw); else draw(0);
    return () => { disposed = true; cancelAnimationFrame(frame); resizeObserver?.disconnect(); };
  }, [scene, animated, value, spin, observer]);
  return <canvas ref={canvas} className="cosmos-canvas" role="img" aria-label={label}>{label}</canvas>;
}
