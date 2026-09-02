import pytest

from src.tools import ValuationInputError, calculate_valuation, load_company_context


def test_calculate_valuation_matches_manual_dcf():
    ticker = "COST"
    ctx = load_company_context(ticker)
    vi = ctx.valuation_inputs

    growth_rate = 0.05
    discount_rate = 0.09
    terminal_growth_rate = 0.025
    horizon_years = 5

    result = calculate_valuation(
        ticker=ticker,
        growth_rate=growth_rate,
        discount_rate=discount_rate,
        terminal_growth_rate=terminal_growth_rate,
        horizon_years=horizon_years,
    )

    pv_sum = 0.0
    fcf_t = vi.base_fcf
    for t in range(1, horizon_years + 1):
        fcf_t = vi.base_fcf * (1 + growth_rate) ** t
        pv_sum += fcf_t / (1 + discount_rate) ** t

    terminal_value = fcf_t * (1 + terminal_growth_rate) / (discount_rate - terminal_growth_rate)
    pv_terminal_value = terminal_value / (1 + discount_rate) ** horizon_years
    enterprise_value = pv_sum + pv_terminal_value
    equity_value = enterprise_value - vi.net_debt
    expected_intrinsic = equity_value / vi.shares_outstanding

    assert result.intrinsic_value_per_share == pytest.approx(expected_intrinsic, rel=1e-9)
    assert result.is_scenario is True

    market_price = ctx.metrics.market["price"]
    expected_upside = expected_intrinsic / market_price - 1
    assert result.upside_downside == pytest.approx(expected_upside, rel=1e-9)


def test_discount_rate_must_exceed_terminal_growth():
    with pytest.raises(ValuationInputError):
        calculate_valuation(
            ticker="NVDA", growth_rate=0.1, discount_rate=0.02, terminal_growth_rate=0.03, horizon_years=5
        )


def test_horizon_years_must_be_in_range():
    with pytest.raises(ValuationInputError):
        calculate_valuation(
            ticker="NVDA", growth_rate=0.1, discount_rate=0.1, terminal_growth_rate=0.02, horizon_years=0
        )
    with pytest.raises(ValuationInputError):
        calculate_valuation(
            ticker="NVDA", growth_rate=0.1, discount_rate=0.1, terminal_growth_rate=0.02, horizon_years=11
        )


def test_growth_rate_must_exceed_negative_one():
    with pytest.raises(ValuationInputError):
        calculate_valuation(
            ticker="NVDA", growth_rate=-1.0, discount_rate=0.1, terminal_growth_rate=0.02, horizon_years=5
        )


def test_intc_missing_market_price_is_not_fabricated():
    # INTC pe_ttm은 null이지만 market price는 존재하므로 정상 계산되어야 한다.
    result = calculate_valuation(
        ticker="INTC", growth_rate=0.03, discount_rate=0.15, terminal_growth_rate=0.025, horizon_years=5
    )
    assert result.market_price > 0
    assert result.warnings == []
