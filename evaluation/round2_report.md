# Round 2 평가 리포트

> Round 1과 동일 Test Dataset / Judge Model / Judge Prompt / Temperature로 재실행한 뒤
> 채운다. REQUIREMENTS.md 27.2를 따른다. Round 1을 의도적으로 나쁘게 만들지 않는다.

## 실행 일자

2026-09-03. 공식 Round 2 = `round2_retry01`(`evaluation/runs/20260903_115345_round2_retry01/`),
2026-09-03T02:53:45Z ~ 2026-09-03T03:12:04Z (18분 19초).

Round 1 비교 기준: `round1_retry02`(`evaluation/runs/20260902_173553_round1_retry02/`,
Haiku 4.5 공식 확정본). 두 Run 모두 동일 Test Dataset(`evaluation/test_queries.csv`
20건), 동일 Model(`global.anthropic.claude-haiku-4-5-20251001-v1:0`), 동일
Temperature(0)·`MODEL_MAX_TOKENS`(8192)·`RAG_TOP_K`(3)로 실행되어 비교가 유효하다.

## ⚠️ 무효 처리된 첫 Round 2 시도(`20260903_114018_round2`)

Round 2 개선을 반영한 직후 처음 실행한 `round2`(run_id `20260903_114018_round2`)는
**PASS 8 / FAIL 0 / ERROR 12 (40%)**로 Round 1(70%)보다 크게 나빠졌다. 원인은
Bedrock Quota/Timeout 같은 인프라 문제가 아니라 **이번 Round 2 개선 작업 중
직접 도입한 Prompt 회귀**였다 — `src/prompts.py`의 모든 Structured Output
요구사항 블록(`_STRUCTURED_OUTPUT_NOTICE`/`DEBATE_PROMPT`/`CHAIR_PROMPT`)에
"list 필드의 각 항목은 순수 문자열이어야 한다"는 형식 제약 문구
(`_STRUCTURED_OUTPUT_FORMAT_NOTICE`)를 추가했는데, 이 문구가 오히려 Haiku
4.5의 `InvestmentOpinion` Structured Output 안정성을 크게 악화시켰다(12건의
ERROR 전부가 정확히 이 문구가 삽입된 `key_reasons` 등 필드에서 발생, 이전
Round 1에서는 20건 중 1건꼴이었던 것과 대비됨).

실시간 재현 쿼리로 이 인과관계를 확인한 뒤 해당 문구를 전면 롤백했고(가드레일
오탐 수정과 가정/사실 hedging 규칙은 그대로 유지), 롤백 직후 재현 쿼리에서
Round 1 Member 4명 전원이 다시 정상 성공함을 확인했다. 이 무효 Run은 삭제하지
않고 `evaluation/runs/20260903_114018_round2/`에 그대로 보존했지만(24.1장
"과거 Run 결과 보존" 원칙), **Round 1↔Round 2 비교에는 사용하지 않는다** — 이는
Quota/Timeout류 INVALID와는 다른 사유(자체 도입 회귀)이므로 `run_manifest.json`의
`status`는 `"valid"`로 정확히 남아 있으나(감지 로직상 인프라 신호가 아니므로
당연한 결과), Round 성능 측정으로서는 무효다. 재실행분은 통상적인 재시도
사유(인프라 실패)와는 다르지만 "같은 코드 의도, 회귀만 되돌림"이라는 점에서
`round2_retry01`로 명명했다 — 24.1장의 Retry 명명 규칙이 원래 상정한 사유(Quota
Timeout)와 다르다는 점을 여기 명시적으로 기록한다.

## Round 1 이후 변경 사항

1. **Output Guardrail 오탐 수정**(`src/guardrails.py`) — `_FORBIDDEN_EXPRESSIONS`의
   한국어 문구에 부정 접두어(불/무/안/못) 인식 negative lookbehind를 적용해
   `"확실한 수익"`이 `"불확실한 수익성"`(정반대 의미) 안에서 오매칭되지 않도록
   수정. `_correct_output`에 실제 매칭 문구+문맥(`describe_output_violation`)을
   전달해 Correction이 정확히 무엇을 고쳐야 하는지 알 수 있게 함.
