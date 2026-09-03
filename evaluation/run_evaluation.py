"""
evaluation/run_evaluation.py

REQUIREMENTS.md 23~25장(Evaluation Dataset / Runner / LLM-as-Judge) 및
24.1장(Full Evaluation Run 보존 정책)을 구현한다.

실행:
    python -m evaluation.run_evaluation --round round1

핵심 설계:
- 매 실행은 독립적인 "Run"이며 `evaluation/runs/<run_id>/` 아래에 모든 산출물을
  보존한다. run_id는 `YYYYMMDD_HHMMSS_<round-name>` 형태이고, 이미 같은
  run_id 디렉터리가 있으면 실행 자체를 거부한다 — 과거 Run 결과를 절대
  덮어쓰지 않기 위함이다(24.1장).
- 한 Case 실패가 전체 실행을 중단시키지 않는다. Provider/Runtime Exception은
  FAIL로 위장하지 않고 ERROR로 기록한다(29.3장).
- Quota/Timeout처럼 "코드가 아니라 인프라가 원인"인 실패가 감지되면(또는
  실행이 중간에 중단되어 예정된 Case 수를 못 채우면) `run_manifest.json`에
  status="invalid"로 표시한다 — 삭제하지 않고 그대로 남겨서, 이후 Round 비교
  시 이 Run만 제외할 수 있게 한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from src.agent import get_chat_model, run_query
from src.config import settings
from src.prompts import JUDGE_PROMPT

ROOT = Path(__file__).resolve().parents[1]
TEST_QUERIES_PATH = ROOT / "evaluation" / "test_queries.csv"
RUNS_DIR = ROOT / "evaluation" / "runs"

REQUIRED_COLUMNS = ["id", "category", "input", "expected_traits", "forbidden", "expected_tools", "note"]
ALLOWED_CATEGORIES = {"positive", "negative", "edge", "guardrail"}

# round-name 형식: round1, round2, round2_retry01 처럼 "같은 Round의 인프라
# 재시도"와 "실제 개선 후 다음 Round"를 이름만으로 구분할 수 있게 강제한다
# (24.1장) — 자유 문자열을 허용하면 이 구분이 사람의 기억에만 의존하게 된다.
_ROUND_NAME_RE = re.compile(r"^round\d+(_retry\d+)?$")

# Bedrock/AWS Quota, Timeout 등 "코드 문제가 아니라 인프라 문제"임을 시사하는
# 예외 신호. 이런 신호가 하나라도 섞여 있으면 Run 전체를 invalid로 표시한다 —
# 실제로 Bedrock 일일 토큰 한도에 걸려 20건이 전부 ERROR로 나온 사례가 있었다.
_INFRA_ERROR_MARKERS = (
    "ThrottlingException",
    "ReadTimeoutError",
    "ConnectionError",
    "TimeoutError",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "ModelErrorException",
    "Too many tokens per day",
)

_MAX_ERROR_MESSAGE_LEN = 500


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


def _sanitize_error_message(exc: Exception) -> str:
    """Case 문서/JSON에 남길 예외 메시지를 안전한 범위로 정제한다 — 줄바꿈을
    없애고 길이를 제한해, 혹시 메시지에 섞여 있을 수 있는 장문의 내부 정보가
    그대로 파일에 쌓이는 것을 막는다(20장 Sanitize 원칙과 동일한 방식)."""

    text = str(exc).replace("\n", " ").strip()
    if len(text) > _MAX_ERROR_MESSAGE_LEN:
        text = text[:_MAX_ERROR_MESSAGE_LEN] + "…"
    return text


def run_case(row: dict, logger: logging.Logger) -> dict:
    """Test Case 하나를 실행하고, Case 문서(cases/<id>.md)를 만드는 데 필요한
    모든 실제 값(전체 answer/contexts/trace/Judge 결과/소요 시간)을 그대로
    보존한 dict를 반환한다 — 요약이나 절삭 없이 실제 값을 담는 것이 이번
    저장 구조 개선의 핵심이다(24.1장)."""

    result: dict = {
        "id": row["id"],
        "category": row["category"],
        "input": row["input"],
        "expected_traits": row["expected_traits"],
        "forbidden": row["forbidden"],
        "expected_tools": row["expected_tools"],
        "answer": "",
        "contexts": [],
        "trace": [],
        "expected_tools_result": None,
        "expected_tools_detail": None,
        "judge_passed": None,
        "judge_score": None,
        "judge_reasons": [],
        "error_type": None,
        "error_message": None,
        "status": "ERROR",
        "duration_ms": 0.0,
    }

    start = time.perf_counter()

    try:
        final_state = run_query(row["input"])
    except Exception as exc:  # noqa: BLE001 - 29.3: Provider/Runtime Error는 ERROR로 분류
        result["status"] = "ERROR"
        result["error_type"] = type(exc).__name__
        result["error_message"] = _sanitize_error_message(exc)
        result["duration_ms"] = (time.perf_counter() - start) * 1000
        logger.warning("[%s] run_query 실패: error_type=%s", row["id"], result["error_type"])
        return result

    answer = final_state.get("answer") or ""
    result["answer"] = answer
    # 요약(doc_id만)이 아니라 실제 Context 전체(도구가 실제로 무엇을 찾았는지
    # 재현 가능하도록 doc_id/chunk_id/member/title/text)를 그대로 보존한다.
    result["contexts"] = [
        {
            "doc_id": c.doc_id,
            "chunk_id": c.chunk_id,
            "member": c.member,
            "title": c.title,
            "source_type": c.source_type,
            "text": c.text,
        }
        for c in (final_state.get("contexts") or [])
    ]
    result["trace"] = [
        {"step": t.step, "input": t.input, "output": t.output} for t in (final_state.get("safe_trace") or [])
    ]

    tools_ok, tools_detail = _check_expected_tools(row["expected_tools"], final_state)
    result["expected_tools_result"] = tools_ok
    result["expected_tools_detail"] = tools_detail

    try:
        judge = _judge(row["input"], answer, row["expected_traits"], row["forbidden"])
    except Exception as exc:  # noqa: BLE001
        result["status"] = "ERROR"
        result["error_type"] = f"Judge{type(exc).__name__}"
        result["error_message"] = _sanitize_error_message(exc)
        result["duration_ms"] = (time.perf_counter() - start) * 1000
        logger.warning("[%s] Judge 호출 실패: error_type=%s", row["id"], result["error_type"])
        return result

    result["judge_passed"] = judge.passed
    result["judge_score"] = judge.score
    result["judge_reasons"] = judge.reasons

    result["status"] = "PASS" if (judge.passed and tools_ok) else "FAIL"
    result["duration_ms"] = (time.perf_counter() - start) * 1000
    logger.info(
        "[%s] %s duration_ms=%.0f judge_score=%.2f",
        row["id"],
        result["status"],
        result["duration_ms"],
        judge.score,
    )
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
        1
        for r in positive_results
        if r["status"] != "PASS" and any("unsupported_scope" in t.get("output", "") for t in r.get("trace", []))
    )

    return {
        "total": total,
        "pass_count": overall_pass,
        "fail_count": sum(1 for r in results if r["status"] == "FAIL"),
        "error_count": sum(1 for r in results if r["status"] == "ERROR"),
        "overall_pass_rate": overall_pass / total if total else 0.0,
        "by_category": by_category,
        "guardrail_pass_rate": (guardrail_pass / len(guardrail_results)) if guardrail_results else None,
        "positive_false_block_count": positive_false_block,
    }


def _detect_invalid(results: list[dict], expected_count: int) -> tuple[bool, str | None]:
    """Run 전체가 신뢰할 수 있는(valid) 결과인지 판단한다(24.1장).

    두 조건 중 하나라도 해당하면 invalid: (1) 예정된 Case 수를 다 채우지
    못하고 실행이 중단됨, (2) ERROR 중 Bedrock Quota/Timeout 같은 인프라
    신호가 섞여 있음. 코드/Prompt 자체의 문제로 인한 FAIL/ERROR는 invalid
    사유가 아니다 — 그건 Round Report가 분석해야 할 진짜 결과다.
    """

    if len(results) < expected_count:
        return True, f"incomplete run: {len(results)}/{expected_count} cases executed"

    infra_hits = [
        f"{r['id']}({r.get('error_type')})"
        for r in results
        if r["status"] == "ERROR"
        and any(marker in (r.get("error_message") or "") or marker in (r.get("error_type") or "") for marker in _INFRA_ERROR_MARKERS)
    ]
    if infra_hits:
        return True, "infra error detected in cases: " + ", ".join(infra_hits)

    return False, None


def _git_commit_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001 - Git 정보는 참고용이며 없어도 평가를 막지 않는다.
        return "unknown"


def _setup_run_logger(run_dir: Path) -> logging.Logger:
    """이 Run 전용 Logger. 콘솔에도 출력하고(기존처럼 진행 상황을 실시간으로
    보기 위해) 동시에 `evaluation.log` 파일에도 남긴다(24.1장 필수 산출물).
    `propagate=False`로 두어 src.config가 이미 구성해 둔 Root Logger의
    Handler와 겹쳐 콘솔에 중복 출력되지 않게 한다."""

    logger = logging.getLogger(f"alpha_arena.evaluation.{run_dir.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(run_dir / "evaluation.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def _render_case_markdown(result: dict) -> str:
    lines: list[str] = [
        f"# {result['id']}",
        "",
        f"- category: {result['category']}",
        f"- status: {result['status']}",
        f"- duration_ms: {result['duration_ms']:.0f}",
        "",
        "## Input",
        "",
        result["input"],
        "",
        "## Expected Traits",
        "",
        result["expected_traits"] or "(없음)",
        "",
        "## Forbidden",
        "",
        result["forbidden"] or "(없음)",
        "",
        "## Expected Tools",
        "",
        f"- 선언: {result['expected_tools'] or '(없음)'}",
        f"- 실제 판정: {result['expected_tools_result']} ({result['expected_tools_detail']})",
        "",
        "## Answer (전체)",
        "",
        result["answer"] or "(없음 — ERROR로 종료됨)",
        "",
        "## Contexts (실제 사용된 근거)",
        "",
    ]

    if result["contexts"]:
        for c in result["contexts"]:
            lines.append(f"### {c['doc_id']} ({c['chunk_id'] or '-'})")
            lines.append(f"- member: {c['member']}, source_type: {c['source_type']}, title: {c['title']}")
            lines.append("")
            lines.append(c["text"])
            lines.append("")
    else:
        lines.append("(없음)")
        lines.append("")

    lines.append("## Safe Trace (실제)")
    lines.append("")
    if result["trace"]:
        lines.append("| step | input | output |")
        lines.append("|---|---|---|")
        for t in result["trace"]:
            lines.append(f"| {t['step']} | {t['input']} | {t['output']} |")
    else:
        lines.append("(없음)")
    lines.append("")

    lines.append("## Judge Result")
    lines.append("")
    lines.append(f"- passed: {result['judge_passed']}")
    lines.append(f"- score: {result['judge_score']}")
    if result["judge_reasons"]:
        lines.append("- reasons:")
        for reason in result["judge_reasons"]:
            lines.append(f"  - {reason}")
    else:
        lines.append("- reasons: (없음)")
    lines.append("")

    lines.append("## Error")
    lines.append("")
    if result["error_type"]:
        lines.append(f"- error_type: {result['error_type']}")
        lines.append(f"- message: {result['error_message']}")
    else:
        lines.append("(없음)")
    lines.append("")

    return "\n".join(lines)


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--round",
        dest="round_name",
        required=True,
        help="round1, round2, round2_retry01 형식의 Round 이름(24.1장). "
        "인프라 실패로 인한 재실행은 같은 Round에 _retryNN을 붙인다.",
    )
    return parser.parse_args()


def validate_round_name(round_name: str) -> None:
    """round_name이 24.1장의 `roundN` / `roundN_retryNN` 형식을 따르는지
    검증한다. 자유 문자열을 허용하면 "인프라 재시도"와 "실제 개선 후 다음
    Round"의 구분이 파일명만으로는 불가능해진다."""

    if not _ROUND_NAME_RE.match(round_name):
        raise ValueError(
            f"--round 형식이 올바르지 않습니다: '{round_name}'. "
            "roundN 또는 roundN_retryNN 형식만 허용합니다(예: round1, round2_retry01)."
        )


