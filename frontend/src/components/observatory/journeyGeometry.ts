/** A possible carbon journey, not the measured history of a person's atoms. */
export const JOURNEY_STAGES = [
  { name: "별", title: "내 안의 탄소에는, 별의 시간이 있습니다.", verse: "먼 별의 속에서 / 아직 내 이름은 없었습니다.", detail: "우리 몸의 탄소와 산소 같은 원소는 별의 역사에서 만들어졌습니다. 별이 내보낸 물질은 다음 세대 별과 행성의 재료가 됩니다.", form: "별에서 만들어진 탄소" },
  { name: "바람", title: "붙잡을 수 없던 것이, 나의 재료였습니다.", verse: "어느 날에는 바람. / 어디에도 머물지 않는 듯, 세계를 건넜습니다.", detail: "탄소는 이산화탄소 같은 분자에 들어 대기 중을 이동할 수 있습니다. 바람이 그 분자들을 옮깁니다.", form: "대기 중 이산화탄소의 탄소" },
  { name: "물", title: "흐르는 물은, 다른 재료도 품고 갑니다.", verse: "어느 날에는 물의 흐름. / 나를 품은 강은 이름 없이 흘렀습니다.", detail: "물에는 이산화탄소와 탄소를 포함한 여러 물질이 녹아 있을 수 있습니다. 여기의 점은 물 분자 H₂O 자체가 아니라, 물속 탄소를 나타냅니다.", form: "물에 녹아 있는 탄소 물질" },
  { name: "풀", title: "햇빛을 받은 풀 안에서, 자리를 바꿉니다.", verse: "어느 날에는 풀의 초록. / 빛을 받아, 살아 있는 모양이 되었습니다.", detail: "식물은 광합성으로 이산화탄소의 탄소를 유기물에 담습니다. 원소의 이름을 바꾸는 대신, 결합과 구조를 바꾸는 일입니다.", form: "식물의 유기물에 들어 있는 탄소" },
  { name: "동물", title: "한 생명의 식사가, 다른 생명의 몸이 됩니다.", verse: "어느 날에는 작은 동물. / 다른 생명을 먹고, 다른 생명을 살렸습니다.", detail: "먹이 관계를 통해 탄소는 생물 사이를 이동합니다. 몸의 일부가 되기도 하고, 호흡이나 배설을 통해 다시 환경으로 나가기도 합니다.", form: "먹이와 동물의 몸속 탄소" },
  { name: "숨결", title: "너와 나 사이에, 보이지 않는 길이 있습니다.", verse: "어느 날에는 누군가의 숨결. / 경계는 날숨보다 오래 머물지 못했습니다.", detail: "호흡으로 내보낸 이산화탄소는 대기와 섞이고 식물에 다시 흡수될 수 있습니다. 숨결은 몸과 환경이 계속 물질을 주고받는 한 장면입니다.", form: "날숨의 이산화탄소" },
  { name: "조상", title: "할아버지와 할머니도, 같은 세계를 지나왔습니다.", verse: "할아버지와 할머니의 시간을 떠올립니다. / 우리 모두, 세계의 재료로 잠시 살았습니다.", detail: "앞선 세대 역시 먹고 숨 쉬며 환경과 물질을 교환했습니다. 특정 원자가 내 조상의 몸을 거쳤다고 확인한 기록은 없습니다. 그림은 세대와 환경의 연결을 상상한 경로입니다.", form: "앞선 세대의 생명을 떠올리는 장면" },
  { name: "지금의 나", title: "나는 잠시, 나라는 모양입니다.", verse: "지금이라는 눈금에서 / 흩어진 재료가 나라는 이름을 얻습니다.", detail: "우리 몸은 물질을 받아들이고 내보내며 구조를 유지합니다. 나를 정의하는 데에는 원자 목록뿐 아니라 몸의 조직, 기억, 관계와 경험도 함께 자리합니다.", form: "지금 몸의 유기물에 들어 있는 탄소" },
  { name: "다시 세상", title: "이름이 끝난 자리에도, 재료의 길은 남습니다.", verse: "나라는 모양이 풀린 뒤에도 / 세상은 재료를 다시 받아들입니다.", detail: "호흡, 배설, 분해 등으로 탄소는 공기·물·토양으로 돌아갈 수 있습니다. 어떤 경로를 거치고 얼마나 오래 머무는지는 조건에 따라 달라집니다.", form: "공기·물·토양으로 돌아가는 탄소" },
  { name: "다음 생명", title: "미래에는, 아직 이름 없는 모양이 기다립니다.", verse: "아직 태어나지 않은 생명 안에서 / 같은 세계가 다른 이름을 배울지도 모릅니다.", detail: "환경으로 돌아간 탄소는 미래 생명에 다시 들어갈 수 있습니다. 이것이 같은 인격이나 기억의 귀환을 뜻하지는 않습니다. 미래의 모습은 열려 있습니다.", form: "미래 생명으로 이어질 수 있는 탄소" },
] as const;

