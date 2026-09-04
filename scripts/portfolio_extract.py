from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf as fitz


ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = ROOT.parent / "Sahal Portfolio.pdf"
OUT_DIR = ROOT / "static" / "images" / "portfolio"
DATA_PATH = ROOT / "data" / "sahal_projects.json"


SKIP_WORDS = {
    "sahal",
    "branding",
    "agency",
    "portfolio",
    "logo",
    "contact",
    "thank",
    "you",
}


def title_from_text(text: str, page_number: int) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if re.sub(r"\s+", " ", line).strip()
    ]
    candidates: list[str] = []
    for line in lines[:14]:
        clean = re.sub(r"[^A-Za-z0-9&'’+., -]", "", line).strip(" -.,")
        words = clean.lower().split()
        if not clean or len(clean) < 3:
            continue
        if len(clean) > 64:
            continue
        if all(word in SKIP_WORDS for word in words):
            continue
        candidates.append(clean)
    return candidates[0] if candidates else f"Portfolio Project {page_number:02d}"


def category_from_title(title: str) -> str:
    lower = title.lower()
    if any(word in lower for word in ("event", "launch", "expo", "show")):
        return "Event Branding"
    if any(word in lower for word in ("sign", "banner", "billboard", "rollup")):
        return "Signage"
    if any(word in lower for word in ("package", "label", "box")):
        return "Packaging"
    if any(word in lower for word in ("print", "flyer", "brochure", "card", "poster")):
        return "Print"
    return "Brand Identity"


def render_projects() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(PDF_PATH)
    projects = []

    for index, page in enumerate(doc, start=1):
        text = page.get_text("text")
        title = title_from_text(text, index)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or f"project-{index:02d}"
        filename = f"{index:02d}-{slug[:52]}.webp"
        image_path = OUT_DIR / filename

        pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
        pix.pil_save(image_path, format="WEBP", quality=82, method=6)

        projects.append(
            {
                "id": index,
                "title": title,
                "category": category_from_title(title),
                "image": f"images/portfolio/{filename}",
                "summary": "Portfolio work from the Sahal brand archive.",
            }
        )

    DATA_PATH.write_text(json.dumps(projects, indent=2), encoding="utf-8")
    print(f"Rendered {len(projects)} project pages to {OUT_DIR}")
    print(f"Wrote {DATA_PATH}")


def inspect_pdf() -> None:
    doc = fitz.open(PDF_PATH)
    print(f"PDF: {PDF_PATH}")
    print(f"Pages: {doc.page_count}")
    for index in range(min(doc.page_count, 12)):
        text = doc[index].get_text("text")
        print(f"--- PAGE {index + 1} ---")
        print(text[:1000].strip())


if __name__ == "__main__":
    inspect_pdf()
