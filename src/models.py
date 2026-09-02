"""
src/models.py

Alpha Arena의 핵심 Pydantic 계약.

REQUIREMENTS.md 11장(Structured Output 모델)에 정의된 필드 이름과 의미는
구현 시작 이후 안정적으로 유지한다. Enum, 필드명을 임의로 변경하지 않는다.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 11.1 Enum
# ---------------------------------------------------------------------------


class Stance(str, Enum):
    """분석상의 의견 Label. 실제 주문을 의미하거나 실행하지 않는다."""

    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NEUTRAL = "neutral"
    AVOID = "avoid"
    SELL = "sell"


class ConflictType(str, Enum):
    """Debate 단계에서 Member 간 불일치를 분류하는 축. Chair가 다수결 대신
    이 축을 기준으로 쟁점을 정리하는 데 사용한다."""

    FACT = "fact"
    ASSUMPTION = "assumption"
    VALUATION = "valuation"
    RISK = "risk"
    TIME_HORIZON = "time_horizon"


# ---------------------------------------------------------------------------
# 11.2 GuardrailResult
# ---------------------------------------------------------------------------


class GuardrailResult(BaseModel):
    """Input/Output Guardrail 판정 결과.

    `allowed=False`일 때 `user_message`는 사용자에게 그대로 노출해도 안전한
    거절/안내 문구여야 한다(내부 사유는 `reason_code`로만 남긴다).
    """

    allowed: bool
    reason_code: str
    user_message: str | None = None


# ---------------------------------------------------------------------------
# 11.3 EvidenceRef
# ---------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    """InvestmentOpinion/FinalThesis 안에서 하나의 주장을 근거에 연결하는 포인터.

    검색된 원문 전체가 아니라 doc_id/chunk_id로 추적 가능성만 보장하고,
    `support`에는 짧은 Paraphrase만 담아 원문 대량 인용을 피한다.
    """

    doc_id: str
    chunk_id: str | None = None
    source_type: str | None = None
    title: str | None = None
    source_url: str | None = None
    support: str


# ---------------------------------------------------------------------------
# 11.4 InvestmentOpinion
# ---------------------------------------------------------------------------


class InvestmentOpinion(BaseModel):
    """Round 1 독립 분석 및 Debate 이후 Revised 의견의 공통 Schema.

    Round 1 생성 시점에는 다른 Member의 의견을 전혀 참조하지 않은 상태에서
    채워지며, Debate 이후에는 `stance`만 `revised_stance`로 교체되어
    `revised_opinions`에 다시 담긴다(다른 필드는 스키마에 별도 개정 필드가
    없으므로 원본을 유지 — REQUIREMENTS.md 11.6 참고).
    """

    member: str
    lens: str
    stance: Stance
    confidence: float = Field(ge=0.0, le=1.0)

    thesis: str
    key_reasons: list[str]
    risks: list[str]
    assumptions: list[str]
    conditions_to_change_mind: list[str]
    evidence: list[EvidenceRef]


# ---------------------------------------------------------------------------
# 11.5 Disagreement
# ---------------------------------------------------------------------------


class Disagreement(BaseModel):
    """DebateReview 안에서 한 Member가 다른 특정 Member와 겪는 하나의 불일치.

    승자를 가리기 위한 것이 아니라 fact/assumption/valuation/risk/time_horizon
    중 어떤 종류의 불일치인지, 어느 쪽 근거가 더 강한지를 구조적으로 남긴다.
    """

    target_member: str
    conflict_type: ConflictType
    issue: str
    my_position: str
    other_position: str
    evidence_assessment: str
    resolution: str


# ---------------------------------------------------------------------------
# 11.6 DebateReview
# ---------------------------------------------------------------------------


class DebateReview(BaseModel):
    """한 Member가 Round 1 전체 의견을 검토한 뒤의 Debate 결과.

    `changed_view=False`(의견 유지)도 정상적인 성공 결과다 — Debate의 목표는
    누군가를 설득하는 것이 아니라 불일치를 명시적으로 검토하는 것이기 때문이다.
    """

    member: str
    original_stance: Stance
    revised_stance: Stance
    changed_view: bool
    change_summary: str
    disagreements: list[Disagreement]


# ---------------------------------------------------------------------------
# 11.7 FinalThesis
# ---------------------------------------------------------------------------


class FinalThesis(BaseModel):
    """Arena Chair가 생성하는 최종 산출물.

    `minority_view`는 다수결로 삭제되지 않는다 — 근거가 있는 소수의견이면
    나머지 세 Member가 반대하더라도 보존해야 한다(REQUIREMENTS.md 8장/16장).
    `business_quality_view`와 `price_value_view`를 분리해 "좋은 기업인가"와
    "현재 가격에서 좋은 투자인가"라는 서로 다른 질문에 각각 답한다.
    """

    ticker: str
    verdict: Stance
    confidence: float = Field(ge=0.0, le=1.0)

    summary: str
    business_quality_view: str
    price_value_view: str

    bull_case: list[str]
    bear_case: list[str]
    consensus: list[str]
    disagreements: list[str]
    decisive_factors: list[str]
    key_risks: list[str]
    minority_view: list[str]
    conditions_to_revisit: list[str]
    evidence: list[EvidenceRef]


# ---------------------------------------------------------------------------
# Company Data (12.1 / 12.2 / 13)
# ---------------------------------------------------------------------------


class HistoryPoint(BaseModel):
    """연도별 단일 수치 하나(예: FY2025 revenue). DCF 계산의 연도별 투영값도
    같은 타입으로 표현해 History와 Projection을 동일한 방식으로 다룬다."""

    fiscal_year: str
    value: float


class SourceRef(BaseModel):
    """company_snapshot.json에 기재된 1차 출처(10-Q, IR 자료 등) 메타데이터.
    최종 답변이 어떤 공개 자료에 기반했는지 추적할 수 있게 한다."""

    source_id: str
    type: str
    title: str
    url: str
    as_of: str


class CompanyMetrics(BaseModel):
    """get_company_metrics(ticker)의 반환 타입."""

    ticker: str
    snapshot_date: str
    currency: str
    money_unit: str

    company_name: str
    sector: str
    industry: str
    evaluation_role: str
    financial_period: str

    market: dict[str, float | None]
    financials: dict[str, float | None]
    returns: dict[str, float | None]
    balance_sheet: dict[str, float | None]

    business_context: dict[str, object]
    sources: list[SourceRef]


class FinancialHistory(BaseModel):
    """get_financial_history(ticker)의 반환 타입."""

    ticker: str
    revenue: list[HistoryPoint]
    operating_income: list[HistoryPoint]
    free_cash_flow: list[HistoryPoint]


class ValuationInputs(BaseModel):
    """Snapshot에 포함된, Valuation 계산에 사용되는 입력값(주관적 가정과는 구분)."""

    base_fcf: float
    shares_outstanding: float
    net_debt: float
    reference_wacc: float
    default_growth_rate: float | None = None
    default_discount_rate: float | None = None
    default_terminal_growth_rate: float | None = None


class CompanyContext(BaseModel):
    """load_company_context에서 Member Fan-out 전 한 번만 구성하는 공통 Context."""

    ticker: str
    metrics: CompanyMetrics
    history: FinancialHistory
    valuation_inputs: ValuationInputs


# ---------------------------------------------------------------------------
# Valuation (12.4)
# ---------------------------------------------------------------------------


class ValuationResult(BaseModel):
    """calculate_valuation(...)의 반환 타입.

    `is_scenario=True`는 이 값이 확정된 기업가치가 아니라 입력 가정에 따라
    달라지는 하나의 Scenario임을 항상 명시하기 위한 필드다(REQUIREMENTS.md 12.4:
    확정적 기업가치로 표현 금지). growth_rate/discount_rate/terminal_growth_rate는
    호출자가 넘긴 주관적 가정이며 여기 그대로 되돌려주어 출력에서 검증 가능하게 한다.
    """

    ticker: str
    is_scenario: bool = True

    growth_rate: float
    discount_rate: float
    terminal_growth_rate: float
    horizon_years: int

    projected_fcf: list[HistoryPoint]
    terminal_value: float
    pv_enterprise_value: float
    equity_value: float
    intrinsic_value_per_share: float

    market_price: float
    upside_downside: float

    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# RAG Retrieval (6.5)
# ---------------------------------------------------------------------------


class EvidenceContext(BaseModel):
    """retrieve_guru_docs(...) 및 Company Snapshot Context의 공통 표현."""

    doc_id: str
    chunk_id: str | None = None
    member: str | None = None
    text: str
    source_type: str | None = None
    title: str | None = None
    source_url: str | None = None
    score: float | None = None


# ---------------------------------------------------------------------------
# API Contract (19.2 / 19.4)
# ---------------------------------------------------------------------------


class ContextItem(BaseModel):
    """외부 API `contexts` 항목. 최소 doc_id/text 구조를 유지한다."""

    doc_id: str
    text: str
    source_type: str | None = None
    title: str | None = None
    member: str | None = None


class ApiTrace(BaseModel):
    """외부 API `trace` 항목. Safe/High-level 실행 요약만 담는다.

    System Prompt 전문, Credential, Hidden Chain-of-Thought는 절대 담지
    않는다(REQUIREMENTS.md 19.4) — `build_safe_trace`(agent.py)가 이 계약을
    지키도록 매 단계 출력을 짧은 요약 문자열로만 채운다.
    """

    step: str
    input: str
    output: str


class QueryRequest(BaseModel):
    """POST /query의 공식 Request Contract. `question` 외 필드를 추가하지 않는다."""

    question: str


class QueryResponse(BaseModel):
    """POST /query의 공식 Response Contract 최상위 Shape.

    내부 LangGraph State(AlphaArenaState)를 그대로 반환하지 않고, 항상 이
    세 필드(answer/contexts/trace)로만 매핑해서 외부 계약을 고정한다.
    """

    answer: str
    contexts: list[ContextItem]
    trace: list[ApiTrace]


# ---------------------------------------------------------------------------
# Internal Observability Trace (20장)
# ---------------------------------------------------------------------------


class TraceEvent(BaseModel):
    """`logs/trace.jsonl`에 한 줄(JSON)로 기록되는 내부 Observability 이벤트.

    API로 반환되는 ApiTrace보다 더 상세하지만, 이 역시 Credential/전체 System
    Prompt/Hidden Chain-of-Thought는 담지 않는다(REQUIREMENTS.md 20장).
    """

    trace_id: str
    timestamp: str
    step: str
    status: str
    duration_ms: float
    input_summary: str
    output_summary: str
    metadata: dict[str, object] = Field(default_factory=dict)