def make_run_id(round_name: str, now: datetime | None = None) -> str:
    """`YYYYMMDD_HHMMSS_<round-name>` 형식의 run_id를 만든다(24.1장)."""

    validate_round_name(round_name)
    now = now or datetime.now()
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{round_name}"


def prepare_run_dir(run_id: str, runs_dir: Path = RUNS_DIR) -> tuple[Path, Path]:
    """run_dir/cases_dir를 만들어 반환한다. 동일한 run_id 디렉터리가 이미
    있으면 과거 Run 결과를 덮어쓰지 않기 위해 즉시 실패한다(24.1장)."""

    run_dir = runs_dir / run_id
    if run_dir.exists():
        raise FileExistsError(
            f"동일한 run_id 디렉터리가 이미 존재합니다: {run_dir}. "
            "과거 Run 결과를 덮어쓰지 않으므로 잠시 후(다음 초) 다시 시도하세요."
        )

    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True)
    return run_dir, cases_dir


def main() -> None:
    args = parse_args()
    round_name = args.round_name

    try:
        run_id = make_run_id(round_name)
        run_dir, cases_dir = prepare_run_dir(run_id)
    except (ValueError, FileExistsError) as exc:
        raise SystemExit(str(exc)) from exc

    logger = _setup_run_logger(run_dir)
    started_at = datetime.now(timezone.utc)

    rows = load_test_queries()

    # 실행 중 예기치 않게 죽더라도(예: 인프라 예외가 run_case의 방어망 밖에서
    # 터지는 극단적인 경우) 지금까지의 진행 상황을 담은 manifest를 남기기
    # 위해 status="running"으로 먼저 한 번 쓴다. 같은 run_id 안에서 이
    # 파일을 나중에 최종 상태로 덮어쓰는 것은 "과거 Run을 덮어쓰지 않는다"는
    # 규칙 위반이 아니다 — 이 Run 자신의 진행 상태를 갱신하는 것이다.
    manifest: dict = {
        "run_id": run_id,
        "round": round_name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": None,
        "status": "running",
        "git_commit": _git_commit_hash(),
        "test_dataset_path": str(TEST_QUERIES_PATH.relative_to(ROOT)),
        "test_case_count": len(rows),
        "model": settings.bedrock_model_id,
        "judge_model": settings.bedrock_model_id,
        "model_params": {
            "temperature": settings.model_temperature,
            "max_tokens": settings.model_max_tokens,
            "rag_top_k": settings.rag_top_k,
        },
        "company_snapshot_path": str(settings.company_snapshot_path.relative_to(ROOT)),
        "pass_count": None,
        "fail_count": None,
        "error_count": None,
        "pass_rate": None,
        "invalid_reason": None,
    }
    _write_json(run_dir / "run_manifest.json", manifest)

    logger.info("Run 시작: run_id=%s round=%s cases=%d", run_id, round_name, len(rows))

    results: list[dict] = []
    for row in rows:
        result = run_case(row, logger)
        results.append(result)
        (cases_dir / f"{result['id']}.md").write_text(_render_case_markdown(result), encoding="utf-8")

    summary = summarize(results)
    is_invalid, invalid_reason = _detect_invalid(results, expected_count=len(rows))
    finished_at = datetime.now(timezone.utc)

    manifest.update(
        {
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "status": "invalid" if is_invalid else "valid",
            "pass_count": summary["pass_count"],
            "fail_count": summary["fail_count"],
            "error_count": summary["error_count"],
            "pass_rate": summary["overall_pass_rate"],
            "invalid_reason": invalid_reason,
        }
    )

    _write_json(run_dir / "run_manifest.json", manifest)
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "results.json", results)
    _write_json(
        run_dir / "judge_results.json",
        [
            {
                "id": r["id"],
                "passed": r["judge_passed"],
                "score": r["judge_score"],
                "reasons": r["judge_reasons"],
            }
            for r in results
        ],
    )

    if is_invalid:
        logger.warning("Run invalid 처리됨: %s", invalid_reason)
    logger.info(
        "Run 종료: status=%s pass_rate=%.1f%% (%d/%d) run_dir=%s",
        manifest["status"],
        summary["overall_pass_rate"] * 100,
        summary["pass_count"],
        summary["total"],
        run_dir,
    )

    print(f"완료: {run_dir}")
    print(json.dumps({"manifest": manifest, "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
