import pytest

from src.tools import (
    SUPPORTED_TICKERS,
    CompanyDataError,
    get_company_metrics,
    get_financial_history,
    load_company_context,
)


@pytest.mark.parametrize("ticker", SUPPORTED_TICKERS)
def test_get_company_metrics_supported(ticker):
    metrics = get_company_metrics(ticker)
    assert metrics.ticker == ticker
    assert metrics.company_name
    assert metrics.snapshot_date == "2026-08-31"
    assert "price" in metrics.market
    assert "revenue" in metrics.financials


@pytest.mark.parametrize("ticker", SUPPORTED_TICKERS)
def test_get_financial_history_has_three_years(ticker):
    history = get_financial_history(ticker)
    assert len(history.revenue) == 3
    assert len(history.operating_income) == 3
    assert len(history.free_cash_flow) == 3


@pytest.mark.parametrize("ticker", SUPPORTED_TICKERS)
def test_load_company_context(ticker):
    ctx = load_company_context(ticker)
    assert ctx.ticker == ticker
    assert ctx.valuation_inputs.shares_outstanding > 0


def test_unsupported_ticker_raises_controlled_error():
    with pytest.raises(CompanyDataError):
        get_company_metrics("TSLA")


def test_percentages_are_decimal_not_pp():
    # 5.2: 비율은 decimal 형식. NVDA revenue_growth_yoy == 0.8338 (83.38%)이지
    # 83.38 그 자체가 아니어야 한다.
    metrics = get_company_metrics("NVDA")
    growth = metrics.financials["revenue_growth_yoy"]
    assert 0 < growth < 5
