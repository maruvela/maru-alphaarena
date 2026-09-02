"""
Member/Debate/Chair Prompt에 한국어 응답 규칙이 실제로 포함되어 있는지, 그리고
이 정책이 Structured Output의 Enum/필드명(영어 유지)을 건드리지 않는지 확인하는
결정론적 테스트. LLM을 호출하지 않는다.
"""

from src import prompts
from src.models import ConflictType, Stance

_MEMBER_PROMPTS = [
    prompts.BUFFETT_MEMBER_PROMPT,
    prompts.LYNCH_MEMBER_PROMPT,
    prompts.MARKS_MEMBER_PROMPT,
    prompts.DAMODARAN_MEMBER_PROMPT,
]


def test_all_member_prompts_contain_language_policy():
    for member_prompt in _MEMBER_PROMPTS:
        assert prompts._LANGUAGE_POLICY in member_prompt


def test_debate_prompt_contains_language_policy():
    assert prompts._LANGUAGE_POLICY in prompts.DEBATE_PROMPT


def test_chair_prompt_contains_language_policy_and_example():
    assert prompts._LANGUAGE_POLICY in prompts.CHAIR_PROMPT
    # Chair는 Member의 영어 문장을 그대로 복사하지 말라는 구체적 예시가 있어야 한다.
    assert "BAD:" in prompts.CHAIR_PROMPT
    assert "GOOD:" in prompts.CHAIR_PROMPT


def test_output_correction_prompt_preserves_korean_language():
    assert "한국어" in prompts.OUTPUT_CORRECTION_PROMPT


def test_language_policy_allows_english_terms_and_enum_values():
    """언어 정책 문구 자체가 Enum/고유명사/기술 용어는 영어로 유지하라고
    명시하는지 확인한다 — 이 정책이 Schema를 침범하지 않는다는 근거."""

    text = prompts._LANGUAGE_POLICY
    for allowed_term in ("Ticker", "P/E", "ROE", "stance"):
        assert allowed_term in text


def test_stance_and_conflict_type_enum_values_remain_english():
    """이번 언어 정책 작업이 Pydantic Enum 값(영어)을 바꾸지 않았는지 확인한다."""

    assert {s.value for s in Stance} == {"strong_buy", "buy", "neutral", "avoid", "sell"}
    assert {c.value for c in ConflictType} == {"fact", "assumption", "valuation", "risk", "time_horizon"}
