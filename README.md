# Alpha Arena

> 근거 기반 Multi-Agent Investment Committee — 서로 다른 투자 철학을 가진 4명의
> Investment Member가 독립적으로 분석하고, Debate로 핵심 불일치를 검토한 뒤,
> 중립적인 Arena Chair가 다수결이 아닌 근거로 최종 Investment Thesis를 작성한다.

무엇을 푸는 서비스인지, 서비스 정책, 정책적 제약은 [SERVICE.md](SERVICE.md)를,
구현 계약(Schema/Tool/API/평가 기준)은 [REQUIREMENTS.md](REQUIREMENTS.md)를,
설치·실행·점검·평가 재현 절차는 [docs/how_to_use.md](docs/how_to_use.md)를 참고한다.

> **현재 상태**: 핵심 Agent/API/Guardrail/결정론적 Tool 구현이 완료되었고
> 결정론적 Unit Test(43개)가 모두 통과한다. 실제 AWS Bedrock으로 4개 Member +
> Debate + Chair 전체 파이프라인과 20건 Input-Output 평가(Round 1)를 End-to-End로
> 실행해 실측치를 확보했다. RAGAS 4개 지표 중 3개(context_recall,
> context_precision, answer_relevancy)를 측정했고, faithfulness는 라이브러리
> 호환성 문제 해결 도중 **AWS Bedrock 계정의 일일 토큰 한도(Throttling)에
> 도달**해 확정하지 못했다(아래 참고). **Docker Build/Run 검증은 이 작업
> 환경에 Docker가 설치되어 있지 않아 보류**했다. Round 2는 토큰 한도 회복 후
> 재실행 예정이며 그때까지 `evaluation/round2_report.md`는 Template 상태다.

## 활용한 Day 1~7 패턴 (총 7개)

| 패턴 | Alpha Arena 적용 방식 |
|---|---|
| #1 Structured Output | `src/models.py`의 Pydantic 모델(InvestmentOpinion, DebateReview, FinalThesis 등)로 Agent 간 계약을 강제 |
| #3 RAG | `src/retriever.py`가 `data/wisdom/`을 Chroma `investment_wisdom` Collection으로 색인하고 Member 별로 Filtering |
| #4 Multi-tool | `src/tools.py`의 `get_company_metrics` / `get_financial_history` / `calculate_valuation` + `retrieve_guru_docs` |
| #6 Guardrail | `src/guardrails.py`: Direct/Indirect Prompt Injection 방어, 실거래 요청 차단, 출력 후검증 |
| #9 Multi-Agent | `src/agent.py`: LangGraph로 4개 독립 Member + Debate + 중립 Arena Chair 구성 |
| #11 Observability / Trace | `src/tracer.py`(로컬 JSONL) + `src/agent.py`의 `build_safe_trace`(API용 Safe Trace) |
| #12 Evaluation | `evaluation/run_evaluation.py`(Input-Output + LLM-as-Judge) + `evaluation/generate_ragas_dataset.py`/`score_ragas_dataset.py`(RAGAS) |

## 아키텍처

```text
START
  -> input_guardrail            (Direct Injection / 실거래 요청 차단)
  -> resolve_company            (NVDA/COST/INTC 결정론적 Ticker 해석)
  -> load_company_context       (company_snapshot.json 1회 로드)
  -> round1_fanout -> [Buffett | Lynch | Marks | Damodaran] 독립 분석 (Send Fan-out)
  -> collect_round1
  -> debate_fanout -> [Member별 Debate/Revision] (Send Fan-out)
  -> collect_revisions
  -> arena_chair                (다수결 금지, Minority View 보존)
  -> render_answer               (사람이 읽는 답변 생성 + Output Guardrail)
  -> finalize                   (Safe Trace 구성)
  -> END
```

Round 1 Member는 서로의 의견을 보지 않고(State Fan-out Payload에 다른 Member 정보를 담지 않음)
독립적으로 분석하며, Debate 단계에서만 Round 1 전체 의견이 제공된다. 자세한 계약은
[REQUIREMENTS.md](REQUIREMENTS.md) 9~17장을 따른다.

### 핵심 코드 위치

- `src/models.py` — Stance/ConflictType Enum, InvestmentOpinion/DebateReview/FinalThesis 등 Structured Output 계약
- `src/state.py` — LangGraph `AlphaArenaState` (Fan-out 결과는 `operator.add` Reducer로 병합)
- `src/tools.py` — Ticker 해석, Company Snapshot 조회, 결정론적 DCF(`calculate_valuation`)
- `src/retriever.py` — `data/wisdom/*.md` 파싱(Passage Chunking) + Chroma 색인 + Member Filtering 검색
- `src/guardrails.py` — Input/Output Guardrail 정책과 정규식 기반 결정론적 검사
- `src/prompts.py` — Member/Debate/Chair/Judge Prompt 중앙화
- `src/agent.py` — LangGraph Node/Edge 정의, Graph Assembly, `run_query()`
- `src/app.py` — FastAPI `/health`, `/query`
- `evaluation/run_evaluation.py` — 20개 Input-Output Case 실행 + LLM-as-Judge
- `evaluation/generate_ragas_dataset.py` + `evaluation/score_ragas_dataset.py` — RAGAS 4개 지표 측정(2단계, 아래 참고)

