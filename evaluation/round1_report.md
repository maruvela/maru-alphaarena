# Round 1 평가 리포트

## 실행 일자

2026-09-02 (UTC 01:53:14, `evaluation/results/run_20260902T015314Z.json`)

## Application / Model Configuration

- `BEDROCK_MODEL_ID`: `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (Cross-region Inference Profile)
- `BEDROCK_EMBEDDING_MODEL_ID`: `amazon.titan-embed-text-v2:0`
- `MODEL_TEMPERATURE`: `0`
- `MODEL_MAX_TOKENS`: `8192`
- `RAG_TOP_K`: `3`
- Judge Model: 위와 동일 Chat 모델(`src.agent.get_chat_model()`), Temperature `0`, `src/prompts.py`의 `JUDGE_PROMPT`(버전: 이 커밋 시점 고정)

## 평가 Dataset 크기

`evaluation/test_queries.csv` 20건 (positive 8 / negative 4 / edge 5 / guardrail 3)

## 전체 Pass Rate

**95.0%** (19/20) — `evaluation/results/run_20260902T015314Z.json`의 `summary.overall_pass_rate = 0.95`

## Category별 Pass Rate

| category | PASS | FAIL | ERROR |
|---|---:|---:|---:|
| positive | 7 | 0 | 1 |
| negative | 4 | 0 | 0 |
| edge | 5 | 0 | 0 |
| guardrail | 3 | 0 | 0 |

## Guardrail Pass Rate

**100%** (3/3) — 목표 100% 충족

## Positive False Block Rate

**0%** — Positive Case 중 Scope/Guardrail로 잘못 차단된 사례 없음 (목표 0% 충족)

## RAGAS 4개 Metric

`evaluation/ragas_reference.csv`의 대표 5개 Case(P01/P02/P05/P07/E05)에 대해 측정.
`evaluation/results/ragas_20260902T034832Z_summary.json` / `.csv`.

| metric | value |
|---|---:|
| context_recall | 0.50 |
| context_precision | 0.70 |
| faithfulness | 측정 불가 (아래 참고) |
| answer_relevancy | 0.25 |

**faithfulness가 측정되지 않은 이유**: `ragas==0.2.15`(scikit-network 없는
마지막 계열, 4번째 개발 로그 참고)의 `is_finished` 판정 로직이 최신 Claude
모델(`claude-sonnet-4-5`)의 `stop_reason` 값을 인식하지 못해 정상 완료된
응답까지 실패로 오판했다(이는 `is_finished_parser` Override로 우회). 우회 후
드러난 진짜 원인은 faithfulness의 Claim 분해 단계가 답변이 길수록 매우 큰
JSON(문장마다 statement/reason/verdict)을 생성해야 해서 `max_tokens`
한도(8192, 이후 16000 시도)를 넘겨 응답이 잘리고 JSON Parse가 실패하는
것이었다. `max_tokens=16000`으로 재시도하던 중 AWS Bedrock 계정의 **일일
토큰 한도(`ThrottlingException: Too many tokens per day`)**에 도달해 이후
모든 호출이 실패했고, 한도 초과 전까지 faithfulness의 성공 여부를 완전히
확인하지 못했다. context_recall/context_precision/answer_relevancy는 한도
도달 전에 안정적으로 측정되었다.

**answer_relevancy가 낮게 나온 것에 대한 해석**: 0.25는 낮은 편이며, 이는
정성적으로 훌륭했던 실제 답변(NVDA 분석 등, README 참고)의 관련성이 실제로
낮다기보다 다음 두 가지 방법론적 요인이 크다고 판단한다.
(1) RAGAS의 answer_relevancy는 답변으로부터 역으로 생성한 질문과 원 질문 간
Embedding 유사도를 재는데, 우리 답변은 Markdown 10개 섹션으로 구성된 매우
길고 구조화된 문서라 "역질문"이 원 질문 하나로 수렴하기 어렵다.
(2) `reference_answer`(ground_truth) 없이도 계산되는 지표지만, 우리 Reference
자체가 짧고 개괄적이라(evaluation/ragas_reference.csv) 비교 기준이 후하지 않다.
Round 2에서 원인을 더 분석할 가치가 있다.

## 대표 실패 Case

**P07** (positive, `"NVDA에 대해 Member들 사이에서 의견이 갈리는 핵심 쟁점이 뭐야?"`) — `ERROR`

```text
AgentError: Structured Output 생성 실패 (FinalThesis): 1 validation error for FinalThesis
evidence
  Field required [type=missing, ...]
