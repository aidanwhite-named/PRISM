import type { ProgressiveSearchSnapshot } from "./types";

type Candidate = ProgressiveSearchSnapshot["candidates"][number];

export function isPromisingCandidate(candidate: Candidate) {
  return candidate.triage_status === "promising" && !!candidate.triage_reason?.trim()
    && candidate.search_review?.status !== "source_checked"
    && candidate.search_review?.verdict !== "mismatch";
}

/** The result cards and comparison picker must show the same candidate set/order. */
export function visibleSearchCandidates(engine?: ProgressiveSearchSnapshot) {
  return (engine?.candidates ?? []).filter(candidate => candidate.date_status !== "after_cutoff");
}

export function searchCandidateLabel(candidate: { document_number?: string; title?: string }) {
  return [candidate.document_number?.trim(), candidate.title?.trim()].filter(Boolean).join(" · ");
}