## Docker 실행 방법

```bash
cp .env.example .env   # AWS_REGION / BEDROCK_MODEL_ID / BEDROCK_EMBEDDING_MODEL_ID 등을 채운다

docker build -t alpha-arena .
docker run --rm --env-file .env -p 8000:8000 alpha-arena
```

검증:

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"NVDA를 네 가지 투자 관점으로 분석해줘"}'
```

Vector Index는 `docker build` 단계가 아니라 최초 `/query` 호출 시점에 런타임에서
Bedrock Credential로 생성된다(`src/retriever.py`의 Lazy Singleton).

> 이 명령들은 아직 이 저장소 작업 환경에서 직접 실행/검증되지 않았다(Docker
> 미설치 환경). Dockerfile/.dockerignore는 REQUIREMENTS.md 33장 요구사항에
> 맞춰 작성되어 있으나, 실제 `docker build`/`docker run` 성공 여부는 Docker가
> 있는 환경에서 확인이 필요하다.

## 로컬 실행 / 테스트

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt

pytest tests/ -q                              # 결정론적 Unit Test (LLM 호출 없음, 43개)
python -m evaluation.run_evaluation           # Input-Output 평가 (Bedrock Credential 필요)
python -m evaluation.generate_ragas_dataset   # RAGAS 1단계 (메인 venv)
```

RAGAS 채점(2단계)은 의존성 충돌 때문에 별도 venv가 필요하다 — 자세한 이유와
명령은 [docs/how_to_use.md](docs/how_to_use.md) 9.2절을 참고한다.

```bash
python -m venv .venv-ragas
source .venv-ragas/Scripts/activate
pip install -r evaluation/requirements-ragas.txt
python -m evaluation.score_ragas_dataset
```

## RAGAS 4개 지표 / Round 1·2 Input-Output 통과율 / 개선폭

### Round 1 (실측, 2026-09-02)

| 지표 | 값 |
|---|---:|
| 전체 Pass Rate | **95.0%** (19/20) |
| Guardrail Pass Rate | **100%** (3/3) |
| Positive False Block Rate | **0%** |
| RAGAS context_recall | 0.50 |
| RAGAS context_precision | 0.70 |
| RAGAS faithfulness | 측정 불가(아래 참고) |
| RAGAS answer_relevancy | 0.25 |

Category별: positive 7/8 PASS(1 ERROR), negative 4/4, edge 5/5, guardrail 3/3.
유일한 ERROR(P07)는 Arena Chair의 `FinalThesis` Structured Output이 드물게
`max_tokens` 한도 내에서 마지막 필드(`evidence`)까지 도달하지 못해 발생했다 —
스펙대로(29.4장) `FAIL`로 위장하지 않고 `ERROR`로 정확히 분류되었다. 전체
분석과 개선 계획은 [evaluation/round1_report.md](evaluation/round1_report.md)를
참고한다. Round 2 결과는
[evaluation/round2_report.md](evaluation/round2_report.md)에 기록할 예정이다
(현재 Template 상태).

## 트라이앤에러 회고

- **`data/wisdom/**` Front Matter 파싱 실패**: `scripts/build_wisdom.py`가 생성한
  YAML Front Matter의 `raw_file` 필드가 Windows 경로(백슬래시)를 그대로 담고 있어
  표준 YAML Parser(PyYAML)가 잘못된 Escape Sequence로 오류를 일으켰다. `data/wisdom/**`은
  읽기 전용 자산이라 데이터를 고치는 대신, `src/retriever.py`에 이 생성 형식 전용의
  가벼운 Front Matter Parser를 구현해 Retrieval Layer에서 해결했다(REQUIREMENTS.md 37장:
  Retrieval Failure는 Retriever Layer에서 해결).
- **Titan Embedding Max Input Token 초과**: 일부 `data/wisdom` 문서의 단일
  Passage가 8,192 Token을 넘어 임베딩이 실패했다(PDF 한 페이지가 통째로 한
  문단으로 추출된 경우). `src/retriever.py`가 3,000자를 넘는 Passage만
  Retrieval Layer에서 안전하게 재분할하도록 수정했다(원본 데이터는 그대로 둠).
