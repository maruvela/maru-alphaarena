"""
src/tools.py

결정론적 Tool 구현. REQUIREMENTS.md 12장(Tool 계약), 13장(Company Context),
5.3(Ticker 해석)을 따른다.

- 회사 데이터 조회와 DCF 계산은 LLM Mental Arithmetic에 맡기지 않고
  여기 정의된 순수 Python 함수로만 수행한다.
- `data/company_snapshot.json`은 읽기 전용이며 실시간 API로 대체하지 않는다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from src.config import settings
from src.models import (
    CompanyContext,
    CompanyMetrics,
    FinancialHistory,
    HistoryPoint,
    SourceRef,
    ValuationInputs,
    ValuationResult,
)

SUPPORTED_TICKERS: tuple[str, ...] = ("NVDA", "COST", "INTC")

# 5.3 기업명 해석: 결정론적 키워드 매칭
_TICKER_ALIASES: dict[str, str] = {
    "nvda": "NVDA",
    "nvidia": "NVDA",
    "엔비디아": "NVDA",
    "cost": "COST",
    "costco": "COST",
    "코스트코": "COST",
    "intc": "INTC",
    "intel": "INTC",
    "인텔": "INTC",
}

# 긴 별칭부터 먼저 매칭되도록 정렬 (예: "nvda"가 "nvidia"보다 먼저 매칭되지 않게)
_ALIAS_PATTERN = re.compile(
    "|".join(sorted((re.escape(k) for k in _TICKER_ALIASES), key=len, reverse=True)),
    flags=re.IGNORECASE,
)


class CompanyDataError(ValueError):
    """지원하지 않는 Ticker 또는 Snapshot에 없는 데이터를 요청했을 때 발생."""


class ValuationInputError(ValueError):
    """calculate_valuation 입력 검증 실패 시 발생."""


def resolve_tickers(text: str) -> list[str]:
    """
    질문 텍스트에서 언급된 지원 Ticker를 결정론적으로 추출한다.

    NVDA / NVIDIA / 엔비디아 -> NVDA
    COST / Costco / 코스트코 -> COST
    INTC / Intel / 인텔 -> INTC

    등장 순서를 유지한 중복 제거 리스트를 반환한다. 발견하지 못하면 빈 리스트.
    """

    found: list[str] = []
    for match in _ALIAS_PATTERN.finditer(text):
        ticker = _TICKER_ALIASES[match.group(0).lower()]
        if ticker not in found:
            found.append(ticker)
    return found


def resolve_ticker(text: str) -> str | None:
    """단일 기업 식별. 정확히 하나의 지원 Ticker가 발견된 경우에만 반환한다."""

    tickers = resolve_tickers(text)
    if len(tickers) == 1:
        return tickers[0]
    return None


@lru_cache(maxsize=1)
def _load_snapshot() -> dict:
    """`data/company_snapshot.json`을 프로세스 생애주기 동안 1회만 읽는다(File I/O).

    파일은 읽기 전용 입력 자산이므로 캐시해도 안전하며, 매 Member/매 요청마다
    반복해서 디스크를 읽는 비용을 없앤다(13장: Snapshot 반복 조회 회피).
    """

    path = settings.company_snapshot_path
    if not path.exists():
        raise CompanyDataError(f"company_snapshot.json을 찾을 수 없습니다: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_company_raw(ticker: str) -> tuple[dict, dict]:
    """지원하지 않는 Ticker는 데이터를 지어내지 않고 즉시 Controlled Error(12.1장)로
    실패시킨다 — 호출부(agent.py)가 이를 잡아 안전한 Scope 안내로 변환한다."""

    snapshot = _load_snapshot()
    companies = snapshot.get("companies", {})
    if ticker not in companies:
        raise CompanyDataError(
            f"지원하지 않는 Ticker입니다: {ticker}. 지원 Ticker: {', '.join(SUPPORTED_TICKERS)}"
        )
    return snapshot, companies[ticker]


def get_company_metrics(ticker: str) -> CompanyMetrics:
    """
    Snapshot Date, Unit, Market Data, 최신 Financials, Return, Balance Sheet,
    Business Context, Source Metadata를 반환한다. 실시간 Market API는 호출하지 않는다.
    """

    snapshot, company = _get_company_raw(ticker)

    sources = [SourceRef(**s) for s in company.get("sources", [])]

    return CompanyMetrics(
        ticker=ticker,
        snapshot_date=snapshot["snapshot_date"],
        currency=snapshot["currency"],
        money_unit=snapshot["money_unit"],
        company_name=company["company_name"],
        sector=company["sector"],
        industry=company["industry"],
        evaluation_role=company["evaluation_role"],
        financial_period=company["financial_period"],
        market=company["market"],
        financials=company["financials"],
        returns=company["returns"],
        balance_sheet=company["balance_sheet"],
        business_context=company.get("business_context", {}),
        sources=sources,
    )


def get_financial_history(ticker: str) -> FinancialHistory:
    """고정된 연도별 revenue / operating_income / free_cash_flow 이력을 반환한다."""

    _, company = _get_company_raw(ticker)
    growth_history = company.get("growth_history", {})

    def _points(key: str) -> list[HistoryPoint]:
        return [HistoryPoint(**p) for p in growth_history.get(key, [])]

    return FinancialHistory(
        ticker=ticker,
        revenue=_points("revenue"),
        operating_income=_points("operating_income"),
        free_cash_flow=_points("free_cash_flow"),
    )


def load_company_context(ticker: str) -> CompanyContext:
    """
    get_company_metrics + get_financial_history를 한 번만 호출하여
    Member Fan-out 전에 공통 CompanyContext를 구성한다.
    """

    _, company = _get_company_raw(ticker)
    metrics = get_company_metrics(ticker)
    history = get_financial_history(ticker)

    valuation_inputs = ValuationInputs(**company["valuation_inputs"])

    return CompanyContext(
        ticker=ticker,
        metrics=metrics,
        history=history,
        valuation_inputs=valuation_inputs,
    )


def default_valuation_assumptions(context: CompanyContext) -> dict[str, float]:
    """
    Snapshot에 default_* 가정이 없을 때(v0의 모든 기업이 해당) 사용할
    보수적인 기본 Scenario 가정을 만든다.

    이 값은 객관적 Fact가 아니라 "주관적 가정"이며, 호출자는 반드시
    InvestmentOpinion.assumptions에 명시해야 한다.
    """

    vi = context.valuation_inputs
    revenue_growth_yoy = context.metrics.financials.get("revenue_growth_yoy") or 0.0

    growth_rate = vi.default_growth_rate
    if growth_rate is None:
        # 최근 YoY 성장률을 과도한 외삽 없이 완만하게 보정한다.
        growth_rate = max(min(revenue_growth_yoy, 0.15), -0.10)

    discount_rate = vi.default_discount_rate
    if discount_rate is None:
        discount_rate = vi.reference_wacc

    terminal_growth_rate = vi.default_terminal_growth_rate
    if terminal_growth_rate is None:
        terminal_growth_rate = 0.025

    return {
        "growth_rate": growth_rate,
        "discount_rate": discount_rate,
        "terminal_growth_rate": terminal_growth_rate,
        "horizon_years": 5,
    }


def calculate_valuation(
    ticker: str,
    growth_rate: float,
    discount_rate: float,
    terminal_growth_rate: float,
    horizon_years: int = 5,
) -> ValuationResult:
    """
    단순화된 2-Stage Scenario DCF. 모든 계산은 결정론적 Python으로 수행한다.

    FCF_t = base_fcf * (1 + g)^t
    Terminal Value = FCF_N * (1 + terminal_g) / (discount_rate - terminal_g)
    PV Enterprise Value = sum(FCF_t / (1+r)^t) + Terminal Value / (1+r)^N
    Equity Value = Enterprise Value - net_debt
    Intrinsic Value Per Share = Equity Value / shares_outstanding
    Upside/Downside = intrinsic_value_per_share / market_price - 1
    """

    # 12.4장 검증 규칙: Terminal Value 분모(discount_rate - terminal_g)가 0 이하가
    # 되지 않도록 사전에 막는다(0 이하이면 Terminal Value가 발산/음수가 된다).
    if discount_rate <= terminal_growth_rate:
        raise ValuationInputError("discount_rate는 terminal_growth_rate보다 커야 합니다.")
    if not (1 <= horizon_years <= 10):
        raise ValuationInputError("horizon_years는 1~10 사이여야 합니다.")
    if growth_rate <= -1.0:
        raise ValuationInputError("growth_rate는 -1.0보다 커야 합니다.")

    context = load_company_context(ticker)
    vi = context.valuation_inputs

    if vi.shares_outstanding <= 0:
        raise ValuationInputError("shares_outstanding은 0보다 커야 합니다.")

    # 모든 rate는 decimal(0.12 == 12%)이다. company_snapshot.json 전체가 이
    # 단위 규칙을 따르므로 여기서 별도 변환 없이 그대로 지수 계산에 사용한다.
    base_fcf = vi.base_fcf
    projected: list[HistoryPoint] = []
    pv_sum = 0.0

    fcf_t = base_fcf
    for t in range(1, horizon_years + 1):
        # FCF_t = base_fcf * (1+g)^t, 현재가치로 할인해 누적한다.
        fcf_t = base_fcf * (1 + growth_rate) ** t
        pv_sum += fcf_t / (1 + discount_rate) ** t
        projected.append(HistoryPoint(fiscal_year=f"T+{t}", value=fcf_t))

    # Terminal Value: 예측 구간 마지막 해(FCF_N) 이후를 영구성장모형으로 근사.
    terminal_value = fcf_t * (1 + terminal_growth_rate) / (discount_rate - terminal_growth_rate)
    pv_terminal_value = terminal_value / (1 + discount_rate) ** horizon_years

    pv_enterprise_value = pv_sum + pv_terminal_value
    # net_debt가 음수(순현금)면 Equity Value가 Enterprise Value보다 커진다 — 정상.
    equity_value = pv_enterprise_value - vi.net_debt
    intrinsic_value_per_share = equity_value / vi.shares_outstanding

    market_price = context.metrics.market.get("price")
    warnings: list[str] = []
    if market_price is None:
        # 29.2: 없는 데이터를 생성하지 않는다 — 0으로 채우되 Warning으로
        # 한계를 명시해 호출자(LLM Prompt)가 확정적으로 서술하지 않게 한다.
        warnings.append("Snapshot에 market price가 없어 upside/downside를 계산할 수 없습니다.")
        upside_downside = 0.0
    else:
        upside_downside = intrinsic_value_per_share / market_price - 1

    return ValuationResult(
        ticker=ticker,
        is_scenario=True,
        growth_rate=growth_rate,
        discount_rate=discount_rate,
        terminal_growth_rate=terminal_growth_rate,
        horizon_years=horizon_years,
        projected_fcf=projected,
        terminal_value=terminal_value,
        pv_enterprise_value=pv_enterprise_value,
        equity_value=equity_value,
        intrinsic_value_per_share=intrinsic_value_per_share,
        market_price=market_price or 0.0,
        upside_downside=upside_downside,
        warnings=warnings,
    )
