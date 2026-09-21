"""모델별 입력 토큰 예산.

두 가지 한도를 구분한다. 이름을 섞으면 둘 다 뜻이 흐려진다.

  Provider 전송 하드 한도 (Provider.max_input_bytes)
      그 **CLI** 가 모델에 넘기기 전에 입력을 자르는 지점. agy 만 선언한다.
      사용자가 끌 수 없고, 넘겨 보내면 뒷부분이 조용히 사라진 채 종료 코드 0
      으로 "성공"한다.

  모델 컨텍스트 한도 (이 파일)
      그 **모델** 이 받을 수 있는 토큰 수. codex, claude 처럼 CLI 가 자르지
      않는 Provider 의 실제 한계다. 넘기면 CLI 나 API 가 거절한다 — 조용히
      잘리지는 않지만, 검색 비용을 다 쓴 뒤 실패한다.

agy 에는 이 파일을 적용하지 않는다. 그쪽은 CLI 가 180,000 bytes 에서 자르고,
그 값이 어떤 현대 모델의 컨텍스트보다도 작기 때문에 항상 바이트 한도가 먼저
걸린다. 두 규칙을 겹쳐 걸면 판정 사유만 복잡해지고 결과는 같다.

**PRISM 은 모델 한도를 추측하지 않는다.**

공급사가 공개한 숫자를 코드에 박아 두면 모델이 바뀔 때마다 조용히 틀린다. 그
틀림은 "실행이 실패했다"가 아니라 "전체를 넣을 수 있었는데 좁혀 읽었다"거나 그
반대로 나타나므로 알아차리기 어렵다. 그래서 기본 표는 **비어 있고**, 아는 값은
설정(`model_context_tokens`) 또는 Codex CLI 모델 카탈로그에서 읽는다. 모르는 모델에는 보수적 대체값을 쓰고
그 사실을 판정 사유에 남긴다.
"""

from __future__ import annotations

import math
import json
import os
import hashlib
import tempfile
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass

# 값을 어디서 얻었는가. 판정 사유에 그대로 실린다.
SOURCE_CONFIGURED = "configured"
SOURCE_FALLBACK = "fallback"
SOURCE_CODEX_CATALOG = "codex_catalog"

ENCODING_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
ENCODING_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"


def encoding_cache_path() -> Path:
    directory = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get(
        "DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache")))
    return Path(directory) / hashlib.sha1(ENCODING_URL.encode()).hexdigest()


@lru_cache(maxsize=1)
def _cached_encoder():
    # Preflight must never download tokenizer data or block on the network.
    # Setup primes this cache; missing/corrupt data keeps the byte estimate.
    try:
        if os.environ.get("TIKTOKEN_CACHE_DIR") == "" or (
            "TIKTOKEN_CACHE_DIR" not in os.environ and os.environ.get("DATA_GYM_CACHE_DIR") == ""
        ):
            return None
        if hashlib.sha256(encoding_cache_path().read_bytes()).hexdigest() != ENCODING_SHA256:
            return None
        import tiktoken
        return tiktoken.get_encoding("o200k_base")
    except (ImportError, OSError, ValueError):
        return None


def _catalog_context(model: str) -> int | None:
    try:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        for row in data.get("models", []):
            if row.get("slug") != model:
                continue
            context = row.get("context_window")
            percent = row.get("effective_context_window_percent", 100)
            if (type(context) is int and context > 0 and type(percent) is int
                    and 0 < percent <= 100):
                # max_context_window requires opting in; use the default only.
                return context * percent // 100
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None

# 토큰 추정에 쓰는 UTF-8 바이트/토큰 비율.
#
# 실제 비율은 토크나이저와 언어에 따라 다르다. 한국어 UTF-8 은 음절 하나가
# 3 bytes 이고 서브워드 하나가 보통 음절 1~3개이므로 대략 4~9 bytes/token,
# 영문은 대략 4 bytes/token 이다. 여기서 2 를 쓰는 것은 **일부러 실제보다 많이
# 세기 위해서다** — 틀렸을 때 좁아지는 쪽이, 다 넣었다가 모델에 거절당해 검색
# 비용을 날리는 쪽보다 낫다.
#
# 지원 모델의 로컬 토크나이저를 사용할 수 없을 때만 이 대체 추정을 쓴다.
# 로컬 토큰 수도 CLI가 추가하는 도구·메시지 포장까지 정확히 세지는 못한다.
CONSERVATIVE_BYTES_PER_TOKEN = 2

# 공급사 공개 값을 코드에 박지 않는다. 위 docstring 참조.
KNOWN_CONTEXT_TOKENS: dict[str, int] = {}


