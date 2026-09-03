"""
evaluation/score_ragas_dataset.py

RAGAS 평가(REQUIREMENTS.md 26장)의 2단계: `generate_ragas_dataset.py`가 만든
question/answer/contexts/ground_truth 데이터셋에 대해 실제 RAGAS 4개 지표
(context_recall, context_precision, faithfulness, answer_relevancy)를 계산한다.

이 스크립트는 **별도의 RAGAS 전용 venv**에서 실행한다(예: `.venv-ragas`).
`ragas`의 scikit-network 없는 구버전(0.2.x)은 구버전 `langchain-community`를
요구하는데, 이는 `src.agent`가 쓰는 최신 `langchain`/`langgraph`와 같은 venv에
공존할 수 없다. 그래서 이 스크립트는 `src.agent`/`src.retriever` 등 무거운
런타임 의존성을 가진 모듈은 import하지 않고, `src.config`(dotenv 외 의존성
없음)만 재사용해 AWS 설정값을 일관되게 읽는다.

준비(최초 1회):
    python -m venv .venv-ragas
    source .venv-ragas/Scripts/activate
    pip install "ragas==0.2.15" "langchain-aws==0.2.20" python-dotenv datasets

실행:
    source .venv-ragas/Scripts/activate
    python -m evaluation.score_ragas_dataset
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# ragas.executor는 import 시점에 무조건 nest_asyncio.apply()를 호출해 asyncio를
# 전역 패치한다. 이 스크립트는 (Jupyter가 아닌) 평범한 top-level 스크립트라
# 중첩 이벤트 루프 지원이 필요 없는데, Python 3.14에서는 이 패치가 오히려
# asyncio.wait_for의 내부 Task 처리와 충돌해
# "RuntimeError: Timeout should be used inside a task"를 유발한다. ragas를
# import하기 전에 nest_asyncio.apply를 무해한 no-op으로 바꿔 이 패치 자체를
# 막는다(우리는 중첩 이벤트 루프가 필요 없으므로 안전하다).
import nest_asyncio  # noqa: E402

nest_asyncio.apply = lambda *args, **kwargs: None  # noqa: E402

from datasets import Dataset  # noqa: E402
from langchain_aws import BedrockEmbeddings, ChatBedrock  # noqa: E402
from ragas import evaluate
from ragas.run_config import RunConfig  # noqa: E402
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

from src.config import settings

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evaluation" / "results" / "ragas_dataset.json"
RESULTS_DIR = ROOT / "evaluation" / "results"


def main() -> None:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"{DATASET_PATH}가 없습니다. 먼저 메인 venv에서 "
            "`python -m evaluation.generate_ragas_dataset`를 실행하세요."
        )

    rows = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    dataset = Dataset.from_dict(
        {
            "question": [r["question"] for r in rows],
            "answer": [r["answer"] for r in rows],
            "contexts": [r["contexts"] for r in rows],
            "ground_truth": [r["ground_truth"] for r in rows],
        }
    )

    # Judge/Evaluation Model 재현성(21장): Temperature 0을 사용한다.
    # faithfulness처럼 LLM 호출을 여러 번 내부적으로 수행하는 지표는 botocore
    # 기본 Read Timeout(60s)을 넘기기 쉬워, src/agent.py의 ChatBedrock과 동일한
    # 취지로 넉넉한 timeout/재시도를 지정한다. 이 venv의 langchain-aws==0.2.20은
    # 신버전과 달리 `timeout`/`max_retries` 생성자 인자가 없어 botocore Config를
    # 직접 넘긴다.
    from botocore.config import Config

    bedrock_config = Config(connect_timeout=180, read_timeout=180, retries={"max_attempts": 3})

    llm = LangchainLLMWrapper(
        ChatBedrock(
            model_id=settings.bedrock_model_id,
            # langchain-aws==0.2.20의 provider 자동 추출은 region prefix로
            # "eu"/"us"/"us-gov"/"apac"/"sa"만 인식한다. 최근 Cross-region
            # Inference Profile에서 쓰이는 "global." prefix(예:
            # global.anthropic.claude-haiku-4-5-...)는 이 목록에 없어 "global"
            # 자체를 provider로 오인해 `NotImplementedError(Provider global
            # model does not support chat.)`를 던진다. provider를 명시해
            # 이 버그성 자동 추출 로직을 건너뛴다.
            provider="anthropic",
            region_name=settings.aws_region,
            # faithfulness의 Claim 분해/검증 단계는 답변이 길수록 매우 verbose한
            # JSON(문장마다 statement/reason/verdict)을 생성해야 해서 메인 앱의
            # MODEL_MAX_TOKENS(8192)로도 종종 잘린다 — RAGAS 채점 전용으로 더
            # 넉넉하게 잡는다. 12000으로 재시도하던 중 AWS Bedrock 계정의 일일
            # 토큰 한도(ThrottlingException)에 도달해 이 값이 실제로 잘림을
            # 완전히 해소하는지는 확인하지 못했다(round1_report.md 참고) — 한도
            # 회복 후 재검증이 필요하다.
            model_kwargs={"temperature": 0, "max_tokens": 12000},
            config=bedrock_config,
        ),
        # ragas==0.2.15의 기본 is_finished 판정은 stop_reason이
        # ["end_turn","stop","STOP","MAX_TOKENS","eos_token"] 중 하나가 아니면
        # 완료되지 않은 것으로 간주해 LLMDidNotFinishException을 던진다. 이
        # 목록은 이 ragas 버전이 나온 이후 출시된 최신 Claude 모델의 stop_reason
        # 값(예: pause_turn 등)을 알지 못해 정상 완료된 응답까지 실패로
        # 오판한다 — max_tokens를 충분히 크게(8192) 주었는데도 모든 Case에서
        # 동일하게 실패하는 것으로 이를 확인했다(진짜 잘림이라면 일부만
        # 실패해야 한다). 이 모델은 src.agent를 통해 이미 신뢰성이 검증되었으므로
        # ragas의 자체 완료 판정을 건너뛴다.
        is_finished_parser=lambda _response: True,
    )
    embeddings = LangchainEmbeddingsWrapper(
        BedrockEmbeddings(
            model_id=settings.bedrock_embedding_model_id,
            region_name=settings.aws_region,
            config=bedrock_config,
        )
    )

    # faithfulness는 하나의 Job 안에서 여러 차례 순차적으로 LLM을 호출한다
    # (Claim 분해 -> 각 Claim 검증). 우리 답변이 길고 상세할수록 누적 시간이
    # RunConfig 기본 timeout(180s)을 넘기기 쉬워 여유를 더 준다.
    result = evaluate(
        dataset,
        metrics=[context_recall, context_precision, faithfulness, answer_relevancy],
        llm=llm,
        embeddings=embeddings,
        run_config=RunConfig(timeout=420, max_retries=3),
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    result_df = result.to_pandas()
    csv_path = RESULTS_DIR / f"ragas_{timestamp}.csv"
    result_df.to_csv(csv_path, index=False)

    summary = {
        col: float(result_df[col].mean())
        for col in ["context_recall", "context_precision", "faithfulness", "answer_relevancy"]
        if col in result_df.columns
    }
    json_path = RESULTS_DIR / f"ragas_{timestamp}_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"완료: {csv_path}")
    print(f"완료: {json_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
