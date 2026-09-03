# Alpha Arena

> 근거 기반 Multi-Agent Investment Committee — 서로 다른 투자 철학을 가진 4명의
> Investment Member가 독립적으로 분석하고, Debate로 핵심 불일치를 검토한 뒤,
> 중립적인 Arena Chair가 다수결이 아닌 근거로 최종 Investment Thesis를 작성한다.

무엇을 푸는 서비스인지, 서비스 정책, 정책적 제약은 [SERVICE.md](SERVICE.md)를,
구현 계약(Schema/Tool/API/평가 기준)은 [REQUIREMENTS.md](REQUIREMENTS.md)를,
설치·실행·점검·평가 재현 절차는 [docs/how_to_use.md](docs/how_to_use.md)를,
Day 1~7 패턴 체크리스트 대비 구현도 자체 감사는 [pjt_report.md](pjt_report.md)를
참고한다.

> **현재 상태**: 핵심 Agent/API/Guardrail/결정론적 Tool 구현이 완료되었고
> 결정론적 Unit Test(**88개**)가 모두 통과한다. 비용 절감을 위해 Bedrock
> 모델을 `claude-sonnet-4-5`에서 **`claude-haiku-4-5`**로 전환했고, 이 모델
> 기준으로 공식 **Round 1**(70.0% Pass, `round1_retry02`)과 **Round 2**
> (95.0% Pass, `round2_retry01`)를 실제 Bedrock으로 End-to-End 실행해 실측치를
> 확보했다. RAGAS 4개 지표(context_recall/context_precision/faithfulness/
> answer_relevancy)도 전부 측정 완료했다. 평가 결과는 이제 매 실행마다
> `evaluation/runs/<run_id>/`에 독립적으로 영구 보존된다(REQUIREMENTS.md
> 24.1장). **Docker Build/Run 검증은 이 작업 환경에 Docker가 설치되어 있지
> 않아 여전히 보류** 상태다.

## 활용한 Day 1~7 패턴 (총 7개, 상세 감사는 [pjt_report.md](pjt_report.md))

| 패턴 | Alpha Arena 적용 방식 |
|---|---|
| #1 Structured Output | `src/models.py`의 Pydantic 모델(InvestmentOpinion, DebateReview, FinalThesis 등)로 Agent 간 계약을 강제 |
| #3 RAG | `src/retriever.py`가 `data/wisdom/`을 Chroma `investment_wisdom` Collection으로 색인하고 Member 별로 Filtering |
| #4 Multi-tool | `src/tools.py`의 `get_company_metrics` / `get_financial_history` / `calculate_valuation` + `retrieve_guru_docs` |
| #6 Guardrail | `src/guardrails.py`: Direct/Indirect Prompt Injection 방어, 실거래 요청 차단, 출력 후검증 |
| #9 Multi-Agent | `src/agent.py`: LangGraph로 4개 독립 Member + Debate + 중립 Arena Chair 구성 |
| #11 Observability / Trace | `src/tracer.py`(로컬 JSONL) + `src/agent.py`의 `build_safe_trace`(API용 Safe Trace) + 번호 매긴 콘솔 실행 로그 |
| #12 Evaluation | `evaluation/run_evaluation.py`(Input-Output + LLM-as-Judge, Run별 영구 저장) + `evaluation/generate_ragas_dataset.py`/`score_ragas_dataset.py`(RAGAS) |

각 패턴이 체크리스트 문구를 얼마나 엄격하게 충족하는지(예: LCEL `\|` 체이닝
미사용, Hybrid RAG 미적용 등 정직한 갭)는 [pjt_report.md](pjt_report.md)에
파일:라인 근거와 함께 정리했다.

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
[REQUIREMENTS.md](REQUIREMENTS.md) 9~17장을 따른다. LangGraph 실행 흐름을 사람이
읽는 번호(`[01]`~`[08]`, Member는 `A`~`D`)로 실시간 확인하는 방법은
[docs/how_to_use.md](docs/how_to_use.md) 10절, Mermaid Diagram은 11절을 참고한다.

### 핵심 코드 위치

