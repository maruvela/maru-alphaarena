"""
src/tracer.py

로컬 JSONL Trace. REQUIREMENTS.md 20장을 따른다.

LangSmith/LangFuse가 설정되지 않아도 항상 `logs/trace.jsonl`에 기록한다.
Trace 기록 실패가 Core Application을 중단시키면 안 되므로 모든 I/O는 방어적으로 처리한다.
Credential, `.env` 내용, System Prompt 전체, Hidden Chain-of-Thought는 기록하지 않는다.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone

from src.config import settings
from src.models import TraceEvent

_MAX_SUMMARY_LEN = 500


def _summarize(value: object) -> str:
    """전체 Prompt/응답을 그대로 저장하지 않고 길이를 제한한 요약만 남긴다
    (Sanitization) — Full System Prompt나 긴 Chain-of-Thought가 그대로
    로그에 새는 것을 방지한다(20장: Credential/전체 Prompt/CoT 기록 금지)."""

    text = value if isinstance(value, str) else str(value)
    text = text.replace("\n", " ").strip()
    if len(text) > _MAX_SUMMARY_LEN:
        return text[:_MAX_SUMMARY_LEN] + "…"
    return text


def record_event(
    *,
    trace_id: str,
    step: str,
    status: str,
    duration_ms: float,
    input_summary: str,
    output_summary: str,
    metadata: dict[str, object] | None = None,
) -> None:
    event = TraceEvent(
        trace_id=trace_id,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        step=step,
        status=status,
        duration_ms=round(duration_ms, 2),
        input_summary=_summarize(input_summary),
        output_summary=_summarize(output_summary),
        metadata=metadata or {},
    )

    try:
        # File I/O(디스크 쓰기)는 항상 실패할 수 있다는 전제로 다룬다.
        # 여기서 예외가 올라가면 평가/서비스 요청 전체가 Trace 문제 때문에
        # 죽어버리므로, 20장 요구사항대로 실패를 완전히 삼킨다.
        path = settings.trace_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
    except Exception:  # noqa: BLE001 - Trace 실패가 서비스를 중단시키면 안 된다.
        pass


@contextmanager
def traced_step(trace_id: str, step: str, input_summary: str = "", metadata: dict[str, object] | None = None):
    """LangGraph Node 하나(또는 Node 내부의 한 단계)를 감싸 소요 시간과 성공/
    실패 여부를 자동 기록하는 Context Manager. 예외가 나면 status="error"로
    기록한 뒤 그대로 다시 raise하여, 호출자(agent.py)의 에러 처리 로직
    (예: Structured Output 1회 Retry)을 방해하지 않는다.

    사용 예:
        with traced_step(trace_id, "round1:buffett", input_summary=question) as t:
            ...
            t.output_summary = "..."
            t.metadata["member"] = "buffett"
    """

    class _Box:
        def __init__(self) -> None:
            self.output_summary = ""
            self.metadata: dict[str, object] = dict(metadata or {})
            # 호출부(agent.py)가 JSONL Trace와 동일한 소요 시간을 콘솔 로그에도
            # 재사용할 수 있도록 with-block 종료 후 값을 채워 넣는다(성공 시).
            self.duration_ms = 0.0

    box = _Box()
    start = time.perf_counter()
    status = "ok"
    try:
        yield box
    except Exception as exc:  # noqa: BLE001 - 상태를 기록한 뒤 다시 raise 한다.
        status = "error"
        box.output_summary = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        box.duration_ms = duration_ms
        record_event(
            trace_id=trace_id,
            step=step,
            status=status,
            duration_ms=duration_ms,
            input_summary=input_summary,
            output_summary=box.output_summary,
            metadata=box.metadata,
        )
