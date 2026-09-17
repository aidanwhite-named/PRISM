/**
 * 설정 화면이 **실제로 그려지는가.**
 *
 * 이 화면은 한 번 구조가 어긋난 적이 있다 — 패치가 앵커를 잘못 잡아 전달 카드가
 * 두 벌이 되고, 폐기한 선택지가 살아 있었다. 타입 검사는 그것을 잡지 못한다.
 *
 * 고정하는 것:
 *   - 전달 방식 선택지가 auto / full / retrieval 셋뿐이다 (폐기 값 없음)
 *   - 화면이 안내하는 「0 = 사용 안 함」이 실제로 있는 값에만 붙는다
 *   - 두 한도(전송 하드 / 모델 컨텍스트)가 각자 자기 절에서 설명된다
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const settingsResponse = {
  values: {
    progressive_search_enabled: false,
    max_file_size_bytes: 26214400,
    max_total_upload_bytes: 104857600,
    max_files_per_job: 20,
    max_inline_chars: 0,
    default_timeout_seconds: 900,
    max_concurrency_per_provider: 1,
    runtime_context: "런타임",
    runtime_context_enabled: true,
    default_prompt_id: "",
    default_provider: "agy",
    provider_paths: {},
    default_models: {},
    reasoning_effort: {},
    keep_raw_output: true,
    fail_on_tool_use: true,
    max_search_tool_calls: 40,
    retrieval_mode: "auto",
    retrieval_max_rounds: 10,
    retrieval_max_page_reads: 80,
    retrieval_evidence_chars: 40000,
    retrieval_hits_per_document: 6,
    retrieval_neighbor_pages: 1,
    model_context_tokens: {},
    model_output_reserve_tokens: 32000,
    unknown_model_context_tokens: 128000,
    delivery_scale_documents: 0,
    delivery_scale_pages: 0,
    delivery_scale_claim_elements: 0,
    embedding_cache_max_mb: 512,
    retrieval_semantic_enabled: true,
    epo_integration_enabled: false,
    epo_consumer_key: "",
    epo_consumer_secret: "",
  },
  warnings: [],
  data_dir: "C:/data",
  runs_dir: "C:/data/runs",
  env_filtering: {
    allowlist: [],
    blocked_prefixes: [],
    removed_count: 0,
    removed_sample: [],
  },
};

const providersResponse = [
  {
    provider: "agy",
    display_name: "agy",
    installed: true,
    executable_path: "agy",
    executable_kind: "native_exe",
    executable_ok: true,
    version: "1",
    auth_state: "OK",
    capabilities: { models: ["agy-default"] },
    notes: [],
    install_hint: "",
    execution_supported: true,
    usable: true,
    runnable: true,
  },
  {
    provider: "codex",
    display_name: "Codex",
    installed: true,
    executable_path: "codex",
    executable_kind: "native_exe",
    executable_ok: true,
    version: "0.149.0",
    auth_state: "OK",
    capabilities: {
      models: ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
      reasoning_efforts: ["low", "medium", "high", "xhigh", "max", "ultra"],
      reasoning_efforts_by_model: {
        "gpt-5.6-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
        "gpt-5.6-terra": ["low", "medium", "high", "xhigh", "max", "ultra"],
        "gpt-5.6-luna": ["low", "medium", "high", "xhigh", "max"],
      },
      reasoning_defaults_by_model: {
        "gpt-5.6-sol": "low",
        "gpt-5.6-terra": "medium",
        "gpt-5.6-luna": "medium",
      },
    },
    notes: [],
    install_hint: "",
    execution_supported: true,
    usable: true,
    runnable: true,
  },
];

vi.mock("../lib/api", () => ({
  api: {
    settings: vi.fn(async () => settingsResponse),
    listPrompts: vi.fn(async () => []),
    listProviders: vi.fn(async () => providersResponse),
    updateSettings: vi.fn(async () => settingsResponse),
    probeProviders: vi.fn(async () => []),
    checkOpenAlex: vi.fn(async () => ({
      ok: true, detail: "OpenAlex 에 키 없이 연결했습니다.", http_status: 200, expires_in: null,
    })),
    startProviderLogin: vi.fn(async () => ({
      session_id: "google-login", provider: "agy", intent: "login",
      method: "google", mode: "browser", state: "WAITING_FOR_USER",
      message: "브라우저에서 Google 로그인을 완료하세요.",
      started_at: "2026-09-13T00:00:00Z", completed_at: null, can_cancel: true,
    })),
    submitProviderLoginCode: vi.fn(async () => ({
      session_id: "google-login", provider: "agy", intent: "login",
      method: "google", mode: "browser", state: "WAITING_FOR_USER",
      message: "인증 코드를 확인하고 있습니다.", needs_authorization_code: false,
      started_at: "2026-09-13T00:00:00Z", completed_at: null, can_cancel: true,
    })),
    providerLoginStatus: vi.fn(async () => ({
      session_id: "google-login", provider: "agy", intent: "login",
      method: "google", mode: "browser", state: "SUCCEEDED",
      message: "로그인이 완료되었습니다.", needs_authorization_code: false,
      started_at: "2026-09-13T00:00:00Z", completed_at: "2026-09-13T00:01:00Z", can_cancel: false,
    })),
  },
}));

let SettingsPage: typeof import("./SettingsPage").default;

beforeEach(async () => {
  SettingsPage = (await import("./SettingsPage")).default;
});
afterEach(cleanup);

async function renderPage() {
  const result = render(<SettingsPage />);
  await waitFor(() =>
    expect(screen.getByLabelText("전달 방식")).toBeTruthy(),
  );
  return result;
}

describe("대용량 인용발명 전달 방식", () => {
  it("이전 서버가 보내는 폐기한 안내는 숨기고 다른 경고는 표시한다", async () => {
    const { api } = await import("../lib/api");
    const removedWarnings = [
      "구성대비 분석 실행 도구(agy)는 셸·파일 도구를 끄는 수단이 없습니다. PRISM 은 도구 호출을 탐지해 실패로 기록할 뿐 호출 자체를 막지 못하므로, 신뢰할 수 없는 출처의 문서 분석에는 권장하지 않습니다.",
      "유사문헌 검색 실행 도구(codex)는 셸·파일 도구를 끄는 수단이 없습니다. PRISM 은 도구 호출을 탐지해 실패로 기록할 뿐 호출 자체를 막지 못하므로, 신뢰할 수 없는 출처의 문서 분석에는 권장하지 않습니다.",
      "의미 검색이 켜져 있습니다. sentence-transformers 와 모델 캐시가 없으면 키워드 검색만으로 진행하며, 그 사실이 보고서와 실행 기록에 남습니다.",
    ];
    vi.mocked(api.settings).mockResolvedValueOnce({
      ...await api.settings(),
      warnings: [...removedWarnings, "EPO OPS 사용량 한도에 도달했습니다."],
    });
    await renderPage();
    for (const warning of removedWarnings) expect(screen.queryByText(warning)).toBeNull();
    expect(screen.getByText("EPO OPS 사용량 한도에 도달했습니다.")).toBeTruthy();
  });
  it.each([true, false])("검색 방식(%s)과 무관하게 폐기한 설정을 표시하지 않는다", async (progressive) => {
    const { api } = await import("../lib/api");
    const current = await api.settings();
    vi.mocked(api.settings).mockResolvedValueOnce({
      ...current,
      values: { ...current.values, progressive_search_enabled: progressive, literature_integration_enabled: true },
    });
    const { container } = await renderPage();
    expect(container.querySelector(".settings-agy-permissions")).toBeNull();
    expect(screen.queryByText("연락처 이메일 (선택)")).toBeNull();
    expect(screen.queryByRole("button", { name: "연락처 저장" })).toBeNull();
    expect(screen.getByLabelText(/OpenAlex API Key/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "권장 목록 다시 적용" })).toBeNull();
  });
  it("브라우저 인증 코드를 제출하고 확인 후 로그인 완료를 표시한다", async () => {
    const { api } = await import("../lib/api");
    vi.mocked(api.listProviders).mockResolvedValueOnce([
      { ...providersResponse[0], auth_state: "NOT_LOGGED_IN", experimental: true, risks: [] },
    ] as Awaited<ReturnType<typeof api.listProviders>>);
    vi.mocked(api.startProviderLogin).mockResolvedValueOnce({
      session_id: "google-login", provider: "agy", intent: "login",
      method: "google", mode: "browser", state: "WAITING_FOR_USER",
      message: "인증 코드를 붙여넣어 주세요.", needs_authorization_code: true,
      started_at: "2026-09-13T00:00:00Z", completed_at: null, can_cancel: true,
    });
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: /^로그인$/ }));
    fireEvent.click(screen.getByRole("button", { name: "Google로 로그인" }));
    const input = await screen.findByLabelText("Google 인증 코드") as HTMLInputElement;
    const code = "4/prism-fake-code-for-tests";
    expect(input.type).toBe("password");
    fireEvent.change(input, { target: { value: code } });
    fireEvent.click(screen.getByRole("button", { name: "로그인 완료" }));
    await waitFor(() => {
      expect(api.submitProviderLoginCode).toHaveBeenCalledWith("agy", "google-login", code);
      expect(screen.getByLabelText("Google 인증 코드")).toBeTruthy();
      expect((screen.getByRole("button", { name: "로그인 완료" }) as HTMLButtonElement).disabled).toBe(true);
    });
    expect(document.body.textContent).not.toContain(code);
    await waitFor(() => expect(screen.getByText("로그인 완료")).toBeTruthy(), { timeout: 2500 });
  });

  it("agy에서 Google 브라우저 로그인을 시작한다", async () => {
    const { api } = await import("../lib/api");
    vi.mocked(api.listProviders).mockResolvedValueOnce([
      { ...providersResponse[0], auth_state: "NOT_LOGGED_IN", experimental: true, risks: [] },
    ] as Awaited<ReturnType<typeof api.listProviders>>);
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: /^로그인$/ }));
    fireEvent.click(screen.getByRole("button", { name: "Google로 로그인" }));
    await waitFor(() => {
      expect(api.startProviderLogin).toHaveBeenCalledWith("agy", "google");
      expect(screen.getByRole("button", { name: "로그인 취소" })).toBeTruthy();
      expect(screen.getByLabelText("Google 인증 코드")).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: "창 닫고 로그인 확인" })).toBeNull();
    expect(screen.queryByText("agy 로그인 도우미 열기")).toBeNull();
  });

  it("유사문헌 검색 도구를 구성대비 분석과 따로 저장한다", async () => {
    const { api } = await import("../lib/api");
    await renderPage();
    // 기본은 분석을 따른다 — 검색 쪽에는 모델을 고르는 칸이 없다.
    expect(screen.queryByLabelText("유사문헌 검색 모델")).toBeNull();

    fireEvent.change(screen.getByLabelText("유사문헌 검색 실행 도구"), {
      target: { value: "codex" },
    });
    fireEvent.change(screen.getByLabelText("유사문헌 검색 모델"), {
      target: { value: "gpt-5.6-sol" },
    });
    fireEvent.change(screen.getByLabelText("유사문헌 검색 추론강도"), {
      target: { value: "high" },
    });
    // 분석 쪽은 그대로다.
    expect(
      (screen.getByLabelText("구성대비 분석 실행 도구") as HTMLSelectElement).value,
    ).toBe("agy");
    // codex 는 웹 검색 도구를 선언하지 않았다.
    expect(document.body.textContent).toContain(
      "Codex는 PRISM이 확인한 웹 검색 도구를 제공하지",
    );

    fireEvent.click(screen.getByRole("button", { name: "실행 도구 저장" }));
    await waitFor(() =>
      expect(api.updateSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          default_provider: "agy",
          default_models: {},
          search_provider: "codex",
          search_models: { codex: "gpt-5.6-sol" },
          search_reasoning_effort: { codex: "high" },
        }),
      ),
    );
    // 프롬프트는 다른 카드의 값이라 함께 보내지 않는다.
    const sent = vi.mocked(api.updateSettings).mock.calls.at(-1)?.[0] ?? {};
    expect(sent).not.toHaveProperty("default_prompt_id");
    expect(sent).not.toHaveProperty("default_search_prompt_id");
  });

  it("기본 프롬프트는 별도 카드에서 프롬프트 값만 저장한다", async () => {
    const { api } = await import("../lib/api");
    const { container } = await renderPage();
    const card = container.querySelector(".settings-prompt-defaults") as HTMLElement;
    expect(card).toBeTruthy();
    // 실행 도구 선택은 이 카드에 없다.
    expect(within(card).queryByLabelText("구성대비 분석 실행 도구")).toBeNull();

    fireEvent.click(within(card).getByRole("button", { name: "기본 프롬프트 저장" }));
    // 앞선 테스트의 호출이 mock 에 남아 있으므로 마지막 호출이 바뀔 때까지 기다린다.
    await waitFor(() => {
      const sent = vi.mocked(api.updateSettings).mock.calls.at(-1)?.[0] ?? {};
      expect(Object.keys(sent).sort()).toEqual([
        "default_prompt_id",
        "default_search_prompt_id",
      ]);
    });
  });

  it("Codex 모델별 추론강도를 드롭다운으로 표시한다", async () => {
    await renderPage();
    fireEvent.change(screen.getByLabelText("구성대비 분석 실행 도구"), {
      target: { value: "codex" },
    });

    const modelSelect = screen.getByLabelText("구성대비 분석 모델") as HTMLSelectElement;
    fireEvent.change(modelSelect, { target: { value: "gpt-5.6-luna" } });

    await waitFor(() => {
      const effort = screen.getByLabelText("구성대비 분석 추론강도") as HTMLSelectElement;
      expect([...effort.options].map((option) => option.value)).toEqual([
        "",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
      ]);
    });
    expect(screen.getByText(/모델 기본값\(medium\)/)).toBeTruthy();

    fireEvent.change(modelSelect, { target: { value: "gpt-5.6-sol" } });
    await waitFor(() => {
      const effort = screen.getByLabelText("구성대비 분석 추론강도") as HTMLSelectElement;
      expect([...effort.options].map((option) => option.value)).toContain("ultra");
    });
    expect(screen.getByText(/모델 기본값\(low\)/)).toBeTruthy();
  });

  it("선택지가 auto / full / retrieval 셋뿐이다", async () => {
    await renderPage();
    const select = screen.getByLabelText("전달 방식") as HTMLSelectElement;
    const values = [...select.options].map((option) => option.value);
    expect(values).toEqual(["auto", "full", "retrieval"]);
    // 폐기한 값이 살아 있으면 사용자가 저장할 수 없는 값을 고를 수 있다.
    expect(values).not.toContain("focused");
  });

  it("전달 카드가 한 벌만 있다", async () => {
    await renderPage();
    expect(screen.getAllByText("대용량 인용발명 전달 방식")).toHaveLength(1);
    expect(screen.getAllByLabelText("전달 방식")).toHaveLength(1);
  });

  it("두 한도를 각자 다른 축으로 설명한다", async () => {
    const { container } = await renderPage();
    const text = container.textContent ?? "";
    // 전송 하드 한도: 사용자가 끌 수 없다.
    expect(text).toContain("180,000 bytes");
    expect(text).toContain("사용자가 끌 수 없고");
    // 모델 컨텍스트: 별도 절에서 설명하고, 추측하지 않는다고 밝힌다.
    expect(text).toContain("모델 컨텍스트 입력 예산");
    expect(text).toContain("모델 한도를 추측하지 않습니다");
    // 사건 규모 기준: 전송 한도가 아니라고 못박는다.
    expect(text).toContain("전송 한도가 아닙니다");
  });

  it("폐기한 설정이 화면에 남아 있지 않다", async () => {
    const { container } = await renderPage();
    const text = container.textContent ?? "";
    expect(text).not.toContain("auto 전환 크기");
    expect(text).not.toContain("페이지 단위 전달 최대 문자 수");
    expect(text).not.toContain("대형 사건 기준");
  });

  it("「0 = 사용 안 함」 안내가 붙은 값은 실제로 0 을 담고 있다", async () => {
    await renderPage();
    // 백엔드가 0 을 거절하면서 화면만 0 을 안내하던 결함의 회귀.
    for (const label of [
      "문헌 수 기준 (0 = 사용 안 함)",
      "총 페이지 수 기준 (0 = 사용 안 함)",
      "청구항 구성 수 기준 (0 = 사용 안 함)",
    ]) {
      const field = screen.getByLabelText(label) as HTMLInputElement;
      expect(field.value).toBe("0");
    }
  });

  it("새 설정이 실제 값으로 그려진다", async () => {
    await renderPage();
    expect(
      (screen.getByLabelText("근거 페이지 앞뒤로 더 담을 페이지 수") as HTMLInputElement)
        .value,
    ).toBe("1");
    expect(
      (screen.getByLabelText("임베딩 캐시 상한 (MB, 0 = 정리 안 함)") as HTMLInputElement)
        .value,
    ).toBe("512");
    expect(
      (screen.getByLabelText("출력·추론 예약 토큰") as HTMLInputElement).value,
    ).toBe("32000");
  });

  it("등록된 모델 한도가 없으면 대체값을 쓴다고 알린다", async () => {
    const { container } = await renderPage();
    expect(container.textContent).toContain("없음 (전부 대체값 사용)");
  });

  it("내부 실행 한도는 설정 화면에 노출하지 않는다", async () => {
    const { container } = await renderPage();
    const text = container.textContent ?? "";
    expect(text).not.toContain("실행 한도");
    expect(text).not.toContain("파일 1개 최대 크기");
    expect(text).not.toContain("실행 제한 시간 (초)");
    expect(text).not.toContain("검색 1회당 최대 도구 호출 수");
    expect(text).not.toContain("raw stdout/stderr 를 파일로 보존");
  });

  it("특허 연동 카드를 전체 폭 대상으로 표시하고 설명을 간결하게 유지한다", async () => {
    const { container } = await renderPage();
    expect(container.querySelector(".settings-epo")).toBeTruthy();
    expect(container.textContent).toContain(
      "EPO OPS API로 특허를 검색하고 받은 XML과 결과를 대조합니다.",
    );
  });

  it("OpenAlex 키를 비밀 값으로 저장하고 연결 테스트를 부른다", async () => {
    const { api } = await import("../lib/api");
    const enabled = {
      ...settingsResponse,
      values: { ...settingsResponse.values, literature_integration_enabled: true },
      secrets_set: { literature_openalex_api_key: false },
    };
    const saved = { ...enabled, secrets_set: { literature_openalex_api_key: true } };
    vi.mocked(api.settings).mockResolvedValueOnce(
      enabled as unknown as Awaited<ReturnType<typeof api.settings>>,
    );
    vi.mocked(api.updateSettings).mockResolvedValueOnce(
      saved as unknown as Awaited<ReturnType<typeof api.updateSettings>>,
    );
    const { container } = await renderPage();
    const card = container.querySelector(".settings-literature") as HTMLElement;
    const input = within(card).getByLabelText(/OpenAlex API Key/) as HTMLInputElement;
    expect(input.type).toBe("password");

    const key = "oa-test-key-123";
    fireEvent.change(input, { target: { value: key } });
    fireEvent.click(within(card).getAllByRole("button", { name: "저장" })[0]);
    await waitFor(() =>
      expect(api.updateSettings).toHaveBeenCalledWith({ literature_openalex_api_key: key }),
    );
    // 저장한 키는 초안에서 지우고 "저장됨"만 보인다.
    await waitFor(() => expect(within(card).getByText("저장됨")).toBeTruthy());
    expect(input.value).toBe("");
    expect(document.body.textContent).not.toContain(key);

    fireEvent.click(within(card).getByRole("button", { name: "OpenAlex 연결 테스트" }));
    await waitFor(() => expect(api.checkOpenAlex).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(card.textContent).toContain("OpenAlex 에 키 없이 연결했습니다."),
    );
  });
});
