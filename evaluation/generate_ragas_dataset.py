"""
evaluation/generate_ragas_dataset.py

RAGAS 평가(REQUIREMENTS.md 26장)의 1단계: Alpha Arena 본체(src.agent)를 실행해
질문/답변/근거(Context)를 수집하고, RAGAS가 채점할 수 있는 형태로 저장한다.

이 스크립트는 **메인 앱 venv**(langgraph/chromadb/langchain-aws 최신 버전)에서
실행한다. RAGAS 채점 자체(2단계)는 별도 venv에서 실행하는
`evaluation/score_ragas_dataset.py`가 담당한다 — 이유는 `ragas`(구버전만
scikit-network 없이 설치 가능)가 요구하는 `langchain-community` 구버전과,
`src.agent`가 요구하는 최신 `langchain`/`langgraph` 생태계가 같은 venv 안에서
동시에 설치될 수 없기 때문이다(docs/how_to_use.md 11절 참고).

실행(메인 venv):
    python -m evaluation.generate_ragas_dataset
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.agent import run_query

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "evaluation" / "ragas_reference.csv"
TEST_QUERIES_PATH = ROOT / "evaluation" / "test_queries.csv"
DATASET_PATH = ROOT / "evaluation" / "results" / "ragas_dataset.json"


def _load_questions() -> dict[str, str]:
    with TEST_QUERIES_PATH.open("r", encoding="utf-8") as f:
        return {row["id"]: row["input"] for row in csv.DictReader(f)}


def _load_reference() -> list[dict]:
    if not REFERENCE_PATH.exists():
        raise FileNotFoundError(f"RAGAS Reference 파일이 없습니다: {REFERENCE_PATH}")
    with REFERENCE_PATH.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    questions_by_id = _load_questions()
    rows = []

    for ref in _load_reference():
        case_id = ref["id"]
        question = questions_by_id.get(case_id)
        if question is None:
            raise ValueError(f"test_queries.csv에서 id={case_id}를 찾지 못했습니다.")

        print(f"[{case_id}] 실행 중: {question}")
        final_state = run_query(question)
        contexts = [c.text for c in (final_state.get("contexts") or [])]

        rows.append(
            {
                "id": case_id,
                "question": question,
                "answer": final_state.get("answer") or "",
                "contexts": contexts,
                "ground_truth": ref["reference_answer"],
            }
        )
        print(f"[{case_id}] 완료 ({len(contexts)}개 context)")

    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료: {DATASET_PATH} ({len(rows)}건)")
    print("다음 단계: 별도 RAGAS venv에서 `python -m evaluation.score_ragas_dataset` 실행")


if __name__ == "__main__":
    main()