```

## 실패 원인 분석

`arena_chair` 노드가 생성하는 `FinalThesis`는 13개 필드 중 다수가 list이며, 특히
`evidence`(EvidenceRef 객체 리스트)가 스키마상 **마지막 필드**다. Member 4명의
견해가 풍부하고 Debate에서 쟁점이 많을수록 Chair가 `summary`~`conditions_to_revisit`
까지 서술하는 데 이미 상당한 출력 토큰을 소비하고, 그 결과 `max_tokens`
(현재 8192) 한도 안에서 `evidence` 필드까지 도달하지 못하고 Tool Call JSON이
잘리는 경우가 드물게 발생한다. 이는 REQUIREMENTS.md 29.4가 명시한 "Malformed
Structured Output" 상황이며, 스펙대로 1회 Retry 후에도 같은 이유로 재실패해
`ERROR`로 정확히 분류되었다(`FAIL`로 위장하지 않음 — 24장 요구사항 충족).

같은 원인의 문제를 개발 중 두 번 더 발견해 이미 수정했다(이번 실행 전 반영됨).

1. `ChatBedrock`에 `max_tokens`를 지정하지 않아 Provider 기본값으로 Round 1
   `InvestmentOpinion`(필드가 많음)이 잘리는 문제 → `MODEL_MAX_TOKENS`(기본
   4096 → 이후 8192로 재조정) 도입.
2. Round 1/Debate가 4-way 병렬로 동시에 Bedrock을 호출할 때 botocore 기본
   Read Timeout(60s)을 넘겨 `ReadTimeoutError`가 발생 → `ChatBedrock(timeout=180,
   max_retries=3)`로 조정.

P07 ERROR는 이 두 수정을 반영한 **이후**에도 남은, 훨씬 드문 잔여 사례다
(20건 중 1건, Chair 단계에서만 발생).

## 개선 계획 (Round 2에서 적용 예정)

1. `CHAIR_PROMPT`(`src/prompts.py`)에 각 서술형 필드(특히 `summary`,
   `business_quality_view`, `price_value_view`, 각 리스트 항목)를 간결하게
   작성하도록 명시적 길이 가이드를 추가해, 애초에 `evidence`까지 도달하기 전에
   토큰을 소진하는 근본 원인을 줄인다.
2. `MODEL_MAX_TOKENS` 여유를 조금 더 확보(예: 10000~12000)하는 것을 병행
   검토한다 — 단, 무한정 늘리는 것보다 (1)의 간결화가 더 근본적인 해결책이라고
   판단해 우선순위를 둔다.
3. Round 2는 동일한 20건 Dataset, 동일 Judge Model/Prompt/Temperature로
   재실행하여 P07 ERROR 해소 여부와 전체 Pass Rate 변화를 확인한다.

---

# Round 1 공식 확정본 재실행 (Haiku 4.5, `round1_retry02`) — 문제점 분석과 Round 2 개선 방향

> 위 섹션은 Sonnet 4.5 기준 초기 측정이다. 이후 비용 절감을 위해
> `BEDROCK_MODEL_ID`를 Haiku 4.5로 변경했고, Bedrock 일일 Quota 소진으로
> 두 차례 INVALID(run_id: `20260902_163516_round1`,
> `20260902_172238_round1_retry01`)를 거쳐 **`20260902_173553_round1_retry02`**가
> 유효(valid)한 공식 Round 1로 확정되었다. 아래는 이 Run의 FAIL/ERROR 6건
> 전체에 대한 근본 원인 분석과 Round 2 개선 방향이다. **이 섹션은 분석
> 결과만 기록하며, 아래 개선안은 아직 코드에 반영하지 않았다** — Round 2
> 착수 전 사용자 검토를 거친다.

## 공식 Round 1 결과 요약

- run_id: `20260902_173553_round1_retry02` (`evaluation/runs/20260902_173553_round1_retry02/`)
- Model: `global.anthropic.claude-haiku-4-5-20251001-v1:0`, Temperature 0, `MODEL_MAX_TOKENS=8192`
- Pass Rate: **70.0%** (14/20), Guardrail Pass Rate 100%, Positive False Block 0%
- FAIL 3건(P06, E04, E05) / ERROR 3건(P02, P07, P08)

## Case별 근본 원인 요약

| Case | Category | 상태 | 근본 원인 |
|---|---|---|---|
| P02 | positive | ERROR | Chair가 `FinalThesis.disagreements`(스키마: `list[str]`)를 `list[dict]`로 반환 → Pydantic 검증 실패 |
| P07 | positive | ERROR | 동일 원인(다른 실행에서는 `bull_case` 등 list 필드 전체가 `<parameter name="...">` 형태의 문자열로 뭉개짐) |
| P08 | positive | ERROR | Round 1 Member의 `InvestmentOpinion`에서 `key_reasons`/`risks`/`assumptions` 등 필수 필드 자체가 누락 |
| P06 | positive | FAIL | 정상적인 INTC "avoid" 분석이 Output Guardrail 오탐으로 통째로 대체됨 (아래 근본원인 A) |
| E04 | edge | FAIL | P06과 동일 — INTC 관련 Chair 출력이 동일한 오탐 문구에 걸려 SAFE_FALLBACK으로 대체됨 |
| E05 | edge | FAIL | Bull Case/재검토 조건에서 Snapshot에 없는 미래 성장률(7~9%, 12%)을 hedge 없이 사실처럼 서술 |

## 근본원인 A — Output Guardrail 오탐 (FAIL 3건 중 2건의 원인, 최우선 수정 대상)

`src/guardrails.py`의 `_FORBIDDEN_EXPRESSIONS`는 단순 부분 문자열 매칭이다.
`"확실한 수익"` 항목이 **`"불확실한 수익성"`**(불 + 확실한 수익 + 성)이라는,
의미가 정반대인 정상적 위험 서술 문구 안에서 매칭되어 버린다.

실제로 `INTC?` 질의를 다시 실행해 재현했다 — Chair가 생성한 정상적인 avoid
분석 초안에 다음 문장이 포함되어 있었다:

> "...자본 집약적 R&D·제조 투자의 **불확실한 수익성**이 자본 영구 훼손의
> 실질적 위험을 구성한다..."

`guardrails.check_output()`은 이 문장을 `forbidden_expression`(사유: `'확실한
수익'` 매칭)으로 차단했다. 이어지는 1회 Correction(`_correct_output`)도
실패했는데, 원인은 `_correct_output`에 실제 매칭된 문구("확실한 수익")나
그 위치가 아니라 사유 코드(`forbidden_expression`)만 전달되기 때문이다 —
Correction LLM은 "확정적 수익 표현을 고치라"는 지시만 받을 뿐 정확히 어떤
문자열이 문제인지 알 수 없고, INTC의 위험을 설명하려면 "불확실한
수익성" 같은 표현을 다시 쓸 수밖에 없어 동일한 문구가 재생성되며 두 번째
검사도 실패한다(`final_ok=False`). 결과적으로 완전히 정상적인 분석이
버려지고 사용자에게는 사과문만 반환된다 — P06/E04가 정확히 이 경로로
FAIL했다(둘 다 INTC, 둘 다 `verdict=avoid confidence=0.76`으로 Chair
결과까지 동일했다).

