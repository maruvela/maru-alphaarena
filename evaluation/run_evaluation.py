"""
evaluation/run_evaluation.py

REQUIREMENTS.md 23~25장(Evaluation Dataset / Runner / LLM-as-Judge)을 구현한다.

실행:
    python -m evaluation.run_evaluation

- evaluation/test_queries.csv를 읽어 전체 Case를 실행한다.
- 한 Case 실패가 전체 평가 실행을 중단시키지 않는다.
- Provider/Runtime Exception은 FAIL이 아니라 ERROR로 분류한다.
- 결과는 evaluation/results/ 아래 JSON(기계 판독용)과 Markdown(사람 판독용)으로 저장한다.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from src.agent import get_chat_model, run_query
from src.prompts import JUDGE_PROMPT

ROOT = Path(__file__).resolve().parents[1]
TEST_QUERIES_PATH = ROOT / "evaluation" / "test_queries.csv"
RESULTS_DIR = ROOT / "evaluation" / "results"

REQUIRED_COLUMNS = ["id", "category", "input", "expected_traits", "forbidden", "expected_tools", "note"]
ALLOWED_CATEGORIES = {"positive", "negative", "edge", "guardrail"}


class JudgeResult(BaseModel):
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str]


def load_test_queries(path: Path = TEST_QUERIES_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"필수 평가 자산이 없습니다: {path}. REQUIREMENTS.md 23장에 따라 "
            "공식 평가셋 없이는 평가를 실행할 수 없습니다."
        )

    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != REQUIRED_COLUMNS:
            raise ValueError(
                f"test_queries.csv 컬럼이 예상과 다릅니다. 필요: {REQUIRED_COLUMNS}, 실제: {reader.fieldnames}"
            )
        rows = list(reader)

    for row in rows:
        if row["category"] not in ALLOWED_CATEGORIES:
            raise ValueError(f"허용되지 않은 category: {row['category']} (id={row['id']})")

    return rows


def _judge(question: str, answer: str, expected_traits: str, forbidden: str) -> JudgeResult:
    prompt_text = JUDGE_PROMPT.format(
        question=question,
        answer=answer,
        expected_traits=expected_traits or "(없음)",
        forbidden=forbidden or "(없음)",
    )
    model = get_chat_model().with_structured_output(JudgeResult)
    return model.invoke(prompt_text)


def _check_expected_tools(expected_tools: str, final_state: dict) -> tuple[bool, str]:
    """
    Tool 사용 여부를 안전한 Internal State로부터 결정론적으로 판단한다.

    - get_company_metrics / get_financial_history: company_context가 로드되었는가
    - retrieve_guru_docs: Member Tag가 붙은 RAG Context가 존재하는가
    - calculate_valuation: v0 구현에서 Damodaran Member는 항상 baseline DCF를
      계산하므로, Final Thesis까지 도달했는지로 판단한다.
    """

    tool_names = [t.strip() for t in expected_tools.split(",") if t.strip()]
    if not tool_names:
        return True, "(no tools expected)"

    has_company_context = final_state.get("company_context") is not None
    has_rag_context = any(c.member is not None for c in (final_state.get("contexts") or []))
    has_final_thesis = final_state.get("final_thesis") is not None

    checks = {
        "get_company_metrics": has_company_context,
        "get_financial_history": has_company_context,
        "retrieve_guru_docs": has_rag_context,
        "calculate_valuation": has_final_thesis,
    }

    missing = [name for name in tool_names if not checks.get(name, False)]
    return (len(missing) == 0), (f"missing: {missing}" if missing else "ok")


def run_case(row: dict) -> dict:
    result: dict = {
        "id": row["id"],
        "category": row["category"],
        "input": row["input"],
        "answer": "",
        "contexts": [],
        "trace": [],
        "expected_traits_result": None,
        "forbidden_result": None,
        "expected_tools_result": None,
        "judge_reasons": [],
        "status": "ERROR",
    }

    try:
        final_state = run_query(row["input"])
    except Exception as exc:  # noqa: BLE001 - 29.3: Provider/Runtime Error는 ERROR로 분류
        result["status"] = "ERROR"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    answer = final_state.get("answer") or ""
    result["answer"] = answer
    result["contexts"] = [c.doc_id for c in (final_state.get("contexts") or [])]
    result["trace"] = [t.step for t in (final_state.get("safe_trace") or [])]

    tools_ok, tools_detail = _check_expected_tools(row["expected_tools"], final_state)
    result["expected_tools_result"] = tools_ok
    result["expected_tools_detail"] = tools_detail

    try:
        judge = _judge(row["input"], answer, row["expected_traits"], row["forbidden"])
    except Exception as exc:  # noqa: BLE001
        result["status"] = "ERROR"
        result["error"] = f"Judge {type(exc).__name__}: {exc}"
        return result

    result["expected_traits_result"] = judge.passed
    result["forbidden_result"] = judge.passed
    result["judge_score"] = judge.score
    result["judge_reasons"] = judge.reasons

    result["status"] = "PASS" if (judge.passed and tools_ok) else "FAIL"
    return result


def summarize(results: list[dict]) -> dict:
    total = len(results)
    by_category: dict[str, dict[str, int]] = {}

    for r in results:
        cat = r["category"]
        by_category.setdefault(cat, {"PASS": 0, "FAIL": 0, "ERROR": 0})
        by_category[cat][r["status"]] += 1

    overall_pass = sum(1 for r in results if r["status"] == "PASS")
    guardrail_results = [r for r in results if r["category"] == "guardrail"]
    guardrail_pass = sum(1 for r in guardrail_results if r["status"] == "PASS")
    positive_results = [r for r in results if r["category"] == "positive"]
    positive_false_block = sum(
        1 for r in positive_results if r["status"] != "PASS" and "unsupported_scope" in "".join(r.get("trace", []))
    )

    return {
        "total": total,
        "overall_pass_rate": overall_pass / total if total else 0.0,
        "by_category": by_category,
        "guardrail_pass_rate": (guardrail_pass / len(guardrail_results)) if guardrail_results else None,
        "positive_false_block_count": positive_false_block,
    }


def main() -> None:
    rows = load_test_queries()
    results = [run_case(row) for row in rows]
    summary = summarize(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    json_path = RESULTS_DIR / f"run_{timestamp}.json"
    json_path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md_lines = [
        f"# Evaluation Run {timestamp}",
        "",
        f"- Total cases: {summary['total']}",
        f"- Overall pass rate: {summary['overall_pass_rate']:.1%}",
        f"- Guardrail pass rate: {summary['guardrail_pass_rate']}",
        f"- Positive false-block count: {summary['positive_false_block_count']}",
        "",
        "## By category",
        "",
        "| category | PASS | FAIL | ERROR |",
        "|---|---:|---:|---:|",
    ]
    for cat, counts in summary["by_category"].items():
        md_lines.append(f"| {cat} | {counts['PASS']} | {counts['FAIL']} | {counts['ERROR']} |")

    md_lines.append("")
    md_lines.append("## Case Detail")
    md_lines.append("")
    md_lines.append("| id | category | status | judge_score |")
    md_lines.append("|---|---|---|---:|")
    for r in results:
        md_lines.append(f"| {r['id']} | {r['category']} | {r['status']} | {r.get('judge_score', '')} |")

    md_path = RESULTS_DIR / f"run_{timestamp}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"완료: {json_path}")
    print(f"완료: {md_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
