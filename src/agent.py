"""
src/agent.py

LangGraph 구성 및 Invoke. REQUIREMENTS.md 9장(Workflow) / 14~17장(Member, Debate,
Chair, Rendering)을 구현한다.

Graph:
START -> input_guardrail -> (blocked) -> finalize -> END
                          -> resolve_company -> (blocked) -> finalize -> END
                                              -> load_company_context
                                              -> round1_fanout -> round1_member x4 (Send)
                                              -> collect_round1
                                              -> debate_fanout -> debate_member x4 (Send)
                                              -> collect_revisions
                                              -> arena_chair
                                              -> render_answer
                                              -> finalize -> END
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from langchain_aws import ChatBedrock
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src import guardrails, prompts, tools, tracer
from src.config import settings
from src.models import (
    ApiTrace,
    CompanyContext,
    DebateReview,
    EvidenceContext,
    FinalThesis,
    GuardrailResult,
    InvestmentOpinion,
    ValuationResult,
)
from src.retriever import MEMBERS as MEMBER_KEYS
from src.retriever import retrieve_guru_docs
from src.state import AlphaArenaState

# 사람이 서버 터미널에서 LangGraph 실행 흐름을 실시간으로 볼 수 있게 하는
# 콘솔 Observability 채널. `src.tracer`의 JSONL Trace(파일 기록, 평가/사후
# 분석용)를 대체하지 않고 그대로 유지한 채, 같은 Node 경계에서 사람이 읽기
# 쉬운 한 줄짜리 [TAG] 메시지를 "추가로" 남긴다. Level/포맷은 src.config가
# LOG_LEVEL 환경변수로 이미 설정해두므로 여기서는 Logger만 얻는다.
logger = logging.getLogger("alpha_arena")

MEMBER_CONFIG: dict[str, dict[str, str]] = {
    "buffett": {
        "full_name": "Warren Buffett",
        "lens_title": "Quality / Moat / Long-term Compounder",
        "prompt": prompts.BUFFETT_MEMBER_PROMPT,
    },
    "lynch": {
        "full_name": "Peter Lynch",
        "lens_title": "Growth / Business Momentum",
        "prompt": prompts.LYNCH_MEMBER_PROMPT,
    },
    "marks": {
        "full_name": "Howard Marks",
        "lens_title": "Risk / Price / Market Cycle",
        "prompt": prompts.MARKS_MEMBER_PROMPT,
    },
    "damodaran": {
        "full_name": "Aswath Damodaran",
        "lens_title": "Valuation / Intrinsic Value",
        "prompt": prompts.DAMODARAN_MEMBER_PROMPT,
    },
}


# ---------------------------------------------------------------------------
# 콘솔 로그 단계 번호 (Single Source of Truth)
#
# 사람이 서버 터미널에서 "지금 LangGraph의 어느 단계인지"를 번호로 바로
# 대응해서 볼 수 있게 하기 위한 중앙 Mapping이다. logger 호출부마다 번호와
# 한글 Label을 따로 하드코딩하지 않고 전부 이 표와 아래 헬퍼 함수를 거치게
# 해서, 번호 체계가 여러 곳에서 서로 어긋나는 것을 막는다.
#
# 이 번호는 docs/how_to_use.md의 Mermaid Diagram(12절)과 Node 대응표(13절)에도
# 동일하게 쓰인다 — Graph 구조(add_node/add_edge)가 바뀌면 이 표와 두 문서
# 섹션을 함께 갱신해야 한다(REQUIREMENTS.md 36.2/20.1).
#
# Key는 `src.tracer.traced_step(...)`에 실제로 넘기는 step 문자열과 동일하다
# (guardrail/resolve_company/company_context/chair/output_guardrail). 즉 이
# 번호는 새 개념이 아니라 이미 JSONL Trace에 기록되는 step 이름에 사람이 읽기
# 쉬운 번호를 얹은 것뿐이다. `finalize`는 Node 함수 자체는 있지만 tracer로
# 감싸지 않으므로(43번 근처 node_finalize 참고) 이 표에만 등록해 둔다.
STEP_LABELS: dict[str, tuple[str, str]] = {
    "guardrail": ("01", "입력 가드레일"),
    "resolve_company": ("02", "기업 식별"),
    "company_context": ("03", "Company Context 로드"),
    "chair": ("06", "Arena Chair 종합"),
    "output_guardrail": ("07", "출력 가드레일"),
    "finalize": ("08", "최종 응답 생성"),
}

# Round1(04)/Debate(05) Fan-out의 4개 병렬 분기를 표시용 문자(A~D)에 고정
# 매핑한다. retriever.MEMBERS(=buffett/lynch/marks/damodaran) 등장 순서를
# 그대로 쓰므로, Member 구성이 바뀌면(추가/순서 변경) 이 매핑도 함께 바뀐다.
MEMBER_LETTERS: dict[str, str] = dict(zip(MEMBER_KEYS, "ABCD"))

_MEMBER_STEP_NUMBER = {"round1": "04", "debate": "05"}
_MEMBER_STEP_ACTION = {"round1": "1차 분석", "debate": "토론/재검토"}


def _tag(step: str) -> str:
    """Member와 무관한 단일 단계의 `[NN 설명]` 태그를 만든다."""

    number, label = STEP_LABELS[step]
    return f"[{number} {label}]"


def _member_tag(step: str, member: str) -> str:
    """Round1/Debate 병렬 분기 하나의 `[NN-X 이름 설명]` 태그를 만든다.

    Round 1(4개)과 Debate(4개)는 실제로 LangGraph Send 기반 병렬 실행이라
    완료 순서가 매 호출마다 달라질 수 있다(9.2/9.3장) — 이 태그는 순서를
    강제로 맞추기 위한 것이 아니라, 로그 한 줄만 보고도 "어느 단계의 어느
    Member인지"를 바로 식별하기 위한 것이다.
    """

    number = _MEMBER_STEP_NUMBER[step]
    letter = MEMBER_LETTERS[member]
    full_name = MEMBER_CONFIG[member]["full_name"]
    action = _MEMBER_STEP_ACTION[step]
    return f"[{number}-{letter} {full_name} {action}]"


def _member_rag_tag(member: str) -> str:
    """Round 1(04) 내부에서만 일어나는 RAG 검색의 Sub-step 태그(04-X.1)를 만든다.

    RAG 검색은 독립된 LangGraph Node가 아니라 `node_round1_member` 함수
    내부의 한 단계이므로(6.5장 `retrieve_guru_docs` 호출), 새 최상위 번호를
    만들지 않고 소속 단계(04)에 `.1`로 종속시킨다 — 실제 Graph에 없는
    Node를 로그에서 지어내지 않기 위함이다(§14 조사 결과와 동일한 이유).
    """

    letter = MEMBER_LETTERS[member]
    full_name = MEMBER_CONFIG[member]["full_name"]
    return f"[04-{letter}.1 {full_name} RAG 검색]"


class AgentError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# LLM 접근
# ---------------------------------------------------------------------------

_chat_model: ChatBedrock | None = None


def _get_chat_model() -> ChatBedrock:
    """AWS Bedrock ChatModel의 Lazy Singleton.

    모듈 Import 시점이 아니라 최초 호출 시점에 생성한다 — Credential이 없는
    환경(예: Docker build 단계, Import만 하는 Unit Test)에서도 이 모듈을
    안전하게 import할 수 있게 하기 위함이다. Temperature는 REQUIREMENTS.md
    21장에 따라 재현성을 위해 기본값 0을 사용한다.
    """

    global _chat_model
    if _chat_model is None:
        _chat_model = ChatBedrock(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
            model_kwargs={
                "temperature": settings.model_temperature,
                # FinalThesis/InvestmentOpinion처럼 list 필드가 많은 Structured
                # Output은 Provider 기본 max_tokens로는 중간에 잘릴 수 있어
                # 명시적으로 넉넉히 지정한다(그렇지 않으면 29.4 Retry가 반복돼도
                # 같은 이유로 계속 실패한다).
                "max_tokens": settings.model_max_tokens,
            },
            # max_tokens를 늘리면 응답 생성 시간도 길어지므로, botocore 기본
            # read timeout(60s)보다 넉넉한 값을 준다 — Round1/Debate가 4-way
            # 병렬로 동시에 호출되는 상황에서는 지연이 더 커질 수 있다.
            timeout=180,
            max_retries=3,
        )
    return _chat_model


def _invoke_structured(schema: type, prompt_text: str):
    """29.4: Malformed Structured Output은 최대 1회 Retry."""

    structured_model = _get_chat_model().with_structured_output(schema)
    try:
        return structured_model.invoke(prompt_text)
    except Exception:  # noqa: BLE001 - 1회 제한 Retry
        try:
            return structured_model.invoke(prompt_text)
        except Exception as exc:  # noqa: BLE001
            raise AgentError(f"Structured Output 생성 실패 ({schema.__name__}): {exc}") from exc


def get_chat_model() -> ChatBedrock:
    """평가 Runner 등 외부 모듈이 동일 설정의 Chat Model을 재사용할 수 있게 한다."""

    return _get_chat_model()


def _correct_output(draft_answer: str, reason: str) -> str:
    """Output Guardrail Post-check가 실패했을 때의 1회 한정 Correction(18.4장).

    구조가 아니라 자연어 재작성이 목적이므로 Structured Output이 아닌 일반
    Chat 호출을 사용한다. 실패 사유(`reason`, 예: forbidden_expression/secret_leak)를
    Prompt에 그대로 전달해 무엇을 고쳐야 하는지 명확히 지시한다.
    """

    prompt_text = prompts.OUTPUT_CORRECTION_PROMPT.format(reason=reason, draft_answer=draft_answer)
    response = _get_chat_model().invoke(prompt_text)
    content = response.content
    return content if isinstance(content, str) else str(content)


# ---------------------------------------------------------------------------
# Rendering Helpers
# ---------------------------------------------------------------------------


def member_label(member: str) -> str:
    cfg = MEMBER_CONFIG.get(member)
    if not cfg:
        return member
    return f"{cfg['full_name']} Lens — {cfg['lens_title']}"


def _short(text: str, limit: int = 160) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _render_company_context(ctx: CompanyContext) -> str:
    m = ctx.metrics
    bc = m.business_context or {}

    lines = [
        f"Ticker: {ctx.ticker} ({m.company_name})",
        f"Sector/Industry: {m.sector} / {m.industry}",
        f"Evaluation Role: {m.evaluation_role}",
        f"Snapshot Date: {m.snapshot_date} (Financial Period: {m.financial_period})",
        f"Currency/Unit: {m.currency} / {m.money_unit}",
        f"Market: {m.market}",
        f"Financials (TTM): {m.financials}",
        f"Returns: {m.returns}",
        f"Balance Sheet: {m.balance_sheet}",
        f"Business Description: {bc.get('description', '')}",
        "Growth Drivers: " + "; ".join(bc.get("growth_drivers", []) or []),
        "Company-disclosed Key Risks: " + "; ".join(bc.get("key_risks", []) or []),
        "Revenue History: " + ", ".join(f"{p.fiscal_year}={p.value}" for p in ctx.history.revenue),
        "Operating Income History: "
        + ", ".join(f"{p.fiscal_year}={p.value}" for p in ctx.history.operating_income),
        "Free Cash Flow History: "
        + ", ".join(f"{p.fiscal_year}={p.value}" for p in ctx.history.free_cash_flow),
        (
            "Valuation Inputs (Snapshot, 객관적 Fact이지 Assumption 아님): "
            f"base_fcf={ctx.valuation_inputs.base_fcf}, "
            f"shares_outstanding={ctx.valuation_inputs.shares_outstanding}, "
            f"net_debt={ctx.valuation_inputs.net_debt}, "
            f"reference_wacc={ctx.valuation_inputs.reference_wacc}"
        ),
        "Sources: " + "; ".join(f"{s.source_id}(as_of={s.as_of})" for s in m.sources),
    ]
    return "\n".join(lines)


def _render_rag_context(items: list[EvidenceContext]) -> str:
    if not items:
        return "(관련 RAG 근거를 찾지 못했습니다. 확인할 수 없는 내용은 확인할 수 없다고 명시할 것.)"

    blocks = []
    for i, c in enumerate(items, start=1):
        blocks.append(f"[{i}] doc_id={c.doc_id} chunk_id={c.chunk_id} title={c.title}\n{c.text}")
    return "\n\n".join(blocks)


def _render_valuation(v: ValuationResult) -> str:
    return (
        f"growth_rate={v.growth_rate:.4f}, discount_rate={v.discount_rate:.4f}, "
        f"terminal_growth_rate={v.terminal_growth_rate:.4f}, horizon_years={v.horizon_years}\n"
        f"intrinsic_value_per_share={v.intrinsic_value_per_share:.2f}, "
        f"market_price={v.market_price:.2f}, upside_downside={v.upside_downside:+.2%}\n"
        f"warnings={v.warnings}"
    )


def render_final_thesis(thesis: FinalThesis, revised_opinions: list[InvestmentOpinion]) -> str:
    lines: list[str] = []
    lines.append(f"# {thesis.ticker} Investment Thesis")
    lines.append("")
    lines.append("## 1. 결론")
    lines.append(f"- Verdict: {thesis.verdict.value} (Confidence: {thesis.confidence:.2f})")
    lines.append(f"- {thesis.summary}")
    lines.append("")
    lines.append("## 2. 기업의 질 vs 현재 가격")
    lines.append(f"- 기업의 질: {thesis.business_quality_view}")
    lines.append(f"- 가격/가치: {thesis.price_value_view}")
    lines.append("")
    lines.append("## 3. Member별 최종 입장")
    for opinion in revised_opinions:
        lines.append(f"- {member_label(opinion.member)}: {opinion.stance.value} — {opinion.thesis}")
    lines.append("")
    lines.append("## 4. 핵심 쟁점")
    for item in thesis.disagreements:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 5. Bull Case")
    for item in thesis.bull_case:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 6. Bear Case")
    for item in thesis.bear_case:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 7. Minority View")
    if thesis.minority_view:
        for item in thesis.minority_view:
            lines.append(f"- {item}")
    else:
        lines.append("- 이번 Debate에서는 근거 있는 Minority View가 확인되지 않았습니다.")
    lines.append("")
    lines.append("## 8. 주요 리스크")
    for item in thesis.key_risks:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 9. 재검토 조건")
    for item in thesis.conditions_to_revisit:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 10. 근거")
    for ev in thesis.evidence:
        lines.append(f"- [{ev.doc_id}] {ev.support}")
    lines.append("")
    lines.append(guardrails.DISCLAIMER)
    return "\n".join(lines)


def build_safe_trace(state: AlphaArenaState, final_answer: str | None) -> list[ApiTrace]:
    """19.4: High-level, Sanitized Trace만 구성한다."""

    trace: list[ApiTrace] = []
    question = state.get("question", "")

    gr = state.get("guardrail_result")
    trace.append(
        ApiTrace(
            step="guardrail",
            input=_short(question),
            output=f"allowed={gr.allowed if gr else True}, reason={gr.reason_code if gr else 'ok'}",
        )
    )

    ticker = state.get("ticker")
    trace.append(ApiTrace(step="resolve_company", input=_short(question), output=f"ticker={ticker or 'unresolved'}"))

    ctx = state.get("company_context")
    if ctx is not None:
        trace.append(
            ApiTrace(step="company_context", input=str(ticker), output=f"snapshot_date={ctx.metrics.snapshot_date}")
        )

    opinions = state.get("round1_opinions") or []
    if opinions:
        contexts = state.get("contexts") or []
        for member in MEMBER_KEYS:
            count = sum(1 for c in contexts if c.member == member)
            trace.append(ApiTrace(step=f"retrieve_{member}", input=member, output=f"{count} passages"))
        trace.append(
            ApiTrace(
                step="round1",
                input="4 members",
                output=", ".join(f"{o.member}:{o.stance.value}" for o in opinions),
            )
        )

    reviews = state.get("debate_reviews") or []
    if reviews:
        trace.append(
            ApiTrace(
                step="debate",
                input="4 members",
                output=", ".join(f"{d.member}:changed={d.changed_view}" for d in reviews),
            )
        )

    thesis = state.get("final_thesis")
    if thesis is not None:
        trace.append(
            ApiTrace(step="chair", input="synthesize", output=f"verdict={thesis.verdict.value} confidence={thesis.confidence:.2f}")
        )

    if final_answer:
        check = guardrails.check_output(final_answer)
        trace.append(ApiTrace(step="output_guardrail", input="final answer", output=f"allowed={check.allowed}"))

    return trace


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------


def node_input_guardrail(state: AlphaArenaState) -> dict:
    """Graph 진입점(첫 Node). `question`만 읽고 `guardrail_result`를 State에 쓴다.

    Direct Prompt Injection / 실거래 실행 요청을 여기서 먼저 걸러내므로,
    이후 어떤 Tool/LLM 호출도 발생하기 전에 차단이 확정된다(REQUIREMENTS.md 18장).
    """

    trace_id = state["trace_id"]
    question = state["question"]
    with tracer.traced_step(trace_id, "guardrail", input_summary=question) as t:
        gr = guardrails.check_input(question)
        t.output_summary = f"allowed={gr.allowed} reason={gr.reason_code}"

    # 질문 원문은 절대 로그에 남기지 않고 길이만 기록한다(민감/불필요 정보 최소화).
    tag = _tag("guardrail")
    if gr.allowed:
        logger.info("%s PASS trace_id=%s query_len=%d duration_ms=%.0f", tag, trace_id, len(question), t.duration_ms)
    else:
        logger.info("%s blocked reason=%s trace_id=%s duration_ms=%.0f", tag, gr.reason_code, trace_id, t.duration_ms)

    return {"guardrail_result": gr}


def route_after_input_guardrail(state: AlphaArenaState) -> str:
    """`guardrail_result.allowed`만으로 분기 — 차단되면 즉시 finalize(안전 응답)로 보낸다."""

    gr = state["guardrail_result"]
    return "continue" if gr.allowed else "blocked"


def node_resolve_company(state: AlphaArenaState) -> dict:
    """`question`에서 결정론적으로 Ticker를 해석한다(LLM에 맡기지 않음, 5.3장).

    정확히 하나의 지원 Ticker만 발견되면 `ticker`를 State에 쓴다. 0개(미지원/식별
    불가) 또는 2개 이상(Multi-company 요청)이면 데이터를 지어내는 대신
    `guardrail_result`를 실패로 덮어써 finalize에서 안전 안내 문구를 반환하게 한다.
    """

    trace_id = state["trace_id"]
    question = state["question"]

    with tracer.traced_step(trace_id, "resolve_company", input_summary=question) as t:
        tickers = tools.resolve_tickers(question)
        if len(tickers) == 1:
            result = {"ticker": tickers[0]}
            t.output_summary = f"ticker={tickers[0]}"
        else:
            # 0개(미지원/식별 불가) 또는 2개 이상(Multi-company) 모두 같은 방식으로
            # 처리한다 — v0는 한 번에 하나의 기업만 분석한다(REQUIREMENTS.md 5.3).
            message = guardrails.unsupported_scope_message(question, tickers)
            result = {
                "ticker": None,
                "guardrail_result": GuardrailResult(
                    allowed=False, reason_code="unsupported_scope", user_message=message
                ),
            }
            t.output_summary = f"unsupported_scope tickers={tickers}"

    tag = _tag("resolve_company")
    if "ticker" in result and result["ticker"]:
        logger.info("%s completed ticker=%s trace_id=%s duration_ms=%.0f", tag, result["ticker"], trace_id, t.duration_ms)
    else:
        reason = result["guardrail_result"].reason_code
        logger.info("%s failed reason=%s trace_id=%s duration_ms=%.0f", tag, reason, trace_id, t.duration_ms)

    return result


def route_after_resolve_company(state: AlphaArenaState) -> str:
    """resolve_company가 guardrail_result를 실패로 덮어썼는지로 분기한다."""

    gr = state["guardrail_result"]
    return "continue" if gr.allowed else "blocked"


def node_load_company_context(state: AlphaArenaState) -> dict:
    """`ticker`로 Company Snapshot을 정확히 한 번만 읽어 4개 Member가 공유할
    `company_context`를 만든다(13장: 동일 Snapshot을 네 번 반복 조회하지 않음).

    동시에 이 Snapshot 자체도 `company_snapshot:{ticker}` doc_id로 `contexts`에
    추가한다 — 최종 답변의 근거가 RAG Passage뿐 아니라 기업 Snapshot에도
    연결되도록 하기 위함이다(19.3장).
    """

    trace_id = state["trace_id"]
    ticker = state["ticker"]

    tag = _tag("company_context")
    with tracer.traced_step(trace_id, "company_context", input_summary=ticker or "") as t:
        try:
            ctx = tools.load_company_context(ticker)
        except Exception as exc:  # noqa: BLE001 - 콘솔에 실패를 알린 뒤 그대로 재전파한다.
            logger.warning("%s failed trace_id=%s ticker=%s error_type=%s", tag, trace_id, ticker, type(exc).__name__)
            raise
        snapshot_context = EvidenceContext(
            doc_id=f"company_snapshot:{ticker}",
            text=_render_company_context(ctx),
            source_type="company_snapshot",
            title=f"{ctx.metrics.company_name} Snapshot ({ctx.metrics.snapshot_date})",
        )
        t.output_summary = f"loaded snapshot for {ticker}"

    logger.info("%s completed ticker=%s trace_id=%s duration_ms=%.0f", tag, ticker, trace_id, t.duration_ms)

    return {"company_context": ctx, "contexts": [snapshot_context]}


def node_round1_fanout(state: AlphaArenaState) -> dict:  # noqa: ARG001 - passthrough dispatcher
    """실제 작업은 하지 않는 Dispatcher Node. LangGraph에서 Send 기반 Fan-out은
    "어떤 Node의 Conditional Edge가 Send 목록을 반환하는가"로 정의되므로,
    Fan-out 지점을 그래프 상에서 명확히 표시하기 위한 자리표시자로만 존재한다."""

    return {}


def route_round1(state: AlphaArenaState) -> list[Send]:
    """Round 1 Fan-out: 4개 Member 각각에 `round1_member`를 병렬 실행시킨다.

    ★ Round 1 독립성 보장(REQUIREMENTS.md 9.2)의 핵심 지점: payload에는 질문/
    Ticker/공통 Company Context/자신의 member만 담고, 다른 Member의 의견이나
    중간 판단은 절대 포함하지 않는다. 각 round1_member 호출은 서로의 결과를
    볼 수 없는 별도 State(payload)로 실행되므로 Anchoring이 구조적으로 차단된다.
    """

    payload_base = {
        "trace_id": state["trace_id"],
        "question": state["question"],
        "ticker": state["ticker"],
        "company_context": state["company_context"],
    }
    return [Send("round1_member", {**payload_base, "member": member}) for member in MEMBER_KEYS]


def node_round1_member(payload: dict) -> dict:
    """4개의 병렬 분기 중 하나 — 특정 Member 한 명의 독립 Round 1 분석.

    1) 자신의 Guru Corpus만 검색(retrieve_guru_docs가 member로 필터링을 강제).
    2) Damodaran Lens에 한해 baseline DCF를 결정론적으로 계산해 Prompt에 덧붙인다
       (LLM에게 암산시키지 않음, 12.4장). 계산이 실패해도 전체 분석을 막지 않고
       실패 사유를 Prompt에 남겨 LLM이 "확인할 수 없음"으로 처리하게 한다.
    3) InvestmentOpinion Structured Output을 받은 뒤 member/lens 필드는 LLM
       출력을 신뢰하지 않고 여기서 강제로 덮어써, 이후 Debate 단계에서
       "own_opinion.member == 이 Member" 매칭이 항상 정확하도록 보장한다.

    반환값의 `round1_opinions`/`contexts`는 state.py의 `operator.add` Reducer로
    다른 3개 병렬 분기의 결과와 안전하게 합쳐진다(서로 덮어쓰지 않음).
    """

    trace_id = payload["trace_id"]
    member = payload["member"]
    question = payload["question"]
    ticker = payload["ticker"]
    ctx: CompanyContext = payload["company_context"]
    cfg = MEMBER_CONFIG[member]

    rag_tag = _member_rag_tag(member)
    with tracer.traced_step(trace_id, f"retrieve_{member}", input_summary=question, metadata={"member": member}) as t:
        try:
            rag_context = retrieve_guru_docs(member, question, top_k=settings.rag_top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s failed trace_id=%s error_type=%s", rag_tag, trace_id, type(exc).__name__)
            raise
        t.output_summary = f"{len(rag_context)} passages"

    logger.info("%s completed chunks=%d trace_id=%s duration_ms=%.0f", rag_tag, len(rag_context), trace_id, t.duration_ms)

    prompt_kwargs = {
        "question": question,
        "company_context": _render_company_context(ctx),
        "rag_context": _render_rag_context(rag_context),
    }

    if member == "damodaran":
        try:
            assumptions = tools.default_valuation_assumptions(ctx)
            valuation = tools.calculate_valuation(ticker=ticker, **assumptions)
            prompt_kwargs["valuation_result"] = _render_valuation(valuation)
        except Exception as exc:  # noqa: BLE001 - 계산 실패해도 전체 분석은 계속 진행한다.
            prompt_kwargs["valuation_result"] = f"(Valuation 계산 실패: {exc})"

    prompt_text = cfg["prompt"].format(**prompt_kwargs)

    round1_tag = _member_tag("round1", member)
    logger.info("%s started trace_id=%s", round1_tag, trace_id)
    with tracer.traced_step(trace_id, "round1", input_summary=member, metadata={"member": member}) as t:
        try:
            opinion: InvestmentOpinion = _invoke_structured(InvestmentOpinion, prompt_text)
        except Exception as exc:  # noqa: BLE001 - 콘솔에 실패를 알린 뒤 그대로 재전파(29.4 Retry는 이미 소진됨).
            logger.warning("%s failed trace_id=%s error_type=%s", round1_tag, trace_id, type(exc).__name__)
            raise
        # LLM이 member/lens 필드를 자유 형식으로 채웠을 수 있으므로 결정론적 값으로
        # 강제 치환한다 — Debate 단계의 member 매칭이 이 값에 의존한다.
        opinion = opinion.model_copy(update={"member": member, "lens": cfg["lens_title"]})
        t.output_summary = f"{member}: stance={opinion.stance.value} confidence={opinion.confidence}"

    logger.info(
        "%s completed stance=%s confidence=%s duration_ms=%.0f",
        round1_tag,
        opinion.stance.value,
        opinion.confidence,
        t.duration_ms,
    )

    return {"round1_opinions": [opinion], "contexts": list(rag_context)}


def node_collect_round1(state: AlphaArenaState) -> dict:  # noqa: ARG001 - join barrier
    """Fan-in Barrier. 4개의 `round1_member` 병렬 분기가 모두 `collect_round1`로
    향하는 Edge를 갖고 있으므로, LangGraph는 4개가 전부 끝난 뒤에야 이 Node
    (그리고 그다음 단계인 debate_fanout)를 실행한다. Round 1의 네 의견이 모두
    모인 뒤에만 Debate를 시작해야 하므로(9.3장) 이 barrier가 반드시 필요하다."""

    return {}


def node_debate_fanout(state: AlphaArenaState) -> dict:  # noqa: ARG001 - passthrough dispatcher
    """Round 1 Fan-out과 동일한 이유의 자리표시자 Node. `collect_round1` 뒤에만
    실행되므로, 이 시점에는 4개 Round 1 의견이 모두 State에 모여 있음이 보장된다."""

    return {}


def route_debate(state: AlphaArenaState) -> list[Send]:
    """Debate Fan-out: 이제(Round 1 종료 후)는 각 Member에게 나머지 3명의 의견을
    모두 보여준다(9.3장) — Round 1과 반대로 여기서는 의도적으로 다른 Member의
    의견(`other_opinions`)을 payload에 포함시킨다."""

    trace_id = state["trace_id"]
    question = state["question"]
    opinions = state["round1_opinions"]

    sends = []
    for opinion in opinions:
        others = [o for o in opinions if o.member != opinion.member]
        sends.append(
            Send(
                "debate_member",
                {
                    "trace_id": trace_id,
                    "question": question,
                    "member": opinion.member,
                    "own_opinion": opinion,
                    "other_opinions": others,
                },
            )
        )
    return sends


def node_debate_member(payload: dict) -> dict:
    """한 Member가 자신의 Round 1 의견을 나머지 3명의 의견과 비교해 검토한다.

    DebateReview Schema에는 confidence 필드가 없으므로(11.6장), Revision은
    "Stance만 갱신하고 나머지 필드는 Round 1 그대로 유지"라는 최소 범위로
    처리한다 — `changed_view=False`(의견 유지)도 정상 결과이며 활동성을 보여주기
    위해 억지로 의견을 바꾸게 하지 않는다.
    """

    trace_id = payload["trace_id"]
    member = payload["member"]
    question = payload["question"]
    own_opinion: InvestmentOpinion = payload["own_opinion"]
    other_opinions: list[InvestmentOpinion] = payload["other_opinions"]
    cfg = MEMBER_CONFIG[member]

    prompt_text = prompts.DEBATE_PROMPT.format(
        name=cfg["full_name"],
        question=question,
        own_opinion=own_opinion.model_dump_json(indent=2),
        other_opinions=json.dumps([o.model_dump() for o in other_opinions], ensure_ascii=False, indent=2),
    )

    debate_tag = _member_tag("debate", member)
    logger.info("%s started trace_id=%s", debate_tag, trace_id)
    with tracer.traced_step(trace_id, "debate", input_summary=member, metadata={"member": member}) as t:
        try:
            review: DebateReview = _invoke_structured(DebateReview, prompt_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s failed trace_id=%s error_type=%s", debate_tag, trace_id, type(exc).__name__)
            raise
        review = review.model_copy(update={"member": member})
        t.output_summary = f"{member}: changed_view={review.changed_view} revised_stance={review.revised_stance.value}"

    logger.info(
        "%s completed changed_view=%s revised_stance=%s duration_ms=%.0f",
        debate_tag,
        review.changed_view,
        review.revised_stance.value,
        t.duration_ms,
    )

    # Schema상 Debate가 갱신할 수 있는 것은 stance뿐이므로, Round 1 의견을
    # 복사하고 stance만 revised_stance로 교체해 Chair에게 전달할 최종 의견을 만든다.
    revised_opinion = own_opinion.model_copy(update={"stance": review.revised_stance})

    return {"debate_reviews": [review], "revised_opinions": [revised_opinion]}


def node_collect_revisions(state: AlphaArenaState) -> dict:  # noqa: ARG001 - join barrier
    """Debate Fan-in Barrier. 4개 `debate_member` 분기가 모두 끝나야 Chair가
    실행되도록 강제한다(Chair는 4명 전원의 Revision을 봐야 하므로 9.1장 필수)."""

    return {}


def node_arena_chair(state: AlphaArenaState) -> dict:
    """중립 Arena Chair 종합 — Round 1/Debate/Revision 전체를 한 번에 넘겨
    FinalThesis를 생성한다.

    다수결 금지, Confidence 평균으로 Verdict 결정 금지, 근거 있는 Minority
    View 보존 등 Chair가 지켜야 할 규칙은 CHAIR_PROMPT(prompts.py)에 명시되어
    있으며 이 함수는 그 규칙을 강제할 Context(Round1/Debate/Revised 전체)를
    빠짐없이 채워 넘기는 역할만 한다(16장).
    """

    trace_id = state["trace_id"]
    question = state["question"]
    ticker = state["ticker"]
    ctx = state["company_context"]

    prompt_text = prompts.CHAIR_PROMPT.format(
        question=question,
        ticker=ticker,
        company_context=_render_company_context(ctx),
        round1_opinions=json.dumps([o.model_dump() for o in state["round1_opinions"]], ensure_ascii=False, indent=2),
        debate_reviews=json.dumps([d.model_dump() for d in state["debate_reviews"]], ensure_ascii=False, indent=2),
        revised_opinions=json.dumps(
            [o.model_dump() for o in state["revised_opinions"]], ensure_ascii=False, indent=2
        ),
    )

    chair_tag = _tag("chair")
    logger.info("%s started trace_id=%s ticker=%s", chair_tag, trace_id, ticker)
    with tracer.traced_step(trace_id, "chair", input_summary=question) as t:
        try:
            thesis: FinalThesis = _invoke_structured(FinalThesis, prompt_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s failed trace_id=%s error_type=%s", chair_tag, trace_id, type(exc).__name__)
            raise
        # ticker는 LLM 자유 출력이 아니라 이미 확정된 값으로 강제 고정한다.
        thesis = thesis.model_copy(update={"ticker": ticker})
        t.output_summary = f"verdict={thesis.verdict.value} confidence={thesis.confidence}"

    logger.info(
        "%s completed verdict=%s confidence=%s duration_ms=%.0f",
        chair_tag,
        thesis.verdict.value,
        thesis.confidence,
        t.duration_ms,
    )

    return {"final_thesis": thesis}


def node_render_answer(state: AlphaArenaState) -> dict:
    """FinalThesis를 사람이 읽는 답변 문자열로 렌더링하고 Output Guardrail을
    적용한다(17장 Rendering + 18.4 Output Guardrail).

    1차 방어(Prompt)를 통과했더라도 결정론적 Post-check(`guardrails.check_output`)에서
    금지 표현/Secret Leak이 걸리면, 최대 1회의 통제된 Correction을 시도한다
    (29.4장: 무한 Retry 금지). 두 번째도 실패하면 원본 대신 고정된
    Safe Fallback 문구를 반환해 안전하지 않은 텍스트가 사용자에게 나가지 않게 한다.
    """

    trace_id = state["trace_id"]
    thesis = state["final_thesis"]
    draft = render_final_thesis(thesis, state["revised_opinions"])

    tag = _tag("output_guardrail")
    with tracer.traced_step(trace_id, "output_guardrail", input_summary="draft answer") as t:
        check = guardrails.check_output(draft)
        if check.allowed:
            final_answer = draft
            t.output_summary = "ok"
        else:
            corrected = _correct_output(draft, check.reason_code)
            check2 = guardrails.check_output(corrected)
            final_answer = corrected if check2.allowed else guardrails.SAFE_FALLBACK_MESSAGE
            t.output_summary = f"corrected reason={check.reason_code} final_ok={check2.allowed}"

    # t.duration_ms는 with-block의 finally에서 채워지므로 블록이 끝난 뒤(여기)
    # 읽어야 정확한 값이 나온다 — 블록 내부에서 읽으면 항상 0이 찍힌다.
    if check.allowed:
        logger.info("%s PASS trace_id=%s duration_ms=%.0f", tag, trace_id, t.duration_ms)
    else:
        logger.info(
            "%s corrected reason=%s final_ok=%s trace_id=%s duration_ms=%.0f",
            tag,
            check.reason_code,
            check2.allowed,
            trace_id,
            t.duration_ms,
        )

    return {"answer": final_answer}


def node_finalize(state: AlphaArenaState) -> dict:
    """모든 경로(정상 분석 / 입력 단계 차단 / Scope 차단)가 합류하는 마지막 Node.

    - 정상 경로: `render_answer`가 이미 `answer`를 채웠으므로 여기서는 손대지 않는다.
    - 차단 경로: `render_answer`가 전혀 실행되지 않아 `answer`가 비어 있으므로,
      guardrail_result.user_message(또는 고정 Fallback)를 answer로 채운다.

    두 경로 모두 마지막으로 `build_safe_trace`를 한 번만 호출해 API에 반환할
    Safe Trace를 구성한다 — `safe_trace`는 state.py에서 Reducer 없이(마지막
    쓰기가 유효) 선언되어 있으므로, 여러 Node가 부분적으로 append하지 않고
    이 한 곳에서만 최종 조립하는 것이 안전하다.
    """

    trace_id = state["trace_id"]
    tag = _tag("finalize")
    update: dict = {}
    answer = state.get("answer")
    early_exit = answer is None

    if early_exit:
        # 정상 경로(render_answer 실행됨)에는 early_exit 값을 아예 붙이지 않고,
        # 차단 경로에서만 어떤 이유(guardrail_result.reason_code)로 Early Exit
        # 했는지를 남긴다 — reason_code 하나로 어느 단계(01 입력 가드레일의
        # prompt_injection/trade_execution_request 또는 02 기업 식별의
        # unsupported_scope)에서 멈췄는지 항상 구분할 수 있다.
        gr = state.get("guardrail_result")
        answer = (gr.user_message if gr else None) or guardrails.SAFE_FALLBACK_MESSAGE
        update["answer"] = answer
        logger.info("%s completed early_exit=%s trace_id=%s", tag, gr.reason_code if gr else "unknown", trace_id)
    else:
        logger.info("%s completed trace_id=%s", tag, trace_id)

    update["safe_trace"] = build_safe_trace(state, final_answer=answer)
    return update


# ---------------------------------------------------------------------------
# Graph Assembly
# ---------------------------------------------------------------------------

_graph = None


def build_graph():
    """REQUIREMENTS.md 9.1의 논리 흐름을 LangGraph StateGraph로 조립한다.

    Node 이름/구현 방식은 스펙과 100% 동일하지 않아도 되지만(9.1: "구체적인
    구현 방식은 달라도 되지만 의미는 반드시 보존"), 아래 두 성질은 반드시
    유지한다.
    1) Round 1은 서로 독립적으로 실행된 뒤 한 곳(collect_round1)에 모인다.
    2) Debate는 Round 1이 전부 끝난 뒤에만 시작되고, Chair는 Debate가 전부
       끝난 뒤에만 실행된다.
    """

    builder = StateGraph(AlphaArenaState)

    builder.add_node("input_guardrail", node_input_guardrail)
    builder.add_node("resolve_company", node_resolve_company)
    builder.add_node("load_company_context", node_load_company_context)
    builder.add_node("round1_fanout", node_round1_fanout)
    builder.add_node("round1_member", node_round1_member)
    builder.add_node("collect_round1", node_collect_round1)
    builder.add_node("debate_fanout", node_debate_fanout)
    builder.add_node("debate_member", node_debate_member)
    builder.add_node("collect_revisions", node_collect_revisions)
    builder.add_node("arena_chair", node_arena_chair)
    builder.add_node("render_answer", node_render_answer)
    builder.add_node("finalize", node_finalize)

    builder.add_edge(START, "input_guardrail")
    builder.add_conditional_edges(
        "input_guardrail", route_after_input_guardrail, {"continue": "resolve_company", "blocked": "finalize"}
    )
    builder.add_conditional_edges(
        "resolve_company", route_after_resolve_company, {"continue": "load_company_context", "blocked": "finalize"}
    )
    builder.add_edge("load_company_context", "round1_fanout")
    builder.add_conditional_edges("round1_fanout", route_round1)
    builder.add_edge("round1_member", "collect_round1")
    builder.add_edge("collect_round1", "debate_fanout")
    builder.add_conditional_edges("debate_fanout", route_debate)
    builder.add_edge("debate_member", "collect_revisions")
    builder.add_edge("collect_revisions", "arena_chair")
    builder.add_edge("arena_chair", "render_answer")
    builder.add_edge("render_answer", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_query(question: str) -> AlphaArenaState:
    """src/app.py `/query`와 evaluation/*.py가 공유하는 단일 진입점.

    매 호출마다 새 `trace_id`(UUID)를 발급해 내부 JSONL Trace(logs/trace.jsonl)와
    이번 실행을 연결할 수 있게 한다. 그래프 자체는 최초 1회만 컴파일되어
    재사용된다(get_graph의 Lazy Singleton).
    """

    trace_id = str(uuid.uuid4())
    graph = get_graph()
    initial_state: AlphaArenaState = {"trace_id": trace_id, "question": question}

    # 콘솔 로그의 최상위 경계(Graph 밖). 이 함수가 Graph 전체를 감싸므로
    # trace_id별 총 소요 시간을 여기서만 정확히 잴 수 있다. 질문 원문은 남기지
    # 않고 길이만 기록한다.
    logger.info("[요청] query started trace_id=%s query_len=%d", trace_id, len(question))
    start = time.perf_counter()

    try:
        final_state = graph.invoke(initial_state)
    except Exception as exc:  # noqa: BLE001 - 콘솔에 실패를 알린 뒤 app.py가 Controlled 500으로 처리하도록 재전파.
        duration_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            "[요청] failed trace_id=%s error_type=%s duration_ms=%.0f", trace_id, type(exc).__name__, duration_ms
        )
        raise

    duration_ms = (time.perf_counter() - start) * 1000
    # early_exit은 finalize(08)가 이미 State에 answer를 채워뒀는지가 아니라
    # "전체 4-Member 분석까지 도달했는지"(final_thesis 존재 여부)로 판단한다
    # — Chair(06) 이후 render_answer(07)에서 실패해도 early_exit은 아니므로
    # 이 Boolean 하나만으로는 실패까지는 구분하지 못한다(그건 08 로그의
    # early_exit=<reason_code>가 알려준다).
    early_exit = final_state.get("final_thesis") is None
    logger.info(
        "[요청] completed trace_id=%s early_exit=%s duration_ms=%.0f",
        trace_id,
        str(early_exit).lower(),
        duration_ms,
    )
    return final_state
