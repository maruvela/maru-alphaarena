"""
scripts/build_wisdom.py

data/raw/ 아래의 HTML/PDF 원문을 읽어서
Alpha Arena RAG용 Markdown corpus(data/wisdom/)를 생성한다.

특징
- LLM 사용 안 함
- HTML 메뉴/스크립트/스타일 제거
- PDF 텍스트 추출
- Member별 핵심 키워드 기반 문단 선별
- 선택된 문단의 앞/뒤 문단도 같이 보존하여 문맥 유지
- source_manifest.json metadata 연결
- YAML front matter 포함
- data/wisdom/index.json 생성

실행:
    python scripts/build_wisdom.py

전체 본문을 거의 유지하려면:
    python scripts/build_wisdom.py --mode full

특정 Member만:
    python scripts/build_wisdom.py --member buffett
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from pypdf import PdfReader


# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = ROOT / "data" / "raw"
WISDOM_DIR = ROOT / "data" / "wisdom"
MANIFEST_FILE = ROOT / "data" / "source_manifest.json"


# ---------------------------------------------------------
# Member configuration
# ---------------------------------------------------------

MEMBER_CONFIG = {
    "buffett": {
        "style": "Quality / Moat / Long-term Compounder",
        "style_ko": "기업의 질 · 경제적 해자 · 장기 복리",
        "keywords": [
            "moat",
            "competitive advantage",
            "economic franchise",
            "franchise",
            "pricing power",
            "intrinsic value",
            "return on capital",
            "return on equity",
            "capital allocation",
            "capital employed",
            "incremental capital",
            "management",
            "manager",
            "long-term",
            "long term",
            "business economics",
            "great business",
            "good business",
            "owner earnings",
            "cash",
            "earnings",
            "price",
            "value",
        ],
    },

    "lynch": {
        "style": "Growth / Business Momentum",
        "style_ko": "성장 · 사업 모멘텀",
        "keywords": [
            "growth",
            "earnings",
            "earnings growth",
            "company",
            "business",
            "story",
            "research",
            "understand",
            "understanding",
            "sales",
            "revenue",
            "profit",
            "profits",
            "market share",
            "product",
            "customers",
            "price earnings",
            "p/e",
            "peg",
            "long term",
            "long-term",
            "invest",
            "investment",
        ],
    },

    "marks": {
        "style": "Risk / Price / Market Cycle",
        "style_ko": "위험 · 가격 · 시장 사이클",
        "keywords": [
            "risk",
            "uncertainty",
            "price",
            "value",
            "cycle",
            "market cycle",
            "market",
            "sentiment",
            "psychology",
            "consensus",
            "contrarian",
            "expectations",
            "second-level",
            "second level",
            "downside",
            "loss",
            "return",
            "risk-adjusted",
            "forecast",
            "forecasting",
            "interest rate",
            "temperature",
            "optimism",
            "pessimism",
        ],
    },

    "damodaran": {
        "style": "Valuation / Intrinsic Value",
        "style_ko": "가치평가 · 내재가치",
        "keywords": [
            "valuation",
            "value",
            "intrinsic value",
            "discounted cash flow",
            "dcf",
            "cash flow",
            "free cash flow",
            "discount rate",
            "cost of capital",
            "terminal value",
            "terminal growth",
            "growth",
            "expected growth",
            "risk",
            "beta",
            "equity risk premium",
            "relative valuation",
            "multiple",
            "multiples",
            "price",
            "earnings",
            "reinvestment",
            "return on capital",
        ],
    },
}


# ---------------------------------------------------------
# Manifest
# ---------------------------------------------------------

def load_manifest() -> dict[str, dict[str, Any]]:
    """
    source_manifest.json의 source 목록을
    doc_id -> metadata 형태로 변환한다.
    """

    if not MANIFEST_FILE.exists():
        return {}

    data = json.loads(
        MANIFEST_FILE.read_text(encoding="utf-8")
    )

    result = {}

    for source in data.get("sources", []):
        result[source["doc_id"]] = source

    return result


# ---------------------------------------------------------
# Text normalization
# ---------------------------------------------------------

def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # 여러 공백
    text = re.sub(r"[ \t]+", " ", text)

    # 지나친 빈 줄
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def normalize_paragraph(text: str) -> str:
    text = normalize_text(text)

    # 줄바꿈을 문단 내부에서는 공백으로
    text = re.sub(r"\s*\n\s*", " ", text)

    return text.strip()


# ---------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------

REMOVE_TAGS = [
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "form",
    "button",
    "nav",
    "footer",
]


def extract_html(path: Path) -> tuple[str, str]:
    html = path.read_text(
        encoding="utf-8",
        errors="ignore",
    )

    soup = BeautifulSoup(html, "html.parser")

    title = ""

    if soup.title:
        title = soup.title.get_text(
            " ",
            strip=True,
        )

    for tag_name in REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # main/article 우선
    container = (
        soup.find("main")
        or soup.find("article")
        or soup.body
        or soup
    )

    paragraphs = []

    # 문단/제목/리스트 위주로 가져온다.
    for node in container.find_all(
        ["h1", "h2", "h3", "h4", "p", "li"]
    ):
        text = normalize_paragraph(
            node.get_text(" ", strip=True)
        )

        if not text:
            continue

        # 너무 짧은 navigation 조각 제거
        if len(text) < 30:
            continue

        paragraphs.append(text)

    # 특정 오래된 HTML은 <p> 구조가 약할 수 있음
    if len(paragraphs) < 5:
        raw = container.get_text(
            "\n",
            strip=True,
        )

        paragraphs = [
            normalize_paragraph(p)
            for p in raw.split("\n")
            if len(normalize_paragraph(p)) >= 40
        ]

    return title, "\n\n".join(paragraphs)


# ---------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------

def extract_pdf(path: Path) -> tuple[str, str]:
    reader = PdfReader(str(path))

    parts = []

    for page_no, page in enumerate(
        reader.pages,
        start=1,
    ):
        text = page.extract_text() or ""

        text = normalize_text(text)

        if not text:
            continue

        parts.append(
            f"\n[Page {page_no}]\n{text}"
        )

    return path.stem, "\n".join(parts)


# ---------------------------------------------------------
# Paragraph processing
# ---------------------------------------------------------

def split_paragraphs(text: str) -> list[str]:
    paragraphs = []

    # PDF 페이지 마커는 그냥 문단으로 유지
    for raw in re.split(r"\n\s*\n", text):
        p = normalize_paragraph(raw)

        if len(p) < 40:
            continue

        paragraphs.append(p)

    return paragraphs


def deduplicate(
    paragraphs: list[str],
) -> list[str]:

    seen = set()
    output = []

    for p in paragraphs:
        key = re.sub(
            r"\W+",
            "",
            p.lower(),
        )[:500]

        if key in seen:
            continue

        seen.add(key)
        output.append(p)

    return output


# ---------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------

def paragraph_score(
    paragraph: str,
    member: str,
) -> int:

    keywords = MEMBER_CONFIG[
        member
    ]["keywords"]

    lower = paragraph.lower()

    score = 0

    for keyword in keywords:
        count = lower.count(
            keyword.lower()
        )

        if count:
            # 같은 단어 반복에 너무 큰 점수를 주지 않는다.
            score += min(count, 3)

            # multi-word keyword 가중치
            if " " in keyword:
                score += 1

    return score


def select_focused_paragraphs(
    paragraphs: list[str],
    member: str,
    min_score: int = 1,
    neighbor_window: int = 1,
    max_paragraphs: int = 60,
) -> list[str]:
    """
    핵심 문단 + 앞/뒤 문단을 선택한다.

    예:
      관련 문단 index 10이면
      9, 10, 11도 함께 가져온다.
    """

    scored = []

    for idx, paragraph in enumerate(
        paragraphs
    ):
        score = paragraph_score(
            paragraph,
            member,
        )

        if score >= min_score:
            scored.append(
                (idx, score)
            )

    # 점수가 높은 문단 우선
    scored.sort(
        key=lambda x: x[1],
        reverse=True,
    )

    selected_indices = set()

    for idx, _score in scored:
        for offset in range(
            -neighbor_window,
            neighbor_window + 1,
        ):
            candidate = idx + offset

            if 0 <= candidate < len(
                paragraphs
            ):
                selected_indices.add(
                    candidate
                )

        if len(selected_indices) >= max_paragraphs:
            break

    return [
        paragraphs[i]
        for i in sorted(
            selected_indices
        )
    ]


# ---------------------------------------------------------
# Manifest matching
# ---------------------------------------------------------

def find_manifest_item(
    path: Path,
    manifest: dict[str, dict[str, Any]],
) -> dict[str, Any]:

    stem = path.stem.lower()

    # 정확한 doc_id
    if stem in manifest:
        return manifest[stem]

    # 파일명이 약간 줄어든 경우 대비
    for doc_id, item in manifest.items():
        if (
            stem in doc_id.lower()
            or doc_id.lower() in stem
        ):
            return item

    return {}


# ---------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------

def yaml_quote(value: Any) -> str:
    if value is None:
        return '""'

    text = str(value).replace(
        '"',
        '\\"',
    )

    return f'"{text}"'


def build_markdown(
    *,
    member: str,
    path: Path,
    title: str,
    paragraphs: list[str],
    metadata: dict[str, Any],
    mode: str,
) -> str:

    config = MEMBER_CONFIG[member]

    doc_id = metadata.get(
        "doc_id",
        path.stem,
    )

    title = metadata.get(
        "title",
        title or path.stem,
    )

    source_url = metadata.get(
        "source_url",
        "",
    )

    year = metadata.get(
        "year",
        "",
    )

    source_type = metadata.get(
        "source_type",
        path.suffix.lstrip("."),
    )

    authority = metadata.get(
        "authority",
        "",
    )

    topics = metadata.get(
        "topics",
        [],
    )

    topic_text = ", ".join(
        yaml_quote(x)
        for x in topics
    )

    frontmatter = f"""---
