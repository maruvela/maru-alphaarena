"""
src/retriever.py

Guru RAG Pipeline. REQUIREMENTS.md 6장(RAG Corpus / Evidence 정책)을 따른다.

- `data/wisdom/*.md`는 읽기 전용 입력 자산이며 재작성/요약하지 않는다.
- 하나의 Logical Collection(`investment_wisdom`)을 사용하고 member Metadata로 필터링한다.
- `### Passage NNN` 구조를 기본 Chunk로 취급하고, 없는 문서만 fallback splitter를 쓴다.
- Embedding 생성에 AWS Credential이 필요하므로 Index는 `docker build` 단계가 아니라
  최초 조회 시점(Lazy)에 런타임에서 생성한다.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from langchain_aws import BedrockEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import settings
from src.models import EvidenceContext

COLLECTION_NAME = "investment_wisdom"
MEMBERS: tuple[str, ...] = ("buffett", "lynch", "marks", "damodaran")

_PASSAGE_HEADER = re.compile(r"^###\s*Passage\s*(\d+)\s*$", flags=re.MULTILINE)

_FRONT_MATTER_KV = re.compile(r"^(\w+):\s*(.*)$")
_QUOTED_ITEM = re.compile(r'"((?:[^"\\]|\\.)*)"')

_FALLBACK_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)

# Titan Embedding v2의 Max Input Token(8192)을 넘는 극단적으로 긴 단일 Passage가
# 존재할 수 있다(예: PDF 페이지 전체가 하나의 문단으로 추출된 경우). data/wisdom/**은
# 읽기 전용 자산이므로 원문을 쪼개지 않고, Retrieval Layer에서 안전 마진을 두고
# 재분할한다(1 token당 최소 1자로 가정해도 8192자는 8192 token 미만).
_MAX_PASSAGE_CHARS = 3000

_lock = threading.Lock()
_vectorstore: Chroma | None = None


class RetrievalError(ValueError):
    pass


def _split_passages(doc_id: str, body: str) -> list[tuple[str, str]]:
    """
    '### Passage NNN' 구조를 기본 Chunk로 사용한다(6.4장 권장 Chunk ID:
    `{doc_id}#passage-{NNN}`). Passage 구조가 전혀 없는 문서만 고정 설정
    fallback splitter(chunk_size=800/overlap=120)를 사용한다.

    Passage 하나가 Titan Embedding의 Max Input Token(8192)을 넘을 정도로
    길면(예: PDF 한 페이지가 통째로 한 문단으로 추출된 경우) 같은 fallback
    splitter로 그 Passage만 추가 분할해 `{chunk_id}-01`, `-02`, ... 형태의
    하위 Chunk ID를 붙인다 — data/wisdom 원본은 건드리지 않고 Retrieval
    Layer에서만 안전하게 재분할한다(37장: Retrieval Failure는 Retriever에서 해결).

    반환: [(chunk_id, text), ...]
    """

    matches = list(_PASSAGE_HEADER.finditer(body))

    if not matches:
        chunks = _FALLBACK_SPLITTER.split_text(body)
        return [(f"{doc_id}#chunk-{i + 1:03d}", chunk.strip()) for i, chunk in enumerate(chunks) if chunk.strip()]

    passages: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        number = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        text = body[start:end].strip()
        if not text:
            continue

        base_chunk_id = f"{doc_id}#passage-{number}"
        if len(text) <= _MAX_PASSAGE_CHARS:
            passages.append((base_chunk_id, text))
            continue

        sub_chunks = _FALLBACK_SPLITTER.split_text(text)
        for i, sub_chunk in enumerate(sub_chunks):
            if sub_chunk.strip():
                passages.append((f"{base_chunk_id}-{i + 1:02d}", sub_chunk.strip()))

    return passages


def _unescape(value: str) -> str:
    """scripts/build_wisdom.py의 yaml_quote()가 이스케이프한 `\\"`만 되돌린다
    (다른 백슬래시, 예: raw_file의 Windows 경로는 그대로 둔다)."""

    return value.replace('\\"', '"')


def _parse_scalar(raw: str) -> str:
    """`key: "value"` 형태에서 따옴표를 벗기고 값만 꺼낸다."""

    raw = raw.strip()
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        return _unescape(raw[1:-1])
    return raw


def _parse_list(raw: str) -> list[str]:
    """`topics: ["a", "b"]` 같은 단순 리스트에서 따옴표로 감싼 항목만 추출한다."""

    return [_unescape(m) for m in _QUOTED_ITEM.findall(raw)]


def _parse_front_matter(text: str) -> tuple[dict[str, object], str]:
    """
    build_wisdom.py가 생성하는 단순 Flat YAML Front Matter 전용 Parser.

    `raw_file` 필드가 Windows 경로(백슬래시)를 그대로 담고 있어 표준 YAML
    Parser(PyYAML)가 잘못된 Escape Sequence로 오류를 낸다. data/wisdom/**은
    읽기 전용 입력 자산이므로 데이터를 수정하는 대신, 알려진 생성 형식에
    맞춘 전용 Parser로 Retrieval Layer에서 문제를 해결한다.
    """

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    metadata: dict[str, object] = {}
    end_index = len(lines)

    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            end_index = i
            break

        match = _FRONT_MATTER_KV.match(line)
        if not match:
            continue

        key, raw_value = match.group(1), match.group(2)
        metadata[key] = _parse_list(raw_value) if raw_value.startswith("[") else _parse_scalar(raw_value)

    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    return metadata, body


def _load_documents() -> list[Document]:
    """`data/wisdom/<member>/*.md`를 전부 읽어(File I/O) LangChain `Document`
    목록으로 변환한다. 각 Document의 metadata에 `member`를 반드시 심어두는
    것이 핵심이다 — 이후 `retrieve_guru_docs`의 Member Filtering이 바로 이
    metadata 필드에 의존하기 때문이다(6.3장: Member별 Filtering 가능한 Metadata)."""

    documents: list[Document] = []

    for member in MEMBERS:
        member_dir = settings.wisdom_dir / member
        if not member_dir.exists():
            continue

        for path in sorted(member_dir.glob("*.md")):
            raw_text = path.read_text(encoding="utf-8")
            metadata, body = _parse_front_matter(raw_text)
            doc_id = str(metadata.get("doc_id") or path.stem)

            for chunk_id, text in _split_passages(doc_id, body):
                topics = metadata.get("topics") or []
                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "doc_id": doc_id,
                            "chunk_id": chunk_id,
                            "member": member,
                            "title": str(metadata.get("title") or ""),
                            "year": str(metadata.get("year") or ""),
                            "source_type": str(metadata.get("source_type") or ""),
                            "authority": str(metadata.get("authority") or ""),
                            "source_url": str(metadata.get("source_url") or ""),
                            "topics": ", ".join(topics) if isinstance(topics, list) else str(topics),
                        },
                    )
                )

    return documents


def _build_embeddings() -> BedrockEmbeddings:
    """AWS Bedrock 임베딩 모델 클라이언트 생성(외부 Provider 호출 지점).

    수천 개 Passage를 순차적으로 임베딩하는 초기 Index 빌드 중에는 간헐적인
    Read Timeout이 발생할 수 있어 botocore 기본값(60s)보다 넉넉한 Timeout과
    재시도 횟수를 명시한다.
    """

    from botocore.config import Config

    return BedrockEmbeddings(
        model_id=settings.bedrock_embedding_model_id,
        region_name=settings.aws_region,
        config=Config(connect_timeout=60, read_timeout=60, retries={"max_attempts": 5}),
    )


def get_vectorstore(force_rebuild: bool = False) -> Chroma:
    """
    `investment_wisdom` Chroma Collection을 Lazy하게 얻는다.

    이미 Persist된 Index가 있으면 재사용하고, 비어있거나 force_rebuild면
    `data/wisdom/`에서 다시 생성한다. Embedding 생성에는 AWS Credential이
    필요하므로 이 함수는 모듈 Import 시점이 아니라 최초 검색 요청이 들어온
    시점에만 호출된다 — Docker `docker build` 단계에서 Index를 만들지 않기
    위함이다(22장). `_lock`은 여러 Round 1 Member가 동시에(병렬 Fan-out)
    최초 호출을 트리거해도 인덱스를 중복 생성하지 않도록 보호한다.
    """

    global _vectorstore

    with _lock:
        if _vectorstore is not None and not force_rebuild:
            return _vectorstore

        persist_dir = settings.chroma_path()
        persist_dir.mkdir(parents=True, exist_ok=True)

        embeddings = _build_embeddings()
        store = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=embeddings,
            persist_directory=str(persist_dir),
        )

        # 이미 채워진 Persist Index가 있으면 재임베딩을 건너뛴다(비용/시간 절약).
        needs_build = force_rebuild or store._collection.count() == 0  # noqa: SLF001

        if needs_build:
            documents = _load_documents()
            if not documents:
                raise RetrievalError(
                    f"data/wisdom/ 아래에서 RAG 문서를 찾지 못했습니다: {settings.wisdom_dir}"
                )
            if force_rebuild:
                ids = store.get()["ids"]
                if ids:
                    store.delete(ids=ids)
            store.add_documents(documents)

        _vectorstore = store
        return store


def retrieve_guru_docs(member: str, query: str, top_k: int | None = None) -> list[EvidenceContext]:
    """
    REQUIREMENTS.md 6.5의 필수 Tool. `filter={"member": member}`로 Chroma
    검색 자체를 제한하므로, 결과에는 절대 다른 Guru의 Passage가 섞이지
    않는다(한 Member 분석의 근거로 다른 Corpus가 반환되는 것을 구조적으로 방지).

    `score`는 Chroma 기본 거리 지표(낮을수록 유사) 그대로를 담아 반환한다 —
    호출부가 필요하면 자체적으로 재해석할 수 있도록 원값을 보존한다.
    """

    if member not in MEMBERS:
        raise RetrievalError(f"알 수 없는 Member입니다: {member}. 지원: {', '.join(MEMBERS)}")

    k = top_k if top_k is not None else settings.rag_top_k
    store = get_vectorstore()

    results = store.similarity_search_with_score(query, k=k, filter={"member": member})

    contexts: list[EvidenceContext] = []
    for doc, score in results:
        meta = doc.metadata
        contexts.append(
            EvidenceContext(
                doc_id=meta.get("doc_id", ""),
                chunk_id=meta.get("chunk_id"),
                member=meta.get("member"),
                text=doc.page_content,
                source_type=meta.get("source_type") or None,
                title=meta.get("title") or None,
                source_url=meta.get("source_url") or None,
                score=float(score),
            )
        )

    return contexts
