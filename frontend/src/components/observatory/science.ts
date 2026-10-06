export const LIGHT_SPEED = 299_792_458;
export const PLANCK = 6.62607015e-34;
export const ELEMENTARY_CHARGE = 1.602176634e-19;
export const CESIUM_FREQUENCY = 9_192_631_770;
export const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export function photon(nanometres: number) {
  const wavelength = nanometres * 1e-9;
  return { frequency: LIGHT_SPEED / wavelength, energy: PLANCK * LIGHT_SPEED / wavelength / ELEMENTARY_CHARGE,
    band: nanometres < 380 ? "자외선" : nanometres > 780 ? "적외선" : "가시광선" };
}
export function cesiumTicks(seconds: number) { return Math.round(clamp(seconds, 0, 1) * CESIUM_FREQUENCY); }
export function wavelengthColor(nm: number) {
  if (nm < 380) return "#b797f7";
  if (nm < 450) return "#a48aff";
  if (nm < 495) return "#79a7ff";
  if (nm < 530) return "#72dbdf";
  if (nm < 580) return "#aae89a";
  if (nm < 620) return "#f1da87";
  if (nm < 680) return "#f7b18d";
  return "#ee90a3";
}
export type Point3 = [number, number, number];
export function projectPoint([x, y, z]: Point3, pitch: number, yaw: number) {
  const rx = pitch * Math.PI / 180, ry = yaw * Math.PI / 180;
  const y1 = y * Math.cos(rx) - z * Math.sin(rx), z1 = y * Math.sin(rx) + z * Math.cos(rx);
  const x2 = x * Math.cos(ry) + z1 * Math.sin(ry), z2 = -x * Math.sin(ry) + z1 * Math.cos(ry);
  const perspective = 520 / (520 + z2);
  return { x: 350 + x2 * perspective, y: 190 + y1 * perspective, z: z2 };
}

export const SCALE_STOPS = [
  { log: -18, name: "쿼크를 들여다보는 눈금", caption: "10⁻¹⁸ m 규모의 탐사. 쿼크의 실제 크기를 안다는 뜻은 아닙니다.", kind: "probe" },
  { log: -15, name: "양성자", caption: "대략 펨토미터 규모. 원자핵을 이루는 작은 구성원입니다.", kind: "quantum" },
  { log: -10, name: "원자", caption: "대략 10⁻¹⁰ m. 전자는 작은 행성 대신 확률 구름으로 표현합니다.", kind: "atom" },
  { log: -7, name: "바이러스", caption: "약 100 nm를 대표 눈금으로 골랐습니다. 종류마다 크기가 다릅니다.", kind: "virus" },
  { log: -5, name: "세포", caption: "약 10 μm. 작은 방 안에서 생명의 화학이 움직입니다.", kind: "cell" },
  { log: 0, name: "사람의 눈금", caption: "1 m. 우리가 거대한 우주를 상상하는 출발점입니다.", kind: "human" },
  { log: 7, name: "지구", caption: "지름 약 1.27 × 10⁷ m. 그림의 수치는 대표 길이를 반올림한 것입니다.", kind: "earth" },
  { log: 9, name: "태양", caption: "지름 약 1.39 × 10⁹ m. 지구보다 훨씬 넓은 별입니다.", kind: "sun" },
  { log: 13, name: "행성들이 도는 태양계", caption: "10¹³ m 눈금. 태양계의 경계는 정의에 따라 달라집니다.", kind: "solar" },
  { log: 16, name: "빛이 1년 동안 가는 길", caption: "1광년 ≈ 9.46 × 10¹⁵ m. 시간 이름을 가진 거리입니다.", kind: "stars" },
  { log: 21, name: "우리 은하", caption: "대략 10²¹ m 규모. 수많은 별이 하나의 은하를 이룹니다.", kind: "galaxy" },
  { log: 26.94, name: "관측 가능한 우주", caption: "현재의 지름 약 8.8 × 10²⁶ m. 전체 우주의 크기를 뜻하지는 않습니다.", kind: "universe" },
] as const;
export function nearestScale(log: number) { return SCALE_STOPS.reduce((best, item) => Math.abs(item.log - log) < Math.abs(best.log - log) ? item : best); }

