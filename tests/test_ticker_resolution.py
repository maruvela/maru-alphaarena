from src.tools import resolve_ticker, resolve_tickers


def test_nvda_aliases():
    assert resolve_ticker("NVDA 분석해줘") == "NVDA"
    assert resolve_ticker("NVIDIA는 어떤가요") == "NVDA"
    assert resolve_ticker("엔비디아 투자 매력 알려줘") == "NVDA"


def test_cost_aliases():
    assert resolve_ticker("COST 분석") == "COST"
    assert resolve_ticker("Costco 리서치") == "COST"
    assert resolve_ticker("코스트코 어떤가요") == "COST"


def test_intc_aliases():
    assert resolve_ticker("INTC 분석") == "INTC"
    assert resolve_ticker("Intel 턴어라운드") == "INTC"
    assert resolve_ticker("인텔 리스크는") == "INTC"


def test_unsupported_ticker_returns_empty():
    assert resolve_tickers("TSLA 분석해줘") == []
    assert resolve_ticker("TSLA 분석해줘") is None


def test_multi_company_detected():
    tickers = resolve_tickers("NVDA와 코스트코를 비교해줘")
    assert tickers == ["NVDA", "COST"]
    assert resolve_ticker("NVDA와 코스트코를 비교해줘") is None


def test_case_insensitive_english_alias():
    assert resolve_ticker("nvda 분석") == "NVDA"
    assert resolve_ticker("costco research") == "COST"
