"""
src/app.py

FastAPI Entry Point. REQUIREMENTS.md 19장(API 계약)을 따른다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from src.agent import run_query
from src.models import ContextItem, QueryRequest, QueryResponse

# 콘솔 로그 레벨/포맷은 src.config가 import 시점에 이미 설정한다(LOG_LEVEL
# 기반, config.py 참고) — 여기서는 이 모듈 전용 Logger만 얻는다.
logger = logging.getLogger("alpha_arena")


class UTF8JSONResponse(JSONResponse):
    """FastAPI 기본 `JSONResponse`는 Body를 항상 UTF-8 bytes로 인코딩하면서도
    `Content-Type` 헤더에는 `charset`을 명시하지 않는다(`application/json`).
    Windows PowerShell 5.1의 `Invoke-RestMethod`는 charset이 없으면 응답을
    UTF-8이 아닌 다른 인코딩(ISO-8859-1 계열)으로 잘못 해석해 `$response.answer`의
    한글이 모두 깨지는 문제가 있다(실측: `docs/how_to_use.md` 7절 참고). JSON
    Body의 필드/스키마는 그대로 두고 헤더에 `charset=utf-8`만 명시해 해결한다
    — API Contract(19.2장: answer/contexts/trace Shape)는 전혀 바뀌지 않는다.
    """

    media_type = "application/json; charset=utf-8"


app = FastAPI(title="Alpha Arena", version="0.1", default_response_class=UTF8JSONResponse)


@app.get("/health")
def health() -> dict:
    """19.5장: `/query`와 무관하게 프로세스가 살아있는지만 확인하는 별도 Endpoint."""

    return {"status": "ok"}


def _dedupe_contexts(contexts: list) -> list[ContextItem]:
    """내부 State의 `EvidenceContext` 리스트(Round1/Debate 전체에서 누적된
    RAG+Snapshot 근거)를 외부 API `contexts` 최소 계약(doc_id/text)으로 매핑하며
    (doc_id, chunk_id) 기준으로 중복을 제거한다(19.3장) — 여러 Member가 같은
    Passage를 검색했을 수 있기 때문이다."""

    seen: set[tuple[str, str | None]] = set()
    result: list[ContextItem] = []

    for c in contexts:
        key = (c.doc_id, c.chunk_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            ContextItem(
                doc_id=c.doc_id,
                text=c.text,
                source_type=c.source_type,
                title=c.title,
                member=c.member,
            )
        )

    return result


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    """공식 API 계약(19.1/19.2장): 내부 LangGraph State를 그대로 노출하지 않고
    항상 `answer`/`contexts`/`trace` 세 필드로만 매핑해서 반환한다.

    Provider(Bedrock) 오류나 예상치 못한 Runtime 예외는 200으로 감추지 않고
    Controlled 500으로 명확히 알린다(29.3장) — 평가 Runner는 이를 FAIL이 아닌
    ERROR로 분류해야 하므로, 상태 코드로 구분 가능해야 한다.
    """

    try:
        final_state = run_query(request.question)
    except Exception as exc:  # noqa: BLE001 - 29.3 Provider/Runtime Error는 Controlled Error로 처리
        logger.exception("query 처리 중 오류")
        raise HTTPException(status_code=500, detail="internal processing error") from exc

    return QueryResponse(
        answer=final_state.get("answer") or "",
        contexts=_dedupe_contexts(final_state.get("contexts") or []),
        trace=final_state.get("safe_trace") or [],
    )
