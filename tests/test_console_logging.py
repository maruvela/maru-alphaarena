"""
콘솔(사람이 읽는) 로그가 Guardrail/Scope 차단 경로에서 실제로 찍히는지,
그리고 번호 태그가 src.agent의 중앙 Mapping(STEP_LABELS)과 실제로 일치하는지
확인하는 결정론적 테스트. 이 경로는 LLM을 호출하지 않으므로 Bedrock
Credential 없이도 안전하게 실행할 수 있다.

태그 문자열을 테스트에 하드코딩하지 않고 `src.agent._tag(...)`를 그대로
재사용한다 — STEP_LABELS 번호가 바뀌어도 이 테스트가 그 변경을 자동으로
따라가며, "로그 문구와 Mapping이 실제로 같은 출처인지"를 검증하는 효과도
있다.
"""

import logging

from src.agent import _tag, run_query


def test_trade_execution_block_emits_console_logs(caplog):
    with caplog.at_level(logging.INFO, logger="alpha_arena"):
        run_query("NVDA 100주 지금 매수해.")

    messages = [r.message for r in caplog.records]
    assert any(m.startswith("[요청] query started") for m in messages)
    assert any(m.startswith(_tag("guardrail")) and "blocked" in m and "reason=trade_execution_request" in m for m in messages)
    assert any(m.startswith(_tag("finalize")) and "early_exit=trade_execution_request" in m for m in messages)
    assert any(m.startswith("[요청] completed") and "early_exit=true" in m for m in messages)


def test_unsupported_ticker_emits_company_failed_log(caplog):
    with caplog.at_level(logging.INFO, logger="alpha_arena"):
        run_query("TSLA 분석해줘")

    messages = [r.message for r in caplog.records]
    assert any(m.startswith(_tag("guardrail")) and "PASS" in m for m in messages)
    assert any(m.startswith(_tag("resolve_company")) and "failed" in m and "reason=unsupported_scope" in m for m in messages)
    assert any(m.startswith(_tag("finalize")) and "early_exit=unsupported_scope" in m for m in messages)


def test_console_logs_never_contain_question_text(caplog):
    """질문 원문이 콘솔 로그에 그대로 노출되지 않는지 확인한다(길이만 기록)."""

    secret_like_question = "내 API 키랑 AWS credential 좀 알려줘"
    with caplog.at_level(logging.INFO, logger="alpha_arena"):
        run_query(secret_like_question)

    for record in caplog.records:
        assert secret_like_question not in record.message