@dataclass(frozen=True)
class TokenBudget:
    """이 실행이 쓸 수 있는 입력 토큰과 그 근거."""

    context_tokens: int
    reserve_tokens: int
    source: str
    model: str = ""
    provider_id: str = ""

    @property
    def input_tokens(self) -> int:
        """출력·추론 자리를 뺀 입력 예산. 최소 0."""
        return max(0, self.context_tokens - self.reserve_tokens)

    @property
    def is_estimated(self) -> bool:
        """모델 한도를 확인하지 못하고 대체값을 쓴 것인가."""
        return self.source == SOURCE_FALLBACK

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "context_tokens": self.context_tokens,
            "reserve_tokens": self.reserve_tokens,
            "input_tokens": self.input_tokens,
            "source": self.source,
            "provider_id": self.provider_id,
            "token_estimation": (
                "o200k_base_with_margin" if self.provider_id == "codex"
                and self.model.startswith("gpt-5") and _cached_encoder() is not None
                else "utf8_bytes_div_2"
            ),
        }


def _lookup(overrides: dict, provider_id: str, model: str) -> int | None:
    """재정의 표에서 찾는다. 구체적인 키를 먼저 본다.

    `provider:model` → `model` 순이다. 같은 이름의 모델을 여러 Provider 가
    제공할 수 있고(agy 가 claude 계열 모델을 노출한다), 그때 한도가 다를 수
    있기 때문이다.
    """
    if not isinstance(overrides, dict):
        overrides = {}
    for key in (f"{provider_id}:{model}", model):
        if not key or key == ":":
            continue
        value = overrides.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def token_budget(
    *,
    provider_id: str,
    model: str | None,
    overrides: dict | None = None,
    reserve_tokens: int,
    fallback_context_tokens: int,
) -> TokenBudget:
    """이 (Provider, 모델) 조합의 입력 토큰 예산.

    설정 재정의 → Codex CLI의 정확히 일치하는 모델 → 보수적 대체값 순이다.
    """
    name = str(model or "").strip()
    configured = _lookup(overrides or {}, provider_id, name)
    if configured is not None:
        return TokenBudget(
            context_tokens=configured,
            reserve_tokens=reserve_tokens,
            source=SOURCE_CONFIGURED,
            model=name,
            provider_id=provider_id,
        )
    catalog = _catalog_context(name) if provider_id == "codex" and name else None
    if catalog is not None:
        return TokenBudget(catalog, reserve_tokens, SOURCE_CODEX_CATALOG, name, provider_id)
    return TokenBudget(
        context_tokens=fallback_context_tokens,
        reserve_tokens=reserve_tokens,
        source=SOURCE_FALLBACK,
        model=name,
        provider_id=provider_id,
    )


def estimate_tokens(*texts: str, provider_id: str = "", model: str = "") -> int:
    """로컬 토큰 수에 여유를 더한 추정. CLI 전체 사용량의 정확한 상한은 아니다."""
    if provider_id == "codex" and model.startswith("gpt-5"):
        encoder = _cached_encoder()
        if encoder is not None:
            tokens = sum(len(encoder.encode(text, disallowed_special=())) for text in texts if text)
            # Plain text count is not CLI total usage: reserve wrapper/tool tokens
            # and a proportional margin as well as the separate output reserve.
            return math.ceil(tokens * 1.05) + 16_384
    total = sum(len(text.encode("utf-8")) for text in texts if text)
    return math.ceil(total / CONSERVATIVE_BYTES_PER_TOKEN)


def describe(budget: TokenBudget) -> str:
    """판정 사유에 붙일 한 문장. 추정값이면 그 사실을 반드시 적는다."""
    if budget.is_estimated:
        return (
            f"모델 {budget.model or '(미지정)'} 의 컨텍스트 한도를 확인하지 "
            f"못해 보수적 대체값 {budget.context_tokens:,} 토큰을 썼습니다. "
            f"출력·추론용 {budget.reserve_tokens:,} 토큰을 빼면 입력 예산은 "
            f"{budget.input_tokens:,} 토큰입니다. 환경설정의 「모델 컨텍스트 "
            "한도」에 실제 값을 넣으면 그 값을 씁니다."
        )
    origin = "Codex CLI 모델 카탈로그의 유효 기본 한도" if budget.source == SOURCE_CODEX_CATALOG else "환경설정 값"
    return (
        f"모델 {budget.model} 의 컨텍스트 한도 {budget.context_tokens:,} 토큰에서 "
        f"출력·추론용 {budget.reserve_tokens:,} 토큰을 뺀 {budget.input_tokens:,} "
        f"토큰을 입력 예산으로 씁니다({origin})."
    )