export const SOURCE = {
  spectrum: { title: "NASA · 전자기 스펙트럼", url: "https://science.nasa.gov/ems/" },
  second: { title: "BIPM · SI를 정의하는 상수", url: "https://www.bipm.org/en/measurement-units/" },
  stars: { title: "NASA · 별의 일생", url: "https://science.nasa.gov/mission/webb/star-lifecycle/" },
  pulsar: { title: "NASA · 펄사", url: "https://science.nasa.gov/mission/hubble/science/science-behind-the-discoveries/hubble-pulsars/" },
  universe: { title: "NASA · 우주", url: "https://science.nasa.gov/universe/" },
  ccc: { title: "Meissner & Penrose · CCC 논문 (2025)", url: "https://arxiv.org/abs/2503.24263" },
  fluid: { title: "Wang 외 · 불안정 특이점 연구 (2025)", url: "https://arxiv.org/abs/2509.14185" },
  navier: { title: "OpenAI · Navier–Stokes 연구 발표 (2026.09.08)", url: "https://openai.com/index/navier-stokes-solution/" },
  clay: { title: "Clay 수학연구소 · 평가 절차 안내 (2026.09.11)", url: "https://www.claymath.org/year_type/2026/" },
  silver: { title: "연산군일기 · 1503년 5월 18일", url: "https://sillok.history.go.kr/id/kja_10905018_003" },
  argentina: { title: "아르헨티나 정부 · 국명의 유래", url: "https://www.argentina.gob.ar/pais/territorio/denominacion" },
  haber: { title: "Nobel · 하버의 암모니아 합성", url: "https://www.nobelprize.org/prizes/chemistry/1918/haber/facts/" },
  bosch: { title: "Nobel · 보슈의 산업화", url: "https://www.nobelprize.org/prizes/chemistry/1931/bosch/facts/" },
};
export type SourceLink = { title: string; url: string };

export const STAR_STAGES = [
  { title: "성운 · 흩어진 재료", text: "별은 성간 가스와 먼지가 모인 구름에서 시작합니다. 차갑고 조밀한 곳의 물질이 중력으로 모여듭니다.", note: "흩어져 있던 물질이 국소적인 구조를 만듭니다." },
  { title: "원시별 · 중력이 불을 준비한다", text: "구름이 수축하며 중심이 뜨거워집니다. 핵융합이 본격적으로 시작되기 전에는 수축이 중요한 에너지원입니다.", note: "별은 처음부터 빛나는 완성품으로 태어나지 않습니다." },
  { title: "주계열성 · 버티는 두 힘", text: "중력은 안으로, 뜨거운 내부의 압력은 밖으로 작용합니다. 중심의 수소 핵융합이 별의 오랜 삶을 지탱합니다.", note: "안정은 아무 일도 일어나지 않는 상태가 아니라, 힘이 맞서는 상태입니다." },
  { title: "적색초거성 · 내부의 층이 바뀐다", text: "질량이 큰 별은 연료가 바뀌며 크게 부풀고 여러 핵융합 단계를 거칩니다. 이 전시는 무거운 별의 대표적인 경로를 보여줍니다.", note: "태양처럼 가벼운 별의 결말은 이 경로와 다릅니다." },
  { title: "핵붕괴 초신성 · 끝에서 퍼지는 재료", text: "중심부가 지탱되지 못해 붕괴하면, 별의 바깥층이 거대한 폭발로 방출될 수 있습니다. 흩어진 물질은 다음 세대 별과 행성의 재료가 됩니다.", note: "모든 붕괴가 눈에 띄는 초신성으로 이어지는 것은 아닙니다." },
  { title: "남은 핵 · 두 갈림길", text: "남은 핵의 질량과 붕괴 과정에 따라 중성자별 또는 블랙홀이 될 수 있습니다. 아래 선택은 두 결과를 비교하는 개념적 갈림길입니다.", note: "출생 때의 질량 하나만으로 결말을 정확히 정하는 계산은 아닙니다." },
];

export const LIGHT_QUESTIONS = [
  { title: "초록의 편지", clue: "빛의 파장을 550 nm 부근에 맞춰 주세요. 초록빛 한 광자의 에너지는 얼마일까요?", target: 550, tolerance: 20, explanation: "550 nm에서는 진동수가 약 545 THz, 광자 에너지가 약 2.25 eV입니다." },
  { title: "보랏빛으로 짧아진 길", clue: "450 nm 부근으로 이동하세요. 파장이 짧아지면 광자의 에너지는 커집니다.", target: 450, tolerance: 18, explanation: "450 nm 광자는 550 nm 광자보다 에너지가 큽니다. 같은 세기나 밝기를 뜻하지는 않습니다." },
  { title: "눈에 보이지 않는 짧은 빛", clue: "300 nm 부근의 자외선을 찾아보세요. 화면의 보라색은 보이지 않는 빛을 대신하는 표시입니다.", target: 300, tolerance: 20, explanation: "자외선은 가시광선보다 파장이 짧습니다. 거짓 색으로 표시해야 화면에서 비교할 수 있습니다." },
  { title: "보이지 않는 긴 빛", clue: "1,000 nm 부근의 근적외선을 찾아보세요. 우리 눈에는 보이지 않아도 검출기는 볼 수 있습니다.", target: 1000, tolerance: 35, explanation: "근적외선은 열화상에서 주로 쓰는 더 긴 파장대와 구분됩니다. 적외선 전체가 한 종류의 열 사진은 아닙니다." },
  { title: "붉은 귀환", clue: "650 nm 부근으로 돌아오세요. 짧고 긴 빛은 서로 다른 세계를 비춥니다.", target: 650, tolerance: 20, explanation: "하나의 색에 단 하나의 파장만 있는 것은 아닙니다. 이 게임에서는 단색광의 대표 파장을 골랐습니다." },
];
