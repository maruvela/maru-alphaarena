"""
evaluation/run_evaluation.py의 Run 저장 구조(REQUIREMENTS.md 24.1장)를
검증하는 결정론적(Deterministic) 테스트. Bedrock을 호출하지 않는다 —
run_query/_judge는 전부 monkeypatch로 대체한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from evaluation import run_evaluation as ev


# ---- round_name / run_id --------------------------------------------------


@pytest.mark.parametrize("round_name", ["round1", "round2", "round10", "round2_retry01", "round2_retry12"])
def test_validate_round_name_accepts_valid_formats(round_name):
    ev.validate_round_name(round_name)  # 예외가 없어야 통과


@pytest.mark.parametrize(
    "round_name",
    ["Round1", "round", "round1_final", "round1-retry01", "retry01", "round01_retry", ""],
)
def test_validate_round_name_rejects_invalid_formats(round_name):
    with pytest.raises(ValueError):
        ev.validate_round_name(round_name)


def test_make_run_id_uses_timestamp_and_round_name():
    fixed_now = datetime(2026, 9, 2, 16, 30, 0)
    run_id = ev.make_run_id("round1", now=fixed_now)
    assert run_id == "20260902_163000_round1"


def test_make_run_id_rejects_invalid_round_name():
    with pytest.raises(ValueError):
        ev.make_run_id("not-a-round-name")


def test_prepare_run_dir_creates_run_and_cases_dir(tmp_path):
    run_dir, cases_dir = ev.prepare_run_dir("20260902_163000_round1", runs_dir=tmp_path)
    assert run_dir == tmp_path / "20260902_163000_round1"
    assert run_dir.is_dir()
    assert cases_dir == run_dir / "cases"
    assert cases_dir.is_dir()


def test_prepare_run_dir_refuses_to_overwrite_existing_run(tmp_path):
    ev.prepare_run_dir("20260902_163000_round1", runs_dir=tmp_path)
    with pytest.raises(FileExistsError):
        ev.prepare_run_dir("20260902_163000_round1", runs_dir=tmp_path)


# ---- load_test_queries ------------------------------------------------------


def test_load_test_queries_loads_the_approved_dataset():
    rows = ev.load_test_queries()
    assert len(rows) == 20
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    for row in rows:
        assert row["category"] in ev.ALLOWED_CATEGORIES


# ---- summarize --------------------------------------------------------------


def _fake_result(id_, category, status):
    return {
        "id": id_,
        "category": category,
        "status": status,
        "trace": [],
    }


def test_summarize_counts_pass_fail_error_by_category():
    results = [
        _fake_result("P01", "positive", "PASS"),
        _fake_result("P02", "positive", "FAIL"),
        _fake_result("G01", "guardrail", "PASS"),
        _fake_result("G02", "guardrail", "ERROR"),
    ]
    summary = ev.summarize(results)

    assert summary["total"] == 4
    assert summary["pass_count"] == 2
    assert summary["fail_count"] == 1
    assert summary["error_count"] == 1
    assert summary["overall_pass_rate"] == 0.5
    assert summary["by_category"]["positive"] == {"PASS": 1, "FAIL": 1, "ERROR": 0}
    assert summary["by_category"]["guardrail"] == {"PASS": 1, "FAIL": 0, "ERROR": 1}
    assert summary["guardrail_pass_rate"] == 0.5


# ---- _detect_invalid ---------------------------------------------------------


def test_detect_invalid_flags_incomplete_run():
    results = [_fake_result("P01", "positive", "PASS")]
    is_invalid, reason = ev._detect_invalid(results, expected_count=20)
    assert is_invalid is True
    assert "incomplete" in reason


def test_detect_invalid_flags_infra_error_marker():
    results = [
        {
            "id": "P01",
            "category": "positive",
            "status": "ERROR",
            "error_type": "ThrottlingException",
            "error_message": "Too many tokens per day",
        }
    ]
    is_invalid, reason = ev._detect_invalid(results, expected_count=1)
    assert is_invalid is True
    assert "P01" in reason


def test_detect_invalid_is_false_for_genuine_fail_and_error():
    results = [
        _fake_result("P01", "positive", "PASS"),
        {
            "id": "P02",
            "category": "positive",
            "status": "ERROR",
            "error_type": "ValidationError",
            "error_message": "FinalThesis missing required field 'rationale'",
        },
    ]
    is_invalid, reason = ev._detect_invalid(results, expected_count=2)
    assert is_invalid is False
    assert reason is None


# ---- _sanitize_error_message --------------------------------------------------


def test_sanitize_error_message_strips_newlines_and_truncates():
    exc = Exception("line1\nline2\n" + ("x" * 1000))
    sanitized = ev._sanitize_error_message(exc)
    assert "\n" not in sanitized
    assert len(sanitized) <= ev._MAX_ERROR_MESSAGE_LEN + 1  # +1 for the "…" marker


# ---- _check_expected_tools -----------------------------------------------------


def test_check_expected_tools_passes_when_all_present():
    final_state = {
        "company_context": object(),
        "contexts": [type("C", (), {"member": "buffett"})()],
        "final_thesis": object(),
    }
    ok, detail = ev._check_expected_tools(
        "get_company_metrics, retrieve_guru_docs, calculate_valuation", final_state
    )
    assert ok is True


def test_check_expected_tools_fails_when_missing():
    final_state = {"company_context": None, "contexts": [], "final_thesis": None}
    ok, detail = ev._check_expected_tools("get_company_metrics", final_state)
    assert ok is False
    assert "get_company_metrics" in detail


def test_check_expected_tools_empty_expectation_is_always_ok():
    ok, detail = ev._check_expected_tools("", {})
    assert ok is True


# ---- run_case (run_query/_judge monkeypatched — no Bedrock) --------------------


class _FakeContext:
    def __init__(self):
        self.doc_id = "buffett_letters_1989"
        self.chunk_id = "c0"
        self.member = "buffett"
        self.title = "1989 Letter"
        self.source_type = "guru_doc"
        self.text = "Price is what you pay, value is what you get."


class _FakeTraceEvent:
    def __init__(self, step, input_, output):
        self.step = step
        self.input = input_
        self.output = output


class _FakeJudgeResult:
    def __init__(self, passed, score, reasons):
        self.passed = passed
        self.score = score
        self.reasons = reasons


def test_run_case_returns_error_status_when_run_query_raises(monkeypatch):
    def _raise(_question):
        raise RuntimeError("ThrottlingException: Too many tokens per day")

    monkeypatch.setattr(ev, "run_query", _raise)

    logger = logging.getLogger("test")
    row = {
        "id": "P99",
        "category": "positive",
        "input": "AAPL 저평가야?",
        "expected_traits": "",
        "forbidden": "",
        "expected_tools": "get_company_metrics",
    }

    result = ev.run_case(row, logger)

    assert result["status"] == "ERROR"
    assert result["error_type"] == "RuntimeError"
    assert "ThrottlingException" in result["error_message"]
    assert result["duration_ms"] > 0


def test_run_case_returns_pass_when_judge_passes_and_tools_ok(monkeypatch):
    fake_final_state = {
        "answer": "AAPL은 현재 밸류에이션 대비 저평가 상태입니다.",
        "contexts": [_FakeContext()],
        "safe_trace": [_FakeTraceEvent("guardrail", "AAPL...", "ok")],
        "company_context": object(),
        "final_thesis": object(),
    }
    monkeypatch.setattr(ev, "run_query", lambda _q: fake_final_state)
    monkeypatch.setattr(ev, "_judge", lambda *a, **k: _FakeJudgeResult(True, 0.9, ["근거가 충분함"]))

    logger = logging.getLogger("test")
    row = {
        "id": "P01",
        "category": "positive",
        "input": "AAPL 저평가야?",
        "expected_traits": "저평가 여부에 대한 근거 제시",
        "forbidden": "",
        "expected_tools": "get_company_metrics, retrieve_guru_docs",
    }

    result = ev.run_case(row, logger)

    assert result["status"] == "PASS"
    assert result["judge_score"] == 0.9
    assert result["contexts"][0]["doc_id"] == "buffett_letters_1989"
    assert result["trace"][0]["step"] == "guardrail"


def test_run_case_returns_fail_when_judge_fails(monkeypatch):
    fake_final_state = {
        "answer": "무조건 지금 사세요!",
        "contexts": [],
        "safe_trace": [],
        "company_context": object(),
        "final_thesis": object(),
    }
    monkeypatch.setattr(ev, "run_query", lambda _q: fake_final_state)
    monkeypatch.setattr(
        ev, "_judge", lambda *a, **k: _FakeJudgeResult(False, 0.1, ["매수 강요 표현이 포함됨"])
    )

    logger = logging.getLogger("test")
    row = {
        "id": "P02",
        "category": "positive",
        "input": "AAPL 사야해?",
        "expected_traits": "",
        "forbidden": "매수 강요",
        "expected_tools": "",
    }

    result = ev.run_case(row, logger)
    assert result["status"] == "FAIL"


# ---- _render_case_markdown ------------------------------------------------------


def test_render_case_markdown_contains_all_required_sections():
    result = {
        "id": "P01",
        "category": "positive",
        "status": "PASS",
        "duration_ms": 1234.5,
        "input": "AAPL 저평가야?",
        "expected_traits": "근거 제시",
        "forbidden": "",
        "expected_tools": "get_company_metrics",
        "expected_tools_result": True,
        "expected_tools_detail": "ok",
        "answer": "AAPL은 저평가 상태로 보입니다.",
        "contexts": [
            {
                "doc_id": "buffett_letters_1989",
                "chunk_id": "c0",
                "member": "buffett",
                "title": "1989 Letter",
                "source_type": "guru_doc",
                "text": "Price is what you pay.",
            }
        ],
        "trace": [{"step": "guardrail", "input": "AAPL...", "output": "ok"}],
        "judge_passed": True,
        "judge_score": 0.9,
        "judge_reasons": ["근거 충분"],
        "error_type": None,
        "error_message": None,
    }

    md = ev._render_case_markdown(result)

    for heading in (
        "# P01",
        "## Input",
        "## Expected Traits",
        "## Forbidden",
        "## Expected Tools",
        "## Answer (전체)",
        "## Contexts (실제 사용된 근거)",
        "## Safe Trace (실제)",
        "## Judge Result",
        "## Error",
    ):
        assert heading in md

    assert "AAPL은 저평가 상태로 보입니다." in md
    assert "buffett_letters_1989" in md
