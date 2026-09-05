"""
================================================================================
test_rate_limit_retry.py — CLOVA 429 Rate Limit 재시도 정책 단위 테스트
(Task 15, qp_task15_ratelimit.md §2)
--------------------------------------------------------------------------------
실제 LLM을 호출하지 않는다. openai SDK의 실제 예외 클래스(RateLimitError 등)는
가짜 httpx2 Request/Response로 직접 만들어서 core.model_manager._call_with_retry /
_acall_with_retry에 주입한다 — `time.sleep`/`asyncio.sleep`을 일시적으로 가짜로
바꿔치기해서 테스트가 실제로 몇 초씩 기다리지 않게 한다(끝나면 반드시 원복).

§A — _retry_after_seconds(): Retry-After 헤더 파싱
§B — _rate_limit_backoff_seconds(): 스케줄값 + 지터, Retry-After 우선
§C — _call_with_retry(): 429는 긴 백오프로, 5xx/연결오류는 짧은 백오프로 self.max_retries회,
     400은 재시도 없이 즉시 예외
§D — _init_clients(): 클라이언트의 max_retries가 항상 0으로 고정되는지
§E — _acall_with_retry(): 비동기 버전이 동일하게 동작하는지

실행: cd src/agent && python test_rate_limit_retry.py
================================================================================
"""
import asyncio
import sys
import time

import httpx2
import openai

import core.model_manager as mmmod
from core.model_manager import (
    ModelManager,
    _retry_after_seconds,
    _rate_limit_backoff_seconds,
    _RATE_LIMIT_BACKOFF_SCHEDULE,
    _RATE_LIMIT_MAX_RETRIES,
)


# --------------------------------------------------------------------------
# 가짜 예외 생성 헬퍼 — 실제 openai 예외 클래스를 가짜 httpx2 Request/Response로 구성한다.
# --------------------------------------------------------------------------
def _rate_limit_error(retry_after=None):
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    req = httpx2.Request("POST", "https://example.com")
    resp = httpx2.Response(429, headers=headers, request=req)
    return openai.RateLimitError("rate limited", response=resp, body=None)


def _status_error(status: int):
    req = httpx2.Request("POST", "https://example.com")
    resp = httpx2.Response(status, request=req)
    return openai.APIStatusError(f"status {status}", response=resp, body=None)


def _bad_request_error():
    req = httpx2.Request("POST", "https://example.com")
    resp = httpx2.Response(400, request=req)
    return openai.BadRequestError("bad request", response=resp, body=None)


def _connection_error():
    req = httpx2.Request("POST", "https://example.com")
    return openai.APIConnectionError(request=req)


class _FlakyFn:
    """처음 N번은 주어진 예외를 던지고, 그 뒤로는 성공값을 반환하는 가짜 호출 대상."""

    def __init__(self, exceptions, value="ok"):
        self.exceptions = list(exceptions)
        self.value = value
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.exceptions:
            raise self.exceptions.pop(0)
        return self.value

    async def acall(self, *args, **kwargs):
        return self(*args, **kwargs)


class _SleepRecorder:
    """time.sleep/asyncio.sleep을 일시적으로 대체해 실제로 기다리지 않고 호출 인자만 기록한다."""

    def __init__(self):
        self.calls = []
        self._orig_sleep = None
        self._orig_asleep = None

    def __enter__(self):
        self._orig_sleep = mmmod.time.sleep
        self._orig_asleep = mmmod.asyncio.sleep

        def fake_sleep(seconds):
            self.calls.append(seconds)

        async def fake_asleep(seconds):
            self.calls.append(seconds)

        mmmod.time.sleep = fake_sleep
        mmmod.asyncio.sleep = fake_asleep
        return self

    def __exit__(self, *exc):
        mmmod.time.sleep = self._orig_sleep
        mmmod.asyncio.sleep = self._orig_asleep


def _new_manager(max_retries=3) -> ModelManager:
    """네트워크를 타지 않는 ModelManager — 클라이언트 생성만 하고 실제 호출은 안 한다."""
    return ModelManager(api_key="test-key", base_url="https://example.com/v1/", max_retries=max_retries)


