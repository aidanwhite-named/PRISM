"""사람 속도 게이트 — 프로세스를 넘어 요청 간격을 지킨다.

원래 gpatents_backend 안에 있었다. Google Patents 페이지 말고도 PRISM 이 직접
받아 오는 제3자 페이지·파일이 생기면서(논문 OA PDF) 같은 규칙을 두 벌 두지 않기
위해 채널 중립으로 뺐다. 잠금·대기·정지 로직을 복사하면 한쪽만 고쳐지는 날이
오고, 그때 조용히 빨라지는 쪽이 남의 서버를 두드린다.

채널마다 다른 것은 셋뿐이다.

    name          상태·잠금 파일 이름. 채널끼리 간격을 나눠 쓰지 않는다.
    error_class   거절·정지를 알릴 예외. 호출부가 자기 채널 오류로 받는다.
    min_interval  요청 사이 최소 간격(초).

왜 잠금을 쥔 채 기다리는가
--------------------------
MCP 서버와 후속 검증은 다른 프로세스다. 메모리 안의 시계만 보면 두 프로세스가
같은 순간에 요청한다. 그래서 데이터 폴더의 잠금 파일로 직렬화하고, 마지막 요청
시각을 그 파일 옆에 적는다. 잠금을 쥔 채 자므로 다른 프로세스는 기다린다.

왜 429·503 을 10분 정지로 다루는가
----------------------------------
거절은 "조금 뒤에 다시"가 아니라 "지금 그만"이다. 재시도로 같은 문을 두드리면
차단이 길어진다. 정지 상태도 파일에 적어 모든 프로세스가 함께 멈춘다.
"""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from .base import PatentSearchError

DEFAULT_MIN_INTERVAL_SECONDS = 6
JITTER_SECONDS = 2.0
BLOCK_SECONDS = 600
LOCK_WAIT_SECONDS = 180


class PaceLimited(PatentSearchError):
    """상대가 요청을 거절했거나 아직 정지 시간이 남았다. 문헌 부재가 아니다."""


class HumanPaceGate:
    """요청 하나를 사람 속도로 통과시킨다.

    ``run`` 에 넘기는 함수는 응답 객체를 돌려주어야 하고, 그 객체에는 ``status``
    가 있어야 한다(429·503 판정에 쓴다). 그 밖의 형태는 채널이 정한다.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        name: str = "pace",
        min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
        jitter: float = JITTER_SECONDS,
        block_seconds: float = BLOCK_SECONDS,
        lock_wait_seconds: float = LOCK_WAIT_SECONDS,
        error_class: type[Exception] = PaceLimited,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.name = str(name)
        self.min_interval = float(min_interval)
        self.jitter = float(jitter)
        self.block_seconds = float(block_seconds)
        self.lock_wait_seconds = float(lock_wait_seconds)
        self.error_class = error_class
        self._clock = clock
        self._sleep = sleep
        self._rng = rng

    @property
    def _state_path(self) -> Path:
        return self.state_dir / f"{self.name}-pace.json"

    @contextmanager
    def _locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / f"{self.name}-pace.lock"
        with path.open("a+b") as handle:
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + self.lock_wait_seconds
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise self.error_class(
                            f"{self.name}_pace_lock_timeout: 다른 조회가 끝나기를 "
                            "기다리다 시간이 지났습니다."
                        )
                    time.sleep(0.2)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _read_state(self) -> dict:
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, state: dict) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self._state_path)

    def run(self, request: Callable[[], object]):
        with self._locked():
            state = self._read_state()
            now = self._clock()
            blocked_until = float(state.get("blocked_until") or 0)
            if blocked_until > now:
                raise self.error_class(
                    f"{self.name}_rate_limited: 최근 요청이 거절되어 "
                    f"{int(blocked_until - now)}초 동안 조회를 멈춥니다. "
                    "문헌 부재가 아닙니다."
                )
            wait = (
                float(state.get("last_request") or 0)
                + self.min_interval
                + self.jitter * self._rng()
                - now
            )
            if wait > 0:
                self._sleep(wait)
            try:
                response = request()
            finally:
                state["last_request"] = self._clock()
                self._write_state(state)
            if int(getattr(response, "status", 0) or 0) in (429, 503):
                state["blocked_until"] = self._clock() + self.block_seconds
                self._write_state(state)
            return response