2. **Safe Trace 정확도 수정**(`src/agent.py`, `src/state.py`) — `output_guardrail`
   단계가 이미 대체된 최종 answer를 재검사하는 대신, `node_render_answer`가
   실제로 계산한 처리 결과(원본 통과/Correction/Fallback)를 `output_guardrail_status`
   State 필드로 넘겨 그대로 기록하도록 수정.
3. **가정/사실 hedging 규칙 추가**(`src/prompts.py`, `CHAIR_PROMPT`) —
   bull_case/bear_case/decisive_factors/conditions_to_revisit에서 Snapshot에
   없는 구체적 수치를 언급할 때 명시적 가정/조건 표현을 동반하도록 규칙 추가.
4. (시도했다가 롤백) list 필드 형식 강제 문구 — 위 "무효 처리" 절 참고. Round 2
   최종본에는 **포함되지 않음**.

## 전체 Delta

**+25.0%p** (Round 2 95.0% − Round 1 70.0%)

## Category별 Delta

| category | Round 1 (`round1_retry02`) | Round 2 (`round2_retry01`) | Delta |
|---|---:|---:|---:|
| positive | 4/8 PASS (50%) | 7/8 PASS (87.5%) | +37.5%p |
| negative | 4/4 PASS (100%) | 4/4 PASS (100%) | 0%p |
| edge | 3/5 PASS (60%) | 5/5 PASS (100%) | +40%p |
| guardrail | 3/3 PASS (100%) | 3/3 PASS (100%) | 0%p |

Guardrail Pass Rate 100% → 100% (유지), Positive False Block 0% → 0%(유지).

## RAGAS Metric Delta

Round 2에서는 RAGAS를 재측정하지 않았다(사용자 지시: 이번에는 Round 2까지만
진행). Round 1 측정치(4/5 Case 기준)는 `context_recall=0.25`,
`context_precision=0.75`, `faithfulness=0.238`, `answer_relevancy=0.301`
(`evaluation/results/ragas_20260903T001821Z_summary.json`, 상세는
`round1_report.md` 참고).

## 해결된 실패 Case

- **P06**(positive, INTC 매수 분석) — Round 1 FAIL → Round 2 PASS. 근본원인 A
  (Output Guardrail 오탐)가 해소되어 정상적인 avoid 분석이 그대로 사용자에게
  전달됨.
- **E04**(edge, `"INTC?"`) — Round 1 FAIL → Round 2 PASS. P06과 동일한 근본원인
  A 수정으로 해결.
- **E05**(edge, COST 성장률 질문) — Round 1 FAIL → Round 2 PASS. 가정/사실
  hedging 규칙 추가로 Bull Case/재검토 조건의 수치 서술 방식이 개선됨.
- **P02, P07**(positive, ERROR) — Round 1 ERROR → Round 2 PASS. `FinalThesis.
  disagreements` dict 반환 문제가 이번 실행에서는 재현되지 않음(근본원인 B는
  구조적으로 해결되지 않았으므로 우연히 발생하지 않은 것에 가깝다 — 아래
  "남은 실패 Case" 참고).

## 남은 실패 Case

- **P08**(positive, "COST 분석에서 소수 의견이 있으면 알려줘") — Round 1과 Round 2
  모두 동일하게 ERROR. `InvestmentOpinion`의 `key_reasons`/`risks`/`assumptions`
  등 필수 필드 자체가 누락되는 근본원인 B(Haiku 4.5 Structured Output 신뢰성)의
  잔존 사례. 이번 Round 2 개선 범위에서는 프롬프트만으로 해결을 시도하지
  않았다(1차 시도가 오히려 전면 회귀를 유발한 것을 확인했기 때문 — 위 "무효
  처리" 절 참고). Round 3 후보 과제로 남긴다: Chair/Member 호출을 여러 단계로
  쪼개는 등 더 구조적인 접근이 필요해 보인다.

## Regression 여부

Round 1 대비 하락한 지표 없음(모든 Category Delta가 0 이상). 단, 위에서 기록한
대로 Round 2 개발 과정 중 한 차례 심각한 회귀(40%)를 자체적으로 발생시켰다가
원인을 진단해 되돌린 뒤 최종 측정한 것이므로, 이 리포트의 Round 2 수치는
그 회귀가 제거된 이후의 코드 기준이다. 무효 Run(`20260903_114018_round2`)은
"Prompt 변경이 항상 개선을 보장하지 않으며, 변경 직후에도 반드시 실측
검증이 필요하다"는 사례로 남긴다.