doc_id: {yaml_quote(doc_id)}
member: {yaml_quote(member)}
member_style: {yaml_quote(config["style"])}
member_style_ko: {yaml_quote(config["style_ko"])}
title: {yaml_quote(title)}
year: {yaml_quote(year)}
source_type: {yaml_quote(source_type)}
authority: {yaml_quote(authority)}
source_url: {yaml_quote(source_url)}
raw_file: {yaml_quote(str(path.relative_to(ROOT)))}
extraction_mode: {yaml_quote(mode)}
topics: [{topic_text}]
---

"""

    body = [
        frontmatter,
        f"# {title}",
        "",
        f"**Investment Lens:** "
        f"{config['style']} "
        f"({config['style_ko']})",
        "",
        "> 이 문서는 Alpha Arena RAG용으로 "
        "원문에서 기계적으로 정제한 자료입니다. "
        "LLM 요약이 아니며 최종 판단 시 원출처를 "
        "우선 기준으로 사용합니다.",
        "",
        "## Source",
        "",
        f"- Member: {member}",
        f"- Source type: {source_type}",
    ]

    if year:
        body.append(
            f"- Year: {year}"
        )

    if source_url:
        body.append(
            f"- URL: {source_url}"
        )

    body.extend(
        [
            "",
            "## Knowledge",
            "",
        ]
    )

    for idx, paragraph in enumerate(
        paragraphs,
        start=1,
    ):
        body.append(
            f"### Passage {idx:03d}"
        )
        body.append("")
        body.append(paragraph)
        body.append("")

    return "\n".join(body)


# ---------------------------------------------------------
# Build
# ---------------------------------------------------------

def process_file(
    path: Path,
    member: str,
    manifest: dict[str, dict[str, Any]],
    mode: str,
) -> dict[str, Any]:

    if path.suffix.lower() in {
        ".html",
        ".htm",
    }:
        title, text = extract_html(path)

    elif path.suffix.lower() == ".pdf":
        title, text = extract_pdf(path)

    else:
        raise ValueError(
            f"Unsupported file: {path}"
        )

    paragraphs = deduplicate(
        split_paragraphs(text)
    )

    original_count = len(paragraphs)

    if mode == "focused":
        selected = select_focused_paragraphs(
            paragraphs,
            member=member,
            min_score=1,
            neighbor_window=1,
            max_paragraphs=60,
        )

        # 키워드 탐색이 너무 적게 잡히면
        # 문서 앞부분을 보완
        if len(selected) < 8:
            selected = paragraphs[:40]

    else:
        selected = paragraphs

    metadata = find_manifest_item(
        path,
        manifest,
    )

    doc_id = metadata.get(
        "doc_id",
        path.stem,
    )

    out_dir = (
        WISDOM_DIR
        / member
    )

    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    out_path = (
        out_dir
        / f"{doc_id}.md"
    )

    markdown = build_markdown(
        member=member,
        path=path,
        title=title,
        paragraphs=selected,
        metadata=metadata,
        mode=mode,
    )

    out_path.write_text(
        markdown,
        encoding="utf-8",
    )

    return {
        "doc_id": doc_id,
        "member": member,
        "source": str(
            path.relative_to(ROOT)
        ),
        "output": str(
            out_path.relative_to(ROOT)
        ),
        "original_paragraphs": original_count,
        "selected_paragraphs": len(selected),
        "source_url": metadata.get(
            "source_url",
            "",
        ),
        "topics": metadata.get(
            "topics",
            [],
        ),
    }


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--member",
        choices=list(
            MEMBER_CONFIG.keys()
        ),
    )

    parser.add_argument(
        "--mode",
        choices=[
            "focused",
            "full",
        ],
        default="focused",
        help=(
            "focused: 투자철학 관련 문단 중심 / "
            "full: 정제한 전체 본문"
        ),
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help="기존 wisdom markdown 삭제 후 재생성",
    )

    args = parser.parse_args()

    if args.clean and WISDOM_DIR.exists():
        for file in WISDOM_DIR.rglob("*.md"):
            file.unlink()

    manifest = load_manifest()

    members = (
        [args.member]
        if args.member
        else list(
            MEMBER_CONFIG.keys()
        )
    )

    index = []

    for member in members:
        source_dir = (
            RAW_DIR
            / member
        )

        if not source_dir.exists():
            print(
                f"[WARN] 없음: "
                f"{source_dir}"
            )
            continue

        print(
            f"\n=== {member.upper()} ==="
        )

        files = sorted(
            [
                p
                for p in source_dir.iterdir()
                if p.suffix.lower()
                in {
                    ".html",
                    ".htm",
                    ".pdf",
                }
            ]
        )

        for path in files:
            try:
                result = process_file(
                    path,
                    member,
                    manifest,
                    args.mode,
                )

                index.append(result)

                print(
                    "[OK] "
                    f"{result['source']} "
                    f"→ {result['output']} "
                    f"({result['selected_paragraphs']}/"
                    f"{result['original_paragraphs']} passages)"
                )

            except Exception as exc:
                print(
                    f"[FAIL] {path}: {exc}"
                )

    WISDOM_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    index_file = (
        WISDOM_DIR
        / "index.json"
    )

    index_file.write_text(
        json.dumps(
            {
                "generated_at": (
                    datetime.now()
                    .isoformat(
                        timespec="seconds"
                    )
                ),
                "mode": args.mode,
                "documents": index,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(
        f"완료: {len(index)} documents"
    )
    print(
        f"Index: "
        f"{index_file.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()