# --------------------------------------------------------------------------
# §A — _retry_after_seconds()
# --------------------------------------------------------------------------
def run_retry_after_checks() -> bool:
    print("=" * 60)
    print("§A _retry_after_seconds()")
    print("=" * 60)
    checks = [
        ("Retry-After=7 → 7.0", _rate_limit_error(retry_after=7), 7.0),
        ("Retry-After 없음 → None", _rate_limit_error(), None),
        ("Retry-After=abc(숫자 아님) → None", _rate_limit_error(retry_after="abc"), None),
    ]
    results = []
    for label, exc, expected in checks:
        got = _retry_after_seconds(exc)
        ok = got == expected
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r}")
        results.append(ok)

    # response 자체가 없는 일반 예외 → None (방어적 동작, 크래시 없음)
    ok = _retry_after_seconds(ValueError("no response attr")) is None
    print(f"[{'PASS' if ok else 'FAIL'}] response 속성 없는 예외 → None")
    results.append(ok)

    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §B — _rate_limit_backoff_seconds()
# --------------------------------------------------------------------------
def run_backoff_schedule_checks() -> bool:
    print("=" * 60)
    print("§B _rate_limit_backoff_seconds() — 스케줄 + 지터, Retry-After 우선")
    print("=" * 60)
    results = []

    # Retry-After가 있으면 그 값을 그대로 쓴다 (스케줄 무시)
    wait = _rate_limit_backoff_seconds(_rate_limit_error(retry_after=99), attempt=0)
    ok = wait == 99.0
    print(f"[{'PASS' if ok else 'FAIL'}] Retry-After=99 우선 적용: {wait!r}")
    results.append(ok)

    # Retry-After 없으면 스케줄[attempt] <= wait < 스케줄[attempt]*1.1 (지터 0~10%)
    for attempt, base in enumerate(_RATE_LIMIT_BACKOFF_SCHEDULE):
        wait = _rate_limit_backoff_seconds(_rate_limit_error(), attempt=attempt)
        ok = base <= wait <= base * 1.1
        print(f"[{'PASS' if ok else 'FAIL'}] attempt={attempt}: {wait:.3f} (기대 [{base}, {base * 1.1}])")
        results.append(ok)

    # attempt이 스케줄 길이를 넘어가면 마지막 값으로 clamp
    last = _RATE_LIMIT_BACKOFF_SCHEDULE[-1]
    wait = _rate_limit_backoff_seconds(_rate_limit_error(), attempt=999)
    ok = last <= wait <= last * 1.1
    print(f"[{'PASS' if ok else 'FAIL'}] attempt=999 → 마지막 스케줄값으로 clamp: {wait:.3f}")
    results.append(ok)

    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §C — _call_with_retry()