export const JOURNEY_ATOMS = 96;
export const JOURNEY_END = JOURNEY_STAGES.length - 1;
const seed = (i: number) => { const n = Math.sin(i * 127.1 + 311.7) * 43758.5453; return n - Math.floor(n); };
type Point = { x: number; y: number };
const point = (x: number, y: number): Point => ({ x: x * 800, y: y * 360 });
function person(i: number, center: number, scale = 1): Point {
  const u = seed(i + 44), v = seed(i + 88), angle = u * Math.PI * 2;
  if (i % 12 < 2) return point(center + Math.cos(angle) * .054 * Math.sqrt(v) * scale, .25 + Math.sin(angle) * .12 * Math.sqrt(v) * scale);
  if (i % 12 < 7) return point(center + (u - .5) * .17 * scale, .39 + v * .26 * scale);
  if (i % 12 < 9) return point(center + (i % 2 ? -1 : 1) * (.092 + v * .03) * scale, .41 + v * .26 * scale);
  return point(center + (i % 2 ? -1 : 1) * (.03 + v * .025) * scale + (u - .5) * .024, .65 + v * .25 * scale);
}
export function atomPosition(id: number, stage: number): Point {
  const u = seed(id + 11), v = seed(id + 71), angle = u * Math.PI * 2, radius = Math.sqrt(v);
  switch (stage) {
    case 0: return point(.5 + Math.cos(angle) * radius * .15, .49 + Math.sin(angle) * radius * .32);
    case 1: return point(.14 + u * .72, .28 + v * .38 + Math.sin(u * 10) * .09);
    case 2: return point(.5 + Math.sin(angle) * Math.sqrt(v) * (.13 + v * .04), .22 + v * .61);
    case 3: { const stem = id % 5, y = .24 + u * .62; return point(.24 + stem * .13 + Math.sin(u * 3) * .045 * (stem % 2 ? -1 : 1) + (v - .5) * .023, y); }
    case 4:
      if (id % 8 < 5) return point(.45 + Math.cos(angle) * radius * .18, .63 + Math.sin(angle) * radius * .21);
      if (id % 8 < 7) return point(.64 + Math.cos(angle) * radius * .072, .42 + Math.sin(angle) * radius * .14);
      return point(.62 + (id % 2 ? .046 : -.016) + (u - .5) * .019, .14 + v * .21);
    case 5: return point(.26 + u * .48, .42 + Math.sin(u * 9) * .10 + (v - .5) * .18);
    case 6: return person(id, id % 2 ? .34 : .66, .86);
    case 7: return person(id, .5);
    case 8: return point(.15 + u * .70, .63 + v * .24 + Math.sin(u * 13) * .032);
    default:
      if (id % 3 === 0) return point(.5 + (u - .5) * .025, .40 + v * .42);
      return point(id % 3 === 1 ? .42 + Math.cos(angle) * radius * .092 : .58 + Math.cos(angle) * radius * .092, .43 + Math.sin(angle) * radius * .15);
  }
}
export function journeyPosition(id: number, phase: number): Point {
  const bounded = Math.max(0, Math.min(JOURNEY_END, phase));
  const index = Math.floor(bounded), next = Math.min(JOURNEY_END, index + 1);
  const blend = bounded - index, eased = blend * blend * (3 - 2 * blend);
  const a = atomPosition(id, index), b = atomPosition(id, next);
  return { x: a.x + (b.x - a.x) * eased, y: a.y + (b.y - a.y) * eased };
}
