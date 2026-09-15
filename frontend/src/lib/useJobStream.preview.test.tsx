import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useJobStream } from "./useJobStream";

afterEach(() => vi.unstubAllGlobals());

it("announces first-pass results before the job finishes and resets for another job", () => {
  let source: { onmessage: ((event: { data: string }) => void) | null };
  class FakeSource {
    onmessage = null;
    onerror = null;
    close() {}
    constructor() { source = this; }
  }
  vi.stubGlobal("EventSource", FakeSource);
  const { result, rerender } = renderHook(({ id }) => useJobStream(id), { initialProps: { id: "first" } });
  act(() => source.onmessage?.({ data: JSON.stringify({ seq: 12, type: "search_preview_ready", payload: { candidate_count: 3 } }) }));
  expect(result.current.searchPreviewVersion).toBe(12);
  expect(result.current.finished).toBe(false);
  rerender({ id: "second" });
  expect(result.current.searchPreviewVersion).toBe(0);
});
