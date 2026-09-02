"""
src/config.py

환경변수 기반 설정. REQUIREMENTS.md 21장의 권장 Environment Variable을 따른다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[1]


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    """모든 Runtime 설정을 한 곳에 모은 불변 객체. 값은 전부 환경변수(.env)에서
    읽고, Secret은 이 파일에 절대 Hard-code하지 않는다(21장). 모듈 하단의
    `settings` 싱글턴을 다른 모듈들이 그대로 import해서 사용한다.
    """

    # AWS Bedrock. BEDROCK_MODEL_ID는 On-demand로 호출 불가능한 최신 Claude
    # 모델의 경우 모델 ID가 아니라 Cross-region Inference Profile ID
    # (예: "us.anthropic.claude-sonnet-4-5-...")여야 한다 — 계정에서 접근
    # 가능한 형태는 `bedrock list-inference-profiles`로 확인한다.
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    bedrock_model_id: str = field(
        default_factory=lambda: os.getenv(
            "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        )
    )
    bedrock_embedding_model_id: str = field(
        default_factory=lambda: os.getenv(
            "BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"
        )
    )

    # Generation. InvestmentOpinion/FinalThesis처럼 여러 list 필드를 가진 큰
    # Structured Output은 기본 Bedrock max_tokens(Provider별로 1024~로 낮을 수
    # 있음)로는 중간에 잘려 Pydantic Validation이 실패할 수 있어 넉넉히 잡는다.
    model_temperature: float = field(default_factory=lambda: _get_float("MODEL_TEMPERATURE", 0.0))
    model_max_tokens: int = field(default_factory=lambda: _get_int("MODEL_MAX_TOKENS", 8192))
    rag_top_k: int = field(default_factory=lambda: _get_int("RAG_TOP_K", 3))

    # Trace / Logging
    trace_file: str = field(default_factory=lambda: os.getenv("TRACE_FILE", "logs/trace.jsonl"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # Vector store
    chroma_persist_dir: str = field(
        default_factory=lambda: os.getenv("CHROMA_PERSIST_DIR", ".cache/chroma")
    )

    # Data paths (read-only inputs, never written to at runtime)
    wisdom_dir: Path = field(default_factory=lambda: ROOT_DIR / "data" / "wisdom")
    company_snapshot_path: Path = field(
        default_factory=lambda: ROOT_DIR / "data" / "company_snapshot.json"
    )

    # LangSmith / LangFuse (optional)
    langchain_tracing_v2: bool = field(default_factory=lambda: _get_bool("LANGCHAIN_TRACING_V2", False))

    def trace_path(self) -> Path:
        """TRACE_FILE(기본 logs/trace.jsonl)을 항상 Repository 루트 기준
        절대경로로 돌려준다 — 어느 작업 디렉터리에서 실행해도 Trace 파일
        위치가 흔들리지 않게 한다."""

        return ROOT_DIR / self.trace_file

    def chroma_path(self) -> Path:
        """CHROMA_PERSIST_DIR이 이미 절대경로면 그대로, 상대경로면 Repository
        루트 기준으로 해석한다."""

        path = Path(self.chroma_persist_dir)
        if not path.is_absolute():
            path = ROOT_DIR / self.chroma_persist_dir
        return path


settings = Settings()

# 콘솔(사람이 읽는) 로그의 전역 설정을 여기서 한 번만 수행한다. src.config는
# src 패키지의 거의 모든 모듈(app/agent/tools/retriever 등)이 가장 먼저
# import하므로, uvicorn으로 API를 띄우든 evaluation 스크립트를 직접
# 실행하든 동일한 LOG_LEVEL/포맷이 적용된다. `logging.basicConfig`는 Root
# Logger에 이미 Handler가 있으면 아무것도 하지 않으므로 중복 설정 시에도
# 안전하다. Uvicorn 자체 로그("uvicorn"/"uvicorn.access")는 별도 Handler를
# 쓰므로 이 설정과 함께 같은 터미널에 자연스럽게 같이 출력된다.
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

# boto3/botocore/langchain_aws/httpx/urllib3는 각 Bedrock 호출마다 매우 장황한
# INFO 로그("Successfully invoked model ...", ResponseMetadata 전체 dump 등)를
# 남겨 LOG_LEVEL=INFO에서 Alpha Arena 자체 [NN 단계] 로그를 뒤덮어 버린다.
# 이 Logger들은 기본적으로 자체 Level이 없어 Root Logger의 Level을 그대로
# 물려받으므로(Python logging 계층 규칙), 여기서 명시적으로 WARNING 이상만
# 보이게 낮춰준다. DEBUG로 조사할 때는 이 라이브러리들의 상세 로그도 함께
# 필요할 수 있으므로 이 억제를 적용하지 않는다(요구 #9).
if settings.log_level.upper() != "DEBUG":
    for _noisy_logger_name in ("langchain_aws", "boto3", "botocore", "httpx", "urllib3"):
        logging.getLogger(_noisy_logger_name).setLevel(logging.WARNING)
