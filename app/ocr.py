"""
OCR pipeline using Google Cloud Vision.

Week 1 goal (per the build checklist): get ONE OCR call working on a
sample product label. This module does exactly that, plus a very rough
date-candidate extractor so there's something to look at.

Week 2 will build on this: SKU cross-check against the DB and the
confidence branch already live in app/crud.py (apply_ocr_result) — this
module's job is just "image in, (text, confidence) out."

Setup:
    1. Create a GCP project, enable the Vision API.
    2. Create a service account key, download the JSON.
    3. Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json in your .env
       (or export it in your shell).
    4. pip install google-cloud-vision  (already in requirements.txt)

Run it directly to sanity-check against a sample photo:
    python -m app.ocr path/to/label.jpg
"""
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Simple heuristic for pulling date-like substrings out of label text.
# Real labels vary a lot (MM/DD/YY, "BEST BY MAR 14 2027", julian codes,
# etc.) — treat this as a starting point to refine once you have real
# label photos in Week 3's error-rate logging.
DATE_PATTERN = re.compile(
    r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})|"
    r"([A-Z]{3,9}\.?\s+\d{1,2},?\s+\d{2,4})",
    re.IGNORECASE,
)


@dataclass
class OCRResult:
    raw_text: str
    confidence: float          # 0.0 - 1.0, Vision's mean word-level confidence
    date_candidates: list[str]


def extract_text_from_image(image_path: str) -> OCRResult:
    """
    Calls Google Cloud Vision's document_text_detection and returns the
    full text plus an average confidence score.

    Vision doesn't return one overall "confidence" the way this function
    implies — it scores per detected symbol/word. We average those to get
    a single number to compare against CONFIDENCE_THRESHOLD in crud.py.
    Revisit this averaging approach once you have real accuracy numbers;
    a low mean can hide one badly-misread but critical token (the date).
    """
    from google.cloud import vision  # imported lazily so the rest of the
    # app doesn't need GCP credentials configured just to import this file

    client = vision.ImageAnnotatorClient()

    with open(image_path, "rb") as f:
        content = f.read()

    image = vision.Image(content=content)
    response = client.document_text_detection(image=image)

    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")

    full_text = response.full_text_annotation.text if response.full_text_annotation else ""

    # Average word-level confidence across all pages/blocks/paragraphs/words
    confidences = []
    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    confidences.append(word.confidence)

    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    date_candidates = [m.group(0) for m in DATE_PATTERN.finditer(full_text)]

    return OCRResult(raw_text=full_text, confidence=avg_confidence, date_candidates=date_candidates)


def cross_check_sku(detected_text: str, known_skus: dict[str, str]) -> Optional[str]:
    """
    Very rough SKU matcher: looks for any known SKU code as a literal
    substring of the OCR'd text. Replace with a real barcode read
    (Vision also does barcode/UPC detection) once you're past the
    single-camera prototype stage — free text matching on a barcode
    number is fragile.

    known_skus: {sku_code: product_name} pulled from the store's inventory DB.
    """
    for sku, _name in known_skus.items():
        if sku in detected_text:
            return sku
    return None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.ocr path/to/label.jpg")
        sys.exit(1)

    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        print("Warning: GOOGLE_APPLICATION_CREDENTIALS is not set — the Vision call will fail.")
        print("See the docstring at the top of this file for setup steps.\n")

    result = extract_text_from_image(sys.argv[1])
    print("---- Raw text ----")
    print(result.raw_text)
    print("\n---- Confidence ----")
    print(f"{result.confidence:.2%}")
    print("\n---- Date candidates ----")
    print(result.date_candidates or "(none found — check DATE_PATTERN against this label's format)")