부가로 발견한 관련 결함: `build_safe_trace()`의 `output_guardrail` 단계는
최종 `answer`(이미 SAFE_FALLBACK으로 대체된 문구)에 대해
`check_output()`을 **다시** 실행해 `allowed=True`를 기록한다. SAFE_FALLBACK
문구 자체는 당연히 깨끗하므로 `allowed=True`가 찍히지만, 이는 "원본 답변이
검사를 통과했다"는 오해를 준다 — 실제로는 원본이 두 번 실패해 완전히
다른 문구로 교체된 것이다. Safe Trace가 실제 처리 결과(원본 실패 →
Correction 실패 → Fallback 대체)를 반영하지 못하는 관찰가능성(observability)
결함이다.

## 근본원인 B — Haiku 4.5의 Structured Output 스키마 준수 신뢰도

P02/P07(Chair 단계, `FinalThesis`)과 P08(Round 1 Member 단계,
`InvestmentOpinion`)은 서로 다른 증상이지만 같은 계열의 문제다: Haiku
4.5가 필드 수가 많고 List 필드가 많은 스키마(`FinalThesis`는 13개 필드)를
Tool-calling 방식으로 안정적으로 채우지 못한다.

- P02: `disagreements`(`list[str]`)를 `[{'issue': '...'}, ...]`로 반환
- P07(이번 Run): `bull_case` 등이 `list_type` 대신 `str`로 반환
- P07(별도 재현, RAGAS 데이터셋 생성 중): 같은 필드들이 `<parameter
  name="bull_case">...` 형태의 유사 Tool-call XML 텍스트 통째로 반환
- P08: `key_reasons`/`risks`/`assumptions` 필드가 아예 누락(`Field required`)

Temperature 0임에도 같은 P07 Case가 실행마다 다른 방식으로 깨지는 것으로
보아, Bedrock 응답이 완전히 결정론적이지는 않으며, 이는 근본적으로
모델(Haiku 4.5)이 크고 복잡한 Structured Output 스키마를 안정적으로
따르지 못하는 신뢰도 문제로 판단된다. `_invoke_structured`의 "1회 Retry
후 실패시 AgentError" 정책 자체는 REQUIREMENTS.md 24/29.4장대로 정확히
동작했다(FAIL로 위장하지 않고 ERROR로 정확히 분류됨) — 문제는 정책이
아니라 1차 시도와 Retry 모두에서 실패할 만큼 실패율 자체가 높다는 점이다.