- `src/models.py` — Stance/ConflictType Enum, InvestmentOpinion/DebateReview/FinalThesis 등 Structured Output 계약
- `src/state.py` — LangGraph `AlphaArenaState` (Fan-out 결과는 `operator.add` Reducer로 병합)
- `src/tools.py` — Ticker 해석, Company Snapshot 조회, 결정론적 DCF(`calculate_valuation`)
- `src/retriever.py` — `data/wisdom/*.md` 파싱(Passage Chunking) + Chroma 색인 + Member Filtering 검색
- `src/guardrails.py` — Input/Output Guardrail 정책과 정규식 기반 결정론적 검사(부정 접두어 인식 포함)
- `src/prompts.py` — Member/Debate/Chair/Judge Prompt 중앙화, 응답 언어(한국어) 정책
- `src/agent.py` — LangGraph Node/Edge 정의, Graph Assembly, `run_query()`, 번호 매긴 콘솔 로그
- `src/app.py` — FastAPI `/health`, `/query` (UTF-8 charset 명시 응답)
- `evaluation/run_evaluation.py` — `--round <name>` 인자로 20개 Input-Output Case 실행 + LLM-as-Judge, `evaluation/runs/<run_id>/`에 영구 저장
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

pytest tests/ -q                                    # 결정론적 Unit Test (LLM 호출 없음, 88개)
python -m evaluation.run_evaluation --round round3  # Input-Output 평가 (Bedrock Credential 필요)
python -m evaluation.generate_ragas_dataset         # RAGAS 1단계 (메인 venv)
```

`--round`는 `roundN` 또는 `roundN_retryNN` 형식이어야 하며, 같은 이름을 다시
쓰면 과거 결과를 덮어쓰지 않기 위해 실행이 거부된다. Run 저장 구조와
INVALID/Retry/Round 구분 규칙은 [docs/how_to_use.md](docs/how_to_use.md) 9.1절을
참고한다.

RAGAS 채점(2단계)은 의존성 충돌 때문에 별도 venv가 필요하다 — 자세한 이유와
명령은 [docs/how_to_use.md](docs/how_to_use.md) 9.2절을 참고한다.

```bash
python -m venv .venv-ragas
source .venv-ragas/Scripts/activate
pip install -r evaluation/requirements-ragas.txt
python -m evaluation.score_ragas_dataset
```

## RAGAS 4개 지표 / Round 1·2 Input-Output 통과율 / 개선폭

### 모델 전환: Sonnet 4.5 → Haiku 4.5

비용 절감을 위해 `BEDROCK_MODEL_ID`를 `claude-sonnet-4-5`에서
`claude-haiku-4-5`로 바꿨다. 이 전환 이후의 Bedrock Quota는 계정 단위로
걸려 있어 API Key만 교체해서는 회복되지 않는다는 점을 실제로 확인했다(아래
트라이앤에러 참고). 이번 Round 1/Round 2는 전부 Haiku 4.5 기준이며, 과거
Sonnet 4.5 기준 실측치(context_recall 0.50 등)와는 모델이 다르므로 직접
비교하지 않는다.

### Round 1 → Round 2 (실측, 2026-09-02~03, Haiku 4.5)

| 지표 | Round 1 (`round1_retry02`) | Round 2 (`round2_retry01`) | Delta |
|---|---:|---:|---:|
| 전체 Pass Rate | 70.0% (14/20) | **95.0%** (19/20) | **+25.0%p** |
| Guardrail Pass Rate | 100% (3/3) | 100% (3/3) | 0%p |
| Positive False Block Rate | 0% | 0% | 0%p |
| positive | 4/8 PASS | 7/8 PASS | +37.5%p |
| edge | 3/5 PASS | 5/5 PASS | +40%p |

| RAGAS 지표(4/5 Case 기준, E05는 근본원인 B로 제외) | 값 |
|---|---:|
| context_recall | 0.25 |
| context_precision | 0.75 |
| faithfulness | 0.238 |
| answer_relevancy | 0.301 |

Round 1의 FAIL 3건(P06/E04/E05) 중 2건은 Output Guardrail이 `"불확실한
수익성"`을 `"확실한 수익"`(정반대 의미)으로 오탐해 정상 분석 전체를
Fallback으로 대체한 것이 원인이었다 — 부정 접두어 인식 정규식으로 수정해
Round 2에서 전부 PASS로 전환됐다. 남은 ERROR 1건(P08)은 Haiku 4.5가
`InvestmentOpinion`의 일부 필드를 통째로 누락하는 Structured Output
신뢰성 문제로, 아직 근본 해결하지 못한 채 Round 3 후보 과제로 남아 있다.
전체 원인 분석은 [evaluation/round1_report.md](evaluation/round1_report.md)
(Round 1 상세 + 근본원인 A/B/C 분석), 개선 전후 비교는
[evaluation/round2_report.md](evaluation/round2_report.md)를 참고한다.

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
- **Structured Output이 드물게 잘림/깨짐(모델 불문 반복 발생)**: `ChatBedrock`에
  `max_tokens`를 지정하지 않아 Provider 기본값으로 필드가 많은
  `InvestmentOpinion`/`FinalThesis`가 잘리는 문제를 `MODEL_MAX_TOKENS`(기본
  8192) 도입으로 대부분 해결했다. 그러나 Haiku 4.5 전환 후에는 잘림이 아니라
  **형식 자체가 스키마를 벗어나는 문제**(list 필드를 dict나 단일 문자열로
  반환, 필드 자체 누락)가 반복 관찰됐다. 이를 프롬프트 문구로 완화하려던
  시도(모든 list 필드에 "순수 문자열 배열로만 작성하라" 문구 추가)가
  **오히려 역효과**를 내 Round 2 초기 실행의 Pass Rate를 70%→40%로 떨어뜨렸다
  — 실시간 재현으로 인과관계를 확인한 뒤 즉시 롤백했다(`evaluation/round2_report.md`
  "무효 처리된 첫 Round 2 시도" 절 참고). 교훈: Prompt 변경은 항상 개선을
  보장하지 않으며, 변경 직후 반드시 실측 재검증이 필요하다.