# --------------------------------------------------------------------------
def run_call_with_retry_checks() -> bool:
    print("=" * 60)
    print("§C ModelManager._call_with_retry()")
    print("=" * 60)
    mm = _new_manager(max_retries=3)
    results = []

    with _SleepRecorder() as rec:
        # 1) 첫 시도 성공 — 재시도 없음
        fn = _FlakyFn([])
        val = mm._call_with_retry(fn)
        ok = val == "ok" and fn.calls == 1 and rec.calls == []
        print(f"[{'PASS' if ok else 'FAIL'}] 첫 시도 성공 — sleep 없음, calls={fn.calls}")
        results.append(ok)
        rec.calls.clear()

        # 2) 429 두 번 후 성공 — 긴 백오프로 2번 대기
        fn = _FlakyFn([_rate_limit_error(), _rate_limit_error()])
        val = mm._call_with_retry(fn)
        ok = val == "ok" and fn.calls == 3 and len(rec.calls) == 2
        ok = ok and rec.calls[0] >= _RATE_LIMIT_BACKOFF_SCHEDULE[0]
        ok = ok and rec.calls[1] >= _RATE_LIMIT_BACKOFF_SCHEDULE[1]
        print(f"[{'PASS' if ok else 'FAIL'}] 429 2회 후 성공 — calls={fn.calls}, sleeps={rec.calls}")
        results.append(ok)
        rec.calls.clear()

        # 3) 429가 최대 재시도(_RATE_LIMIT_MAX_RETRIES)를 초과하면 결국 예외를 올린다
        fn = _FlakyFn([_rate_limit_error() for _ in range(_RATE_LIMIT_MAX_RETRIES + 1)])
        raised = None
        try:
            mm._call_with_retry(fn)
        except openai.RateLimitError as e:
            raised = e
        ok = raised is not None and fn.calls == _RATE_LIMIT_MAX_RETRIES + 1
        ok = ok and len(rec.calls) == _RATE_LIMIT_MAX_RETRIES
        print(f"[{'PASS' if ok else 'FAIL'}] 429 재시도 소진 — calls={fn.calls}, sleeps={len(rec.calls)}회")
        results.append(ok)
        rec.calls.clear()

        # 4) 400(BadRequestError) — 재시도 없이 즉시 예외
        fn = _FlakyFn([_bad_request_error()])
        raised = None
        try:
            mm._call_with_retry(fn)
        except openai.BadRequestError as e:
            raised = e
        ok = raised is not None and fn.calls == 1 and rec.calls == []
        print(f"[{'PASS' if ok else 'FAIL'}] 400 — 즉시 예외, sleep 없음, calls={fn.calls}")
        results.append(ok)
        rec.calls.clear()

        # 5) 500(APIStatusError) 1회 후 성공 — 짧은 백오프로 재시도(self.max_retries=3 이내)
        fn = _FlakyFn([_status_error(500)])
        val = mm._call_with_retry(fn)
        ok = val == "ok" and fn.calls == 2 and len(rec.calls) == 1
        print(f"[{'PASS' if ok else 'FAIL'}] 500 1회 후 성공 — calls={fn.calls}, sleeps={rec.calls}")
        results.append(ok)
        rec.calls.clear()

        # 6) 500이 self.max_retries(3)를 초과하면 예외를 올린다
        fn = _FlakyFn([_status_error(500) for _ in range(mm.max_retries + 1)])
        raised = None
        try:
            mm._call_with_retry(fn)
        except openai.APIStatusError as e:
            raised = e
        ok = raised is not None and fn.calls == mm.max_retries + 1 and len(rec.calls) == mm.max_retries
        print(f"[{'PASS' if ok else 'FAIL'}] 500 재시도 소진 — calls={fn.calls}, sleeps={len(rec.calls)}회")
        results.append(ok)
        rec.calls.clear()

        # 7) 연결 오류(APIConnectionError)도 500과 같은 정책(짧은 백오프, max_retries회)
        fn = _FlakyFn([_connection_error()])
        val = mm._call_with_retry(fn)
        ok = val == "ok" and fn.calls == 2 and len(rec.calls) == 1
        print(f"[{'PASS' if ok else 'FAIL'}] 연결 오류 1회 후 성공 — calls={fn.calls}, sleeps={rec.calls}")
        results.append(ok)
        rec.calls.clear()

    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §D — _init_clients(): 클라이언트 max_retries는 항상 0
# --------------------------------------------------------------------------
def run_client_max_retries_checks() -> bool:
    print("=" * 60)
    print("§D _init_clients() — 클라이언트 max_retries는 항상 0 (재시도는 이 계층에서만)")
    print("=" * 60)
    results = []
    for configured in (0, 3, 10):
        mm = _new_manager(max_retries=configured)
        ok = getattr(mm.client, "max_retries", None) == 0
        ok = ok and getattr(mm.async_client, "max_retries", None) == 0
        ok = ok and mm.max_retries == configured  # self.max_retries 자체는 그대로 보존(재시도 횟수 용도)
        print(f"[{'PASS' if ok else 'FAIL'}] configured max_retries={configured} → "
              f"client.max_retries={getattr(mm.client, 'max_retries', None)}, "
              f"self.max_retries={mm.max_retries}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §E — _acall_with_retry() — 비동기 버전 회귀
# --------------------------------------------------------------------------
def run_async_call_with_retry_checks() -> bool:
    print("=" * 60)
    print("§E ModelManager._acall_with_retry()")
    print("=" * 60)
    mm = _new_manager(max_retries=3)
    results = []

    async def _scenarios():
        out = []

        # 429 1회 후 성공
        fn = _FlakyFn([_rate_limit_error()])
        val = await mm._acall_with_retry(fn.acall)
        out.append(val == "ok" and fn.calls == 2)

        # 400 — 즉시 예외
        fn = _FlakyFn([_bad_request_error()])
        raised = False
        try:
            await mm._acall_with_retry(fn.acall)
        except openai.BadRequestError:
            raised = True
        out.append(raised and fn.calls == 1)

        return out

    with _SleepRecorder():
        outcomes = asyncio.run(_scenarios())

    labels = ["429 1회 후 성공(비동기)", "400 즉시 예외(비동기)"]
    for label, ok in zip(labels, outcomes):
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        results.append(ok)

    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


def main():
    ok1 = run_retry_after_checks()
    ok2 = run_backoff_schedule_checks()
    ok3 = run_call_with_retry_checks()
    ok4 = run_client_max_retries_checks()
    ok5 = run_async_call_with_retry_checks()
    sys.exit(0 if (ok1 and ok2 and ok3 and ok4 and ok5) else 1)


if __name__ == "__main__":
    main()