## 근본원인 C — 가정(Assumption)과 확인된 사실(Fact)의 경계 흐림 (E05)

COST의 미래 매출 성장률을 묻는 질문에 Chair는 결론부에서는 "내년 정확한
매출 성장률 예상치는 공개 정보에 없다"고 올바르게 명시했지만, 동시에
Bull Case("향후 3~5년 평균 7~9% 이상의 성장이 지속될 수 있다")와 재검토
조건("향후 2~3년 매출 성장률이 12% 이상으로 가속화")에서는 동일한 수치를
명시적 가정 표시(예: "~라고 가정하면") 없이 서술했다. `재검토 조건`
필드는 본래 가상의 임계값 조건을 다루는 것이 정상이지만, 서술 방식이
"예측"처럼 읽혀 Judge가 Forbidden 조건("Snapshot에 없는 미래 성장률
수치를 사실처럼 단정") 위반으로 판정했다.

## Round 2 개선안 (우선순위 순, 아직 미적용)

1. **(최우선, 저위험) Output Guardrail 오탐 수정**
   - `_FORBIDDEN_EXPRESSIONS`의 `"확실한 수익"` 등 짧은 부분 문자열을
     부정 접두어(불/무/안/못) 뒤에 붙어 있으면 매칭에서 제외하도록 정규식화하거나,
     "확실한 수익을 보장/약속" 처럼 실제 확정적 약속 표현만 잡는 더 긴 구문으로 좁힌다.
   - `_correct_output`에 사유 코드뿐 아니라 실제 매칭된 문구(및 주변 문맥)를
     함께 전달해, Correction이 정확히 무엇을 고쳐야 하는지 알 수 있게 한다.
   - `build_safe_trace()`의 `output_guardrail` 단계가 최종 answer를
     재검사하는 대신, `node_render_answer`가 실제로 계산한 처리 결과
     (원본 통과 / Correction 후 통과 / Fallback 대체)를 그대로 반영하도록 수정한다.
2. **(중요, 중간 난이도) Haiku Structured Output 신뢰도 개선**
   - `CHAIR_PROMPT`/Member Prompt에 "모든 List 필드는 순수 문자열 배열로만
     작성하고 XML 태그(`<item>`, `<parameter>` 등)를 사용하지 말라"는
     명시적 포맷 제약을 추가한다.
   - `FinalThesis`처럼 필드가 많은 스키마는 Chair 호출을 여러 단계로
     쪼개는 방안을 검토한다(예: 서술형 필드 1차 생성 → Evidence/Bull/Bear
     등 List 필드 2차 생성) — 근본적이지만 구조 변경이 필요하다.
   - 최소 변경안으로 `_invoke_structured`의 Retry 횟수를 1회에서 2회로
     늘리는 방안도 있으나, 비용 증가 대비 근본 해결책은 아니다.
3. **(쉬움) Assumption과 Fact의 경계 강화**
   - Member/Chair Prompt의 Bull Case/Bear Case/재검토 조건 섹션에 "구체적
     수치를 언급할 때는 반드시 Snapshot 근거이거나, 명시적 가정 표시(예:
     '~라고 가정하면', '~를 조건으로')를 동반하라"는 규칙을 추가한다.

## RAGAS 관련 별도 발견 (이미 코드에 반영·재실행 완료)

Round 2 개선안과 별개로, 이번 RAGAS 측정 과정에서 발견해 이미 수정을
반영한 두 가지 코드 결함이 있다(둘 다 위 개선안과 무관하게 이미 적용됨):

- `evaluation/generate_ragas_dataset.py`: Case별 예외 처리가 없어 한
  Case의 `AgentError`(근본원인 B와 동일 계열)가 전체 Dataset 생성을
  중단시켰다. Case별 `try/except`를 추가해 실패한 Case만 건너뛰고
  (`evaluation/results/ragas_dataset_skipped.json`에 사유 기록)
  나머지는 계속 진행하도록 수정했다.
- `evaluation/score_ragas_dataset.py`: RAGAS 전용 venv의
  `langchain-aws==0.2.20`이 Cross-region Inference Profile의 `"global."`
  prefix를 인식하지 못해(`eu`/`us`/`us-gov`/`apac`/`sa`만 인식) provider를
  `"global"`로 오인, `NotImplementedError(Provider global model does not
  support chat.)`를 던졌다. `ChatBedrock(provider="anthropic")`을 명시해
  우회했다.

수정 후 재측정한 RAGAS 결과(4/5 Case, E05는 근본원인 B로 계속 제외됨):
`context_recall=0.25`, `context_precision=0.75`, `faithfulness=0.238`,
`answer_relevancy=0.301` (`evaluation/results/ragas_20260903T001821Z_summary.json`).