- **Output Guardrail의 부정 표현 오탐**: 금지 문구 `"확실한 수익"`이
  `"불확실한 수익성"`(정반대 의미의 정상적 위험 서술) 안에서 부분 문자열로
  오매칭되어, INTC "avoid" 분석처럼 완전히 정상적인 답변이 통째로 Safe
  Fallback 문구로 대체되는 실제 사례(Round 1 FAIL 3건 중 2건)를 발견했다.
  `src/guardrails.py`에 한국어 부정 접두어(불/무/안/못)를 인식하는 negative
  lookbehind 정규식을 적용해 해결했다. 부가로, Correction 단계(`_correct_output`)에
  실패 사유 코드만 전달되고 실제 매칭된 문구가 전달되지 않아 재시도가
  사실상 무력화된다는 점, 그리고 `build_safe_trace`가 이미 대체된 최종
  답변을 재검사해 원본이 실패했다는 사실을 감추는 관찰가능성 결함도 함께
  발견해 고쳤다.
- **Bedrock Read Timeout**: Round 1/Debate가 4-way 병렬로 동시에 Bedrock을
  호출할 때 botocore 기본 Read Timeout(60초)을 넘겨 실패하는 사례를 발견해
  `ChatBedrock(timeout=180, max_retries=3)`으로 조정했다.
- **Bedrock 일일 토큰 한도는 계정 단위 — API Key 교체로는 회복되지 않음**:
  Quota 소진 후 AWS Access Key만 새로 발급해 재시도했으나 즉시 동일한
  `ThrottlingException`이 재발했다. 이후 완전히 새 자격증명(및 비용
  절감을 위한 Haiku 4.5 전환)으로 교체하고서야 정상 동작을 확인했다 —
  "Too many tokens per day" 한도가 Access Key가 아니라 AWS 계정/리전
  단위로 걸린다는 것을 실제로 검증한 사례다.
