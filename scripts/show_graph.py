"""
scripts/show_graph.py

LangGraph의 실제 Node/Edge 구조를 실행 없이 정적으로 출력하는 보조 스크립트.

`src.agent.build_graph()`는 Node를 등록하고 컴파일만 할 뿐 어떤 Node 함수도
실제로 호출하지 않으며, `ChatBedrock` 인스턴스 역시 최초 LLM 호출 시점에만
지연 생성된다(`src/agent.py`의 `_get_chat_model` 참고) — 따라서 이 스크립트는
LLM/Bedrock/Embedding을 전혀 호출하지 않는다.

실행:
    python scripts/show_graph.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# src.*를 import할 수 있도록 Repository 루트를 sys.path에 추가한다(이
# 스크립트를 `python scripts/show_graph.py`처럼 직접 실행하는 경우 대비).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent import get_graph  # noqa: E402


def main() -> None:
    graph = get_graph().get_graph()

    print("=== Nodes ===")
    for name in graph.nodes:
        print(f"- {name}")

    print()
    print("=== Edges (정적으로 확인 가능한 것만) ===")
    for edge in graph.edges:
        label = f"  [{edge.data}]" if edge.conditional else ""
        print(f"- {edge.source} -> {edge.target}{label}")

    print()
    print(
        "주의: round1_fanout/debate_fanout은 Send() 기반 동적 Fan-out(9.2/9.3장)이라\n"
        "LangGraph의 정적 그래프 분석기가 실제 목적지(round1_member/debate_member)를\n"
        "추론하지 못해 위 Edges 목록과 아래 draw_mermaid() 출력 모두에서 두 Node가\n"
        "고립되어(__end__로만 이어지는 것처럼) 보인다. 실제 런타임 흐름은 정확히\n"
        "add_conditional_edges에 넘긴 route_round1/route_debate 함수가 결정하며,\n"
        "정확한 전체 구조는 docs/how_to_use.md 12절의 (직접 검증해 작성한) Mermaid\n"
        "Diagram을 참고할 것 — 이 자동 출력을 그대로 신뢰하지 않는다."
    )

    print()
    print("=== draw_mermaid() 원본 출력(위 주의사항과 함께 참고용으로만 사용) ===")
    print(graph.draw_mermaid())


if __name__ == "__main__":
    main()
