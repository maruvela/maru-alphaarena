# Alpha Arena — 12개 패턴 체크리스트 대비 구현도 보고서

> 작성 기준일: 2026-09-03. 코드베이스 전수 검색(grep) 결과를 근거로 작성했으며,
> "구현"으로 표시한 항목은 모두 파일:라인 단위 근거를 확인했다. 근거 없이
> 추정으로 "구현됨"이라 쓰지 않았다.

## 1. 요약

Alpha Arena의 `REQUIREMENTS.md` §3은 애초에 **7개 패턴**(#1, #3, #4, #6, #9,
#11, #12)을 의도적으로 목표로 설계되었고, 나머지 5개 패턴(#2 ReAct, #5 MCP,
#7 HITL, #10 Plan-Execute/장기 메모리, 그리고 #9의 동적 Router 요소)은
"패턴 개수를 늘리기 위한 목적으로 임의 추가하지 않는다"는 원칙 하에
**명시적으로 범위에서 제외**했다(`REQUIREMENTS.md` §3, §4.2). 즉 아래에서
"미구현"으로 표시한 항목 중 상당수는 놓친 것이 아니라 처음부터 하지 않기로
결정한 것이다 — 이 문서는 그 결정을 그대로 인정하되, 이번에 받은
체크리스트 기준으로 다시 한 번 냉정하게 좌표를 확인하는 것이 목적이다.

**필수 4개 항목(#1, #3, #11, #12) 결과**: 2개는 확실히 구현(#11, #12), 2개는
**절반만** 체크리스트의 문구 그대로 충족한다(#1은 구조화 출력은 되지만 LCEL
`|` 체이닝은 안 씀, #3은 RAG는 되지만 Hybrid/Reranking/Query Expansion은
없음). 아래 2절에서 항목별로, 3절에서 이 갭을 상세히 설명한다.

**권장 기준(6개 이상)**: 판정 기준을 어떻게 잡느냐에 따라 다르다.
- 엄격하게 "완전 구현"만 세면 **3개**(#6, #11, #12) — 기준 미달.
- "부분 구현"까지 포함하면 **7개**(#1, #4, #6, #8, #9, #11, #12) — 기준 충족.

즉 현재 상태를 "6개 이상 적용"이라고 주장하려면 부분 구현 4개
(#1, #4, #8, #9)를 정직하게 "부분 구현"이라고 밝히면서 포함시켜야 한다.
아래 표에서 그 구분을 숨기지 않았다.

## 2. 패턴별 상세

| # | 패턴 | 상태 | 근거 | 비고 |
|---|---|---|---|---|
| 1 | LCEL chain (Pydantic 구조화 출력) | **부분구현** | `src/agent.py:195` `structured_model = _get_chat_model().with_structured_output(schema)` 후 `.invoke(prompt_text)`. `evaluation/run_evaluation.py:105`도 동일 패턴. | Pydantic 구조화 출력(`InvestmentOpinion`/`DebateReview`/`FinalThesis`/`GuardrailResult`/`JudgeResult`)은 확실히 전면 사용 중. 다만 LCEL의 `\|` 체이닝 문법(`prompt \| llm \| parser`)은 코드 어디에도 없음 — `.format()`으로 문자열을 만든 뒤 `.invoke()`하는 방식. |
| 2 | ReAct (도구 자율 선택) | **미구현(의도적 제외)** | `bind_tools`/`create_react_agent`/`ToolNode` 전체 리포지토리에서 0건. | `REQUIREMENTS.md` §3 마지막 줄: "투자위원회의 전체 절차가 고정되어 있으므로 상위 오케스트레이션은 명시적인 LangGraph 흐름을 사용한다"(§4.2에도 "무제한 ReAct Loop" 명시적 제외). 금융 분석의 재현성을 위해 도구 호출을 LLM 자율 판단이 아닌 결정론적 Python 코드로 고정한 설계 결정. |
| 3 | RAG (하이브리드 검색·리랭킹·쿼리 확장) | **부분구현** | `src/retriever.py:270` `store.similarity_search_with_score(query, k=k, filter={"member": member})` — Chroma Dense Vector 유사도 검색 + Member 메타데이터 필터링뿐. | BM25/Sparse 검색, Cross-encoder Reranker, Query Rewriting/Expansion 전부 없음. `REQUIREMENTS.md` 자체 목표(§3 "#3 RAG: Guru별 RAG 검색")는 애초에 이 세 가지를 요구하지 않았으나, 이번 체크리스트 문구 기준으로는 미달. |
| 4 | 도구 다중 (DB·계산기·외부 API) | **부분구현** | `src/tools.py`의 `resolve_tickers`/`load_company_context`/`calculate_valuation`이 `src/agent.py:464, 512, 580, 596-597` 등에서 호출됨. `retrieve_guru_docs`(`src/retriever.py`)도 별도 도구. | 한 질의 안에서 4개 이상의 도구(기업 조회/재무 이력/RAG/DCF 계산)가 실제로 결합되어 쓰이지만, 체크리스트가 말하는 "자율 결합"(LLM이 어떤 도구를 쓸지 스스로 판단)이 아니라 LangGraph Node가 항상 정해진 순서로 호출하는 결정론적 오케스트레이션. `REQUIREMENTS.md` §7/§9도 "LLM Mental Arithmetic에 맡기지 않고 deterministic Tool Calculation"을 원칙으로 명시 — 의도된 설계. |
| 5 | MCP 서버 연동 | **미구현 (의도적 제외)** | 리포지토리 전체에서 "mcp" 검색 시 `REQUIREMENTS.md`/`SERVICE.md`의 "제외 범위" 서술만 매칭됨. | `REQUIREMENTS.md` §4.2 "MCP Server / Client" 명시적 제외. |
| 6 | 가드레일 (PII·프롬프트 인젝션 방어) | **구현** | `src/guardrails.py`: `check_direct_injection`(L98), `check_trade_execution`(L116), `check_input`(L142, Input 통합), `check_output`(L206, 금지 표현+Secret Leak). Graph 연결: `src/agent.py:432`(Input), `:792`(Output). | Prompt Injection/거래실행요청/확정수익표현/Secret Leak은 정규식 기반으로 탄탄하게 구현. 다만 이름·주민번호 등 전통적 의미의 "PII 개체 탐지"는 없음 — 이 프로젝트 성격(투자 분석 챗봇, 사용자 개인정보 입력 없음)상 필요성 자체가 낮아 별도 미구현. |
| 7 | HITL (위험 작업 승인) | **미구현 (의도적 제외)** | `interrupt` 관련 코드 0건. | `REQUIREMENTS.md` §4.2 "HITL 거래 승인" 명시적 제외 — 애초에 실제 주문을 절대 수행하지 않는 구조(Guardrail이 거래 요청 자체를 차단)라 승인 절차가 필요 없음. |
| 8 | 미들웨어 (요약·마스킹·재시도) | **부분구현** | 재시도: `src/agent.py:192-202`(`_invoke_structured`, Structured Output 실패 시 최대 1회 재시도), `:788-792`(Output Guardrail 위반 시 최대 1회 Correction 재시도). | LangChain/LangGraph의 정식 Middleware 추상화는 쓰지 않고, 각 지점에 개별 try/except로 구현한 임시방편(ad hoc) 재시도. 대화 이력 요약(다중 턴 대화 자체가 없음)이나 PII 마스킹 미들웨어는 없음. |
| 9 | Multi-Agent Supervisor | **부분구현** | `src/agent.py`의 `build_graph()`가 고정된 `StateGraph`를 구성하고, `route_round1`(L552)/`route_debate`(L644-668)가 `Send`로 4명의 고정된 Member(`MEMBER_KEYS`)에게 항상 Fan-out. | 4개의 독립 Persona(Buffett/Lynch/Marks/Damodaran) + 중립 Chair라는 "역할 분할"은 이 프로젝트의 핵심이자 확실히 구현됨. 다만 "Supervisor"라는 이름이 보통 뜻하는 동적 LLM 라우팅(상황에 따라 어떤 Sub-agent를 몇 명 부를지 스스로 결정)은 없고, 정적으로 고정된 Fan-out/Fan-in Graph임 — 이 역시 §3의 "명시적 LangGraph 흐름" 설계 결정에 따른 것. |
| 10 | Plan-Execute · 장기 메모리 | **미구현 (의도적 제외)** | `langgraph.store`/`Store`/Checkpointer import 0건. | `REQUIREMENTS.md` §4.2 "Long-term User Memory", "Plan-and-Execute" 둘 다 명시적 제외. 분석 절차(Guardrail→기업 식별→Context→Round1→Debate→Chair→응답) 자체가 매 요청마다 동일하게 고정되어 있어 런타임에 계획을 새로 세울 필요가 없는 구조. |
| 11 | Observability · Trace | **구현 (자체 구축, LangSmith/LangFuse 아님)** | `src/tracer.py`의 `record_event`(L35)/`traced_step`(L68)이 `logs/trace.jsonl`에 JSONL Trace 기록. `src/app.py`가 반환하는 `QueryResponse.trace`(`ApiTrace` 목록)로 API에도 Safe Trace 노출. 콘솔에는 번호 매긴(`[01]~[08]`) 사람이 읽는 실행 로그도 별도로 출력(`src/agent.py`의 `STEP_LABELS`/`_tag` 계열). | `LANGCHAIN_TRACING_V2`(`src/config.py:88`)가 정의만 되어 있고 리포지토리 어디서도 실제로 읽히지 않는 **죽은 설정값**임을 확인 — LangSmith/LangFuse 연동은 없다. 다만 체크리스트의 "적용 예"는 어디까지나 예시이고 패턴의 본질은 "실행 흐름 추적 가능성"이므로, 자체 구축 Trace로 동일 목적을 충분히 달성했다고 판단해 "구현"으로 표시. |
| 12 | 평가 (RAGAS · LLM-as-Judge) | **구현** | LLM-as-Judge: `evaluation/run_evaluation.py`의 `JudgeResult`(L70)/`_judge()`(L98, `JUDGE_PROMPT` 기반 구조화 채점). RAGAS: `evaluation/score_ragas_dataset.py:131` `metrics=[context_recall, context_precision, faithfulness, answer_relevancy]` 4개 전부 계산, `evaluation/generate_ragas_dataset.py`가 실제 `run_query()` 결과로 Dataset 생성. | 실측 데이터까지 확보됨 — Round 1(`round1_retry02`) 70% → Round 2(`round2_retry01`) 95% Pass Rate 개선을 실제로 측정·문서화(`evaluation/round1_report.md`, `round2_report.md`). RAGAS는 별도 venv(`.venv-ragas`) 분리 등 실제 운영상 이슈까지 해결하며 4개 지표 모두 실측(`context_recall=0.25, context_precision=0.75, faithfulness=0.238, answer_relevancy=0.301`). 12개 패턴 중 가장 완성도 높은 항목. |

## 3. 필수 4개 항목(#1, #3, #11, #12) 갭 상세

체크리스트는 "필수(산출물 규약 상 요구): 1, 3, 11, 12 (Docker·API·트레이스·평가)"라고
명시한다. 산출물(Docker 이미지, API 엔드포인트, Trace 파일, 평가 결과)이라는
**결과물** 기준으로는 4개 전부 존재한다(4절 참고). 그러나 각 패턴의 **기법**
기준으로 보면 다음 2개가 체크리스트 원문보다 좁게 구현되어 있다.

- **#1 (LCEL)**: `with_structured_output()` + `.invoke()` 조합은 실질적으로
  LCEL Runnable이 맞지만, `\|` 연산자로 여러 단계를 연결하는 "체인" 형태는
  쓰지 않았다. 예를 들어 `prompt_template \| structured_model` 같은 명시적
  파이프라인이 없다.
- **#3 (Hybrid RAG)**: Dense Vector 검색 하나만 쓴다. Sparse(BM25) 검색과의
  병행, Cross-encoder Reranking, LLM 기반 Query Expansion 중 어느 것도
  없다.

## 4. Docker·API·Trace·평가 산출물 확인

| 산출물 | 상태 | 근거 |
|---|---|---|
| Docker | 구현 | 리포지토리 루트 `Dockerfile`(15줄), `python:3.12-slim` 기반, `requirements.txt` 설치 후 `src/`, `data/wisdom/`, `data/company_snapshot.json` 복사, 8000 포트로 `uvicorn src.app:app` 실행. `docker-compose.yml`은 없음. **다만 이 개발 환경에는 Docker가 설치되어 있지 않아 실제 빌드/구동 검증은 하지 못했다**(`README.md`의 "트라이앤에러 회고" 참고). |
| API | 구현 | `src/app.py:72` `@app.post("/query", response_model=QueryResponse)`. `GET /health`도 별도 존재. |
| Trace | 구현 | 위 2절 #11 참고. |
| 평가 | 구현 | 위 2절 #12 참고. |

## 5. 개선하고 싶다면 (제안, 아직 미적용)

우선순위와 무관하게, 필수 항목의 기법적 갭만 좁히고 싶다면:

1. **#3 Hybrid RAG**: `src/retriever.py`에 Chroma의 Dense 결과와 별도로
   간단한 키워드/BM25 검색(예: `rank_bm25` 패키지)을 병행해 두 결과를
   합치는 정도로도 "하이브리드"의 최소 요건은 채울 수 있다. Reranking은
   Cross-encoder 모델 하나를 추가로 불러와 상위 K개를 재정렬하는 것으로
   충분하다.
2. **#1 LCEL 체이닝**: `_invoke_structured()`(`src/agent.py`)를
   `ChatPromptTemplate.from_template(...) \| structured_model` 형태로
   바꾸면 문법적으로 LCEL 요건을 충족한다 — 다만 현재 `.format()` 기반
   Prompt 조립 방식과 병행하려면 Prompt 템플릿 구조를 일부 재작성해야
   한다.
3. **#9 Multi-Agent Supervisor화**: Chair가 Member 4명을 항상 전원
   호출하는 대신, 질문 성격에 따라 일부 Member만 선택적으로 호출하는
   가벼운 Router를 추가하면 "동적 위임"에 더 가까워진다. 다만 이는
   `REQUIREMENTS.md` §3이 명시적으로 배제한 "임의 Agent Router"와
   정면으로 충돌하므로, 프로젝트 설계 원칙을 바꾸는 결정이 필요하다.

이 세 가지는 모두 **아직 코드에 반영하지 않았다** — 사용자 검토 후 실제
적용 여부를 결정한다.

## 6. 결론

- REQUIREMENTS.md가 처음부터 목표로 삼은 7개 패턴(#1, #3, #4, #6, #9, #11, #12)은
  전부 어떤 형태로든 코드에 존재한다.
- 이번 체크리스트의 문구를 엄격하게 적용하면, 필수 4개 중 2개(#1, #3)가
  "기법 수준"에서는 체크리스트 예시보다 단순하다 — 다만 그 이유(결정론적
  재현성 우선)가 `REQUIREMENTS.md`에 명시적으로 기록되어 있다.
- 권장 기준(6개 이상)은 "부분 구현"까지 정직하게 포함하면 7개로 충족되고,
  "완전 구현"만 세면 3개로 미달된다 — 어느 기준을 적용할지는 실제 채점
  기준에 달려 있으므로 이 문서에서 판단하지 않고 사실만 남긴다.
