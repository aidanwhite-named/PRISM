type Match = { status?: string; review_status?: string; verdict?: string; reason?: string } | undefined;

export function componentReviewState(match: Match): string {
  if (match?.review_status) return match.review_status;
  if (match?.status === "unavailable") return "unavailable";
  if (match?.status === "source_checked" || (match?.verdict && match.reason)) return "reviewed";
  return "unreviewed";
}

export function componentReviewLabel(match: Match): string {
  const state = componentReviewState(match);
  if (state === "unavailable") return "검토 불가 · 원문 확인 불가";
  if (state === "unreviewed") return "미검토";
  if (match?.status !== "source_checked") return "검토함 · 근거 검증 필요";
  const labels: Record<string, string> = { strong: "강한 대응", partial: "부분 대응",
    mismatch: "대응하지 않음", not_found: "검토 범위 내 대응 근거 없음" };
  return "검토 완료 · " + (labels[match.verdict || ""] || "판정 확인 필요");
}
