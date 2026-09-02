from unittest.mock import patch

from fastapi.testclient import TestClient

from src.app import app
from src.models import ApiTrace, EvidenceContext

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def _fake_final_state():
    return {
        "answer": "근거 기반 응답 예시",
        "contexts": [
            EvidenceContext(doc_id="buffett_1983", chunk_id="buffett_1983#passage-001", member="buffett", text="a"),
            EvidenceContext(doc_id="buffett_1983", chunk_id="buffett_1983#passage-001", member="buffett", text="a"),
            EvidenceContext(doc_id="company_snapshot:NVDA", text="snapshot", source_type="company_snapshot"),
        ],
        "safe_trace": [ApiTrace(step="guardrail", input="q", output="allowed=True, reason=ok")],
    }


def test_query_returns_required_top_level_fields():
    with patch("src.app.run_query", return_value=_fake_final_state()):
        response = client.post("/query", json={"question": "NVDA를 네 가지 투자 관점으로 분석해줘"})

    assert response.status_code == 200
    body = response.json()
    assert set(["answer", "contexts", "trace"]).issubset(body.keys())
    assert body["answer"] == "근거 기반 응답 예시"


def test_query_contexts_are_deduplicated_and_minimal_shape():
    with patch("src.app.run_query", return_value=_fake_final_state()):
        response = client.post("/query", json={"question": "NVDA 분석"})

    body = response.json()
    assert len(body["contexts"]) == 2
    for ctx in body["contexts"]:
        assert "doc_id" in ctx
        assert "text" in ctx


def test_query_trace_entries_have_step_input_output():
    with patch("src.app.run_query", return_value=_fake_final_state()):
        response = client.post("/query", json={"question": "NVDA 분석"})

    body = response.json()
    assert body["trace"][0]["step"] == "guardrail"


def test_query_requires_question_field():
    response = client.post("/query", json={})
    assert response.status_code == 422


def test_query_provider_error_returns_controlled_500():
    with patch("src.app.run_query", side_effect=RuntimeError("boom")):
        response = client.post("/query", json={"question": "NVDA 분석"})
    assert response.status_code == 500


def test_query_response_declares_utf8_charset():
    """Windows PowerShell 5.1의 Invoke-RestMethod는 응답 Content-Type에 charset이
    없으면 UTF-8 대신 다른 인코딩으로 잘못 해석해 한글이 깨진다(실측, docs/
    how_to_use.md 7절). `UTF8JSONResponse`가 이를 막고 있는지 확인한다."""

    with patch("src.app.run_query", return_value=_fake_final_state()):
        response = client.post("/query", json={"question": "NVDA 분석"})

    assert response.headers["content-type"] == "application/json; charset=utf-8"


def test_health_response_also_declares_utf8_charset():
    """`default_response_class`를 앱 레벨로 지정했으므로 `/health`를 포함한 모든
    Endpoint가 동일하게 charset을 명시해야 한다."""

    response = client.get("/health")
    assert response.headers["content-type"] == "application/json; charset=utf-8"


def test_query_response_body_is_valid_utf8_with_korean_preserved():
    """실제 HTTP Byte Stream이 올바른 UTF-8이고, 한글이 손실 없이 왕복되는지
    확인한다(Latin-1 등 다른 인코딩으로 잘못 디코딩하면 이 값과 달라진다)."""

    long_korean_answer = "결론: " + ("가격 대비 매출 성장률과 위험을 함께 평가해야 한다. " * 50)
    fake_state = {
        **_fake_final_state(),
        "answer": long_korean_answer,
    }

    with patch("src.app.run_query", return_value=fake_state):
        response = client.post("/query", json={"question": "NVDA 분석"})

    decoded = response.content.decode("utf-8")
    assert long_korean_answer in decoded
    # 잘못된 인코딩(Latin-1)으로 디코딩하면 한글이 깨져 원문과 달라야 한다 —
    # 이 비교 자체가 "제대로 디코딩했을 때만 원문과 일치한다"는 것을 보증한다.
    mis_decoded = response.content.decode("latin-1")
    assert long_korean_answer not in mis_decoded