- **Evaluation Run 결과가 `tee`의 종료 코드에 가려짐**: `python ... \| tee
  file`로 백그라운드 실행 결과를 캡처했을 때, 쉘의 종료 코드가 `python`이
  아니라 `tee`의 것으로 보고되어 스크립트가 실제로 중간에 크래시했는데도
  "성공(exit 0)"으로 오인할 뻔했다(RAGAS Dataset 생성 스크립트가 한 Case의
  Structured Output 실패로 조용히 죽은 사례). 이후 모든 백그라운드 실행에
  `{ cmd; echo "PYTHON_EXIT_CODE=$?"; } > log 2>&1` 패턴을 적용해 실제
  종료 코드를 명시적으로 기록하도록 바꿨다.
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
- **RAGAS 전용 venv가 새 Cross-region Inference Profile을 인식하지 못함**:
  `langchain-aws==0.2.20`의 Provider 자동 추출 로직이 region prefix로
  `eu`/`us`/`us-gov`/`apac`/`sa`만 인식해, Haiku 4.5의 `global.` prefix를
  provider 이름 자체로 오인하고 `NotImplementedError(Provider global model
  does not support chat.)`를 던졌다. `ChatBedrock(provider="anthropic")`을
  명시해 우회했다.
- **RAGAS `faithfulness`가 최신 Claude 모델에서 계속 실패(Sonnet 4.5 시절)**:
  `ragas==0.2.15`의 `is_finished` 판정이 `claude-sonnet-4-5`의 `stop_reason`
  값을 인식하지 못해 정상 응답도 실패로 오판했다(`is_finished_parser`
  Override로 우회). 이후 Haiku 4.5 전환과 위 두 수정을 거쳐 4개 지표 모두
  정상 측정에 성공했다.
- **PowerShell에서 응답 한글이 깨짐**: FastAPI 기본 `JSONResponse`가
  `Content-Type`에 `charset`을 명시하지 않아 PowerShell 5.1
  `Invoke-RestMethod`가 UTF-8 바이트를 Latin-1로 잘못 해석했다. `charset=utf-8`을
  명시하는 `UTF8JSONResponse`를 `default_response_class`로 지정해 해결했다
  (요청 쪽 인코딩 이슈와 PowerShell 우회법은 [docs/how_to_use.md](docs/how_to_use.md)
  7.1~7.2절 참고).
- **`evaluation/test_queries.csv`는 초안 상태로 시작**: REQUIREMENTS.md 23장에
  따라 이 자산이 없던 시점에 Coding Agent가 임의로 공식 평가셋을 만들지 않고,
  사용자 승인을 받는 절차를 거쳤다. 승인된 이후로는 Debug 목적으로 임의
  수정하지 않는다(37장).

## 알려진 제약

- **Docker Build/Run 미검증**: 이 저장소를 작업한 환경에 Docker가 설치되어
  있지 않아 `docker build`/`docker run` 자체를 실행해보지 못했다. Dockerfile은
  스펙대로 작성했으나 최종 검증이 필요하다.
- **Structured Output 신뢰성 잔존 이슈(Haiku 4.5)**: Round 2에서도 1건(P08)이
  `InvestmentOpinion` 필드 누락으로 ERROR 처리됐다. 프롬프트만으로 고치려던
  시도가 오히려 전체 회귀를 유발한 전례가 있어(위 트라이앤에러 참고),
  Round 3에서는 Chair/Member 호출을 여러 단계로 쪼개는 등 더 구조적인
  접근이 필요해 보인다.
- **RAGAS 표본 크기가 작음**: 20건 전체가 아니라 대표 5개 Case 중 4개
  (E05는 Structured Output 실패로 제외)로만 측정했다. context_recall(0.25)과
  faithfulness(0.238)가 낮게 나온 것이 실제 품질 문제인지, 표본 크기·측정
  설계(예: `contexts`가 RAG 문서만 담고 Company Snapshot/Tool 계산 결과는
  포함하지 않음)의 한계인지는 추가 분석이 필요하다(round1_report.md 참고).
- **12개 패턴 체크리스트 기준 필수 항목 갭**: [pjt_report.md](pjt_report.md)에
  정리했듯, RAG는 Dense Vector 검색만 지원하고(Hybrid/Reranking/Query
  Expansion 없음), Structured Output은 LCEL `\|` 체이닝 문법 대신
  `.with_structured_output().invoke()` 방식을 쓴다.