- **Structured Output이 드물게 잘림**: `ChatBedrock`에 `max_tokens`를 지정하지
  않아 Provider 기본값으로 필드가 많은 `InvestmentOpinion`/`FinalThesis`가
  Tool Call 생성 도중 잘려 Pydantic Validation이 실패하는 사례를 실제 E2E
  테스트 중 발견했다. `MODEL_MAX_TOKENS`(기본 8192) 설정을 추가해 대부분
  해결했으며, 남은 잔여 사례는 round1_report.md에 기록했다.
- **Bedrock Read Timeout**: Round 1/Debate가 4-way 병렬로 동시에 Bedrock을
  호출할 때 botocore 기본 Read Timeout(60초)을 넘겨 실패하는 사례를 발견해
  `ChatBedrock(timeout=180, max_retries=3)`으로 조정했다.
- **로컬 개발 환경(Windows, Python 3.14)에서 `ragas` 설치 실패**: 최신
  `ragas`(0.3+)는 `scikit-network`를 요구해 Windows+Python 3.14에서 C++ Build
  Tools 없이 소스 빌드가 실패한다. `scikit-network`가 없는 마지막 계열인
  `ragas==0.2.15`는 반대로 구버전 `langchain-community`(`chat_models.vertexai`
  모듈 포함)를 요구하는데, 이는 메인 앱이 쓰는 최신 `langchain-aws`/`langgraph`
  스택이 강제하는 최신 `langchain-community`(해당 모듈 제거됨)와 같은 venv에
  공존할 수 없었다. 해결책으로 RAGAS 평가를 두 단계로 분리했다: 메인 venv에서
  `evaluation/generate_ragas_dataset.py`로 질문/답변/근거를 수집해 JSON으로
  저장하고, `evaluation/requirements-ragas.txt`(`ragas==0.2.15` +
  `langchain-aws==0.2.20`)로 만든 별도 venv에서
  `evaluation/score_ragas_dataset.py`가 그 JSON을 읽어 채점한다.
- **`ragas.executor`의 `nest_asyncio.apply()`가 Python 3.14와 충돌**: import
  시점에 무조건 실행되는 이 호출이 `asyncio.wait_for`의 내부 Task 처리와
  부딪혀 모든 RAGAS Job이 `RuntimeError: Timeout should be used inside a
  task`로 실패했다. `ragas` import 전에 `nest_asyncio.apply`를 no-op으로
  치환해 해결했다(중첩 이벤트 루프가 필요 없는 평범한 스크립트라 안전).
- **RAGAS `faithfulness`가 최신 Claude 모델에서 계속 실패**: `ragas==0.2.15`의
  `is_finished` 판정이 `claude-sonnet-4-5`의 `stop_reason` 값을 인식하지 못해
  정상 응답도 실패로 오판했다(`is_finished_parser` Override로 우회). 우회 후
  드러난 진짜 원인은 faithfulness의 Claim 분해 단계가 답변이 길수록 매우 큰
  JSON을 생성해야 해서 `max_tokens`를 넘겨 응답이 잘리는 것이었다 —
  `max_tokens`를 올려 재시도하던 중 AWS Bedrock 계정의 **일일 토큰 한도**에
  도달해 최종 확인은 하지 못했다. 자세한 내용은
  [evaluation/round1_report.md](evaluation/round1_report.md)를 참고한다.
- **`evaluation/test_queries.csv`는 초안 상태로 시작**: REQUIREMENTS.md 23장에
  따라 이 자산이 없던 시점에 Coding Agent가 임의로 공식 평가셋을 만들지 않고,
  사용자 승인을 받는 절차를 거쳤다. 승인된 이후로는 Debug 목적으로 임의
  수정하지 않는다(37장).

## 알려진 제약

- **Docker Build/Run 미검증**: 이 저장소를 작업한 환경에 Docker가 설치되어
  있지 않아 `docker build`/`docker run` 자체를 실행해보지 못했다. Dockerfile은
  스펙대로 작성했으나 최종 검증이 필요하다.
- **AWS Bedrock 일일 토큰 한도 도달**: 이 계정은 현재 일일 토큰 한도에
  도달한 상태다(`ThrottlingException: Too many tokens per day`). 한도가
  회복되기 전까지 RAGAS `faithfulness` 재검증과 Round 2 평가 실행이 보류된다.
- **RAGAS `faithfulness` 미확정**: 나머지 3개 지표는 측정했으나 이 지표만
  라이브러리 호환성 + 토큰 한도 문제로 확정하지 못했다(트라이앤에러 회고 참고).
- **Round 2 미실행**: round1_report.md의 개선 계획(Chair Prompt 간결화 등)을
  반영해뒀으나, 토큰 한도 회복 후 재실행이 필요하다.
