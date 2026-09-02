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
