"""
src/state.py

LangGraph Typed State. REQUIREMENTS.md 10장의 논리 구조를 따른다.

병렬 Fan-out 결과(contexts / round1_opinions / debate_reviews / revised_opinions)는
operator.add Reducer를 사용하여 서로 다른 Member의 결과를 덮어쓰지 않는다.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from src.models import (
    ApiTrace,
    CompanyContext,
    DebateReview,
    EvidenceContext,
    FinalThesis,
    GuardrailResult,
    InvestmentOpinion,
)


class AlphaArenaState(TypedDict, total=False):
    """LangGraph 전체 실행 동안 Node 사이를 오가는 단일 State 객체.

    `total=False`이므로 Graph 실행 초반(예: Guardrail 차단 경로)에는 아직
    존재하지 않는 키가 많다 — 모든 Node는 `state.get(...)`로 안전하게 읽어야
    한다(직접 `state["key"]`로 접근하는 곳은 그 시점에 반드시 채워져 있음이
    Graph 순서로 보장되는 경우로 한정한다. 예: round1_member는 이전 단계인
    load_company_context가 항상 먼저 실행되므로 `company_context`를
    안전하게 바로 읽는다).

    `Annotated[..., operator.add]`가 붙은 네 필드(contexts/round1_opinions/
    debate_reviews/revised_opinions)는 Round 1·Debate의 4개 병렬 분기가 각자
    반환한 리스트를 LangGraph가 자동으로 이어 붙이는(list + list) Reducer다.
    이 Annotation이 없으면 마지막에 실행된 분기의 결과가 나머지를 덮어써
    3명의 의견이 유실된다 — Fan-out 결과 병합의 핵심 안전장치.
    """

    trace_id: str
    question: str
    ticker: str | None

    guardrail_result: GuardrailResult
    company_context: CompanyContext | None

    contexts: Annotated[list[EvidenceContext], operator.add]
    round1_opinions: Annotated[list[InvestmentOpinion], operator.add]
    debate_reviews: Annotated[list[DebateReview], operator.add]
    revised_opinions: Annotated[list[InvestmentOpinion], operator.add]

    # 아래 세 필드는 Reducer가 없다(마지막 쓰기만 유효) — 각각 정확히 한
    # Node(finalize / render_answer / finalize)에서만 채워지도록 설계했기
    # 때문에 여러 분기가 동시에 쓰는 상황 자체가 발생하지 않는다.
    final_thesis: FinalThesis | None
    safe_trace: list[ApiTrace]
    answer: str | None
    error: str | None

    # node_render_answer가 Output Guardrail의 실제 처리 결과(원본 통과 /
    # Correction 후 통과 / Fallback 대체)를 기록한다. build_safe_trace가 이
    # 값을 그대로 옮겨 써서, 이미 대체된 answer를 다시 검사해 원본의 실패를
    # 감추는 일을 방지한다(REQUIREMENTS.md 계약 외부의 내부 전용 필드).
    output_guardrail_status: str | None
