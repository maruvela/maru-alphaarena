from pathlib import Path
import requests
import time

SOURCES = {
    "buffett": [
        (
            "buffett_1983.html",
            "https://www.berkshirehathaway.com/letters/1983.html",
        ),
        (
            "buffett_1991.html",
            "https://www.berkshirehathaway.com/letters/1991.html",
        ),
        (
            "buffett_1992.html",
            "https://www.berkshirehathaway.com/letters/1992.html",
        ),
        (
            "buffett_2007.pdf",
            "https://www.berkshirehathaway.com/letters/2007ltr.pdf",
        ),
    ],

    "lynch": [
        (
            "lynch_pbs_frontline.html",
            "https://www.pbs.org/wgbh/pages/frontline/shows/betting/pros/lynch.html",
        ),
        (
            "lynch_mpr_1993.html",
            "https://archive.mpr.org/stories/1993/05/26/"
            "peter-lynch-on-investing-strategies-and-misconceptions",
        ),
    ],

    "marks": [
        (
            "marks_indispensability_of_risk.html",
            "https://www.oaktreecapital.com/insights/memo/"
            "the-indispensability-of-risk",
        ),
        (
            "marks_i_beg_to_differ.html",
            "https://www.oaktreecapital.com/insights/memo/i-beg-to-differ",
        ),
        (
            "marks_taking_the_temperature.html",
            "https://www.oaktreecapital.com/insights/memo/taking-the-temperature",
        ),
        (
            "marks_illusion_of_knowledge.html",
            "https://www.oaktreecapital.com/insights/memo/"
            "the-illusion-of-knowledge",
        ),
    ],

    "damodaran": [
        (
            "damodaran_intro_valuation.html",
            "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/"
            "background/valintro.htm",
        ),
        (
            "damodaran_valuation_lectures.html",
            "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/eqlect.htm",
        ),
        (
            "damodaran_growth_investing.html",
            "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/"
            "invphillectures/growth.html",
        ),
    ],
}

ROOT = Path("data/raw")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 Alpha-Arena educational RAG collector"
    )
}


def download(url: str, path: Path):
    if path.exists():
        print(f"[SKIP] {path}")
        return

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()

    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".pdf":
        path.write_bytes(response.content)
    else:
        response.encoding = response.apparent_encoding
        path.write_text(response.text, encoding="utf-8")

    print(f"[OK]   {path}")


def main():
    total = sum(len(v) for v in SOURCES.values())
    current = 0

    for member, sources in SOURCES.items():
        print(f"\n=== {member.upper()} ===")

        for filename, url in sources:
            current += 1
            print(f"[{current}/{total}] {url}")

            try:
                download(
                    url,
                    ROOT / member / filename,
                )
            except Exception as e:
                print(f"[FAIL] {filename}: {e}")

            time.sleep(0.5)


if __name__ == "__main__":
    main()