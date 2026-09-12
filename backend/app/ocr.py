"""
OCR date scanner — the second half of intake.

The UPC scanner answers "what is this?"; this module answers "how old is
this one?" Those are different questions and only the second one can go
wrong in a way that hurts someone, which is why nothing here writes to
inventory. It reads a label and proposes a date. A person accepts it.

What it does, in order:

    image ─▶ Google Cloud Vision  ─▶ full text + per-word confidence
          ─▶ find date-shaped substrings
          ─▶ parse each into a real date, with the label keyword beside it
             ("SELL BY", "USE BY", "EXP") to say what the date means
          ─▶ score each candidate by the *minimum* confidence of the words
             that formed it, not the page mean
          ─▶ return them ranked

That minimum-confidence rule is FR-5.8. A page mean hides the failure that
matters: a label can be 98% legible overall and still have the one digit
of the year misread, and the mean will not notice. The date is scored on
its own tokens or it is not scored usefully at all.

Setup:
    1. Create a GCP project, enable the Vision API.
    2. Create a service account key, download the JSON.
    3. Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json in your .env
       (or export it in your shell).
    4. pip install google-cloud-vision  (already in requirements.txt)

Run it directly to sanity-check against a sample photo:
    python -m app.ocr path/to/label.jpg
"""
import calendar
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

from .models import DateLabelType

load_dotenv()

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Keywords printed next to the date, mapped to what the date means. Order
# matters on lookup: the longest match wins, so "BEST BY" is not read as
# the "BB" abbreviation and "SELL BY" is not swallowed by a bare "BY".
LABEL_KEYWORDS: list[tuple[str, DateLabelType]] = [
    ("sell by", DateLabelType.SELL_BY),
    ("sell-by", DateLabelType.SELL_BY),
    ("display until", DateLabelType.SELL_BY),
    ("use by", DateLabelType.USE_BY),
    ("use-by", DateLabelType.USE_BY),
    ("use before", DateLabelType.USE_BY),
    ("expires", DateLabelType.USE_BY),
    ("expiration", DateLabelType.USE_BY),
    ("exp", DateLabelType.USE_BY),
    ("best by", DateLabelType.BEST_BY),
    ("best before", DateLabelType.BEST_BY),
    ("best if used by", DateLabelType.BEST_BY),
    ("bb", DateLabelType.BEST_BY),
    ("packed on", DateLabelType.PACKED_ON),
    ("pkd", DateLabelType.PACKED_ON),
    ("manufactured", DateLabelType.PACKED_ON),
]

# How far back to look from a date for the keyword describing it. Labels
# print "SELL BY 03/14/27" tight together; anything further away than this
# is probably a different field entirely.
KEYWORD_LOOKBEHIND_CHARS = 24

# Date shapes seen on grocery packaging. Each alternative is named so the
# parser knows how to read the groups rather than guessing at ordering.
DATE_PATTERN = re.compile(
    r"(?P<numeric>\b\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}\b)"
    r"|(?P<monthfirst>\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\.?\s*\d{1,2},?\s*\d{2,4}\b)"
    r"|(?P<dayfirst>\b\d{1,2}\s*(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\.?\s*\d{2,4}\b)"
    r"|(?P<monthyear>\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\.?\s*\d{4}\b)",
    re.IGNORECASE,
)


@dataclass
class DateCandidate:
    """One date-shaped thing found on the label, parsed and scored."""
    text: str                    # the substring exactly as printed
    date: datetime               # what it parses to, at UTC midnight
    label_type: DateLabelType    # from the keyword beside it
    confidence: float            # MINIMUM word confidence over its tokens

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "date": self.date.isoformat(),
            "label_type": self.label_type.value,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class OCRResult:
    raw_text: str
    confidence: float                  # mean word confidence across the page
    candidates: list[DateCandidate] = field(default_factory=list)
    error: Optional[str] = None        # set when Vision failed; see FR-5.7

    @property
    def best(self) -> Optional[DateCandidate]:
        """
        The candidate to propose on the confirmation screen.

        Ranked by the specificity of what the label says about it, then by
        how clearly it was read. A date printed under "USE BY" outranks a
        bare date elsewhere on the package, because a bare date on food
        packaging is as often a batch code or a promotion end as it is the
        date we want.
        """
        if not self.candidates:
            return None
        priority = {
            DateLabelType.USE_BY: 0,
            DateLabelType.SELL_BY: 1,
            DateLabelType.BEST_BY: 2,
            DateLabelType.PACKED_ON: 4,
            DateLabelType.UNKNOWN: 3,
        }
        return min(self.candidates, key=lambda c: (priority[c.label_type], -c.confidence))

    @property
    def ambiguous(self) -> bool:
        """
        True when the label offered more than one distinct date.

        UC-08 alternate flow 5b leaves selection among multiple candidates
        unspecified and says such an item should go to review. This is the
        flag that enforces it — the confirmation screen shows every
        candidate and makes the person choose.
        """
        return len({c.date.date() for c in self.candidates}) > 1


# ---------- Parsing ----------

def _resolve_year(year: int, now: datetime) -> int:
    """
    Expand a two-digit year to the century that puts it nearest to today.

    Food on a shelf is dated within a couple of years either way, so
    "nearest" resolves "27" to 2027 without the cliff a fixed pivot year
    has. A year that lands far in the past under every century — "00" —
    stays far in the past, which is what sends it to a person instead of
    quietly becoming a plausible future date.
    """
    if year >= 100:
        return year
    base = now.year - now.year % 100
    return min(
        (base - 100 + year, base + year, base + 100 + year),
        key=lambda candidate: abs(candidate - now.year),
    )


def _safe_date(year: int, month: int, day: int) -> Optional[datetime]:
    """Build a date, or None if the numbers don't describe one (2/30, 13/1)."""
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _parse_numeric(text: str, now: datetime) -> Optional[datetime]:
    """
    Read a 3-part numeric date.

    US packaging is overwhelmingly month-first, so that is tried first, and
    day-first is used only when the first number cannot be a month. An
    unresolvable case like 03/04/27 is returned as March 4th — and this is
    exactly why the confirmation step exists rather than being an optional
    extra: the machine cannot tell, and a person holding the package can.
    """
    parts = [int(p) for p in re.split(r"[/\-.]", text) if p]
    if len(parts) != 3:
        return None

    a, b, c = parts
    # A 4-digit leading number is a year: 2027-03-14 (ISO).
    if a > 31:
        return _safe_date(_resolve_year(a, now), b, c)

    year = _resolve_year(c, now)
    return _safe_date(year, a, b) or _safe_date(year, b, a)


def _parse_month_name(text: str, now: datetime) -> Optional[datetime]:
    """Read 'MAR 14 2027', '14 MAR 2027', or a month/year-only 'MAR 2027'."""
    month_match = re.search(r"[A-Za-z]{3,9}", text)
    if not month_match:
        return None
    month = MONTHS.get(month_match.group(0)[:4].lower()) or MONTHS.get(
        month_match.group(0)[:3].lower()
    )
    if not month:
        return None

    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not numbers:
        return None

    if len(numbers) == 1:
        # Month and year only — common on shelf-stable goods. The whole
        # month is good, so the last day of it is the honest reading.
        year = _resolve_year(numbers[0], now)
        return _safe_date(year, month, calendar.monthrange(year, month)[1])

    day, year = numbers[0], _resolve_year(numbers[1], now)
    return _safe_date(year, month, day)


def _label_type_for(text: str, start: int) -> DateLabelType:
    """Find the keyword printed just before a date and read its meaning."""
    window = text[max(0, start - KEYWORD_LOOKBEHIND_CHARS):start].lower()
    best_position, best_length, best_type = -1, 0, DateLabelType.UNKNOWN
    for keyword, label_type in LABEL_KEYWORDS:
        position = window.rfind(keyword)
        if position < 0:
            continue
        # The keyword closest to the date wins; a tie goes to the longer,
        # more specific one, so "EXPIRES" is not read as the "EXP" prefix.
        if position > best_position or (position == best_position and len(keyword) > best_length):
            best_position, best_length, best_type = position, len(keyword), label_type
    return best_type


def parse_dates(
    text: str,
    word_confidences: Optional[list[tuple[str, float]]] = None,
    now: Optional[datetime] = None,
) -> list[DateCandidate]:
    """
    Pull every date off a block of label text.

    `word_confidences` is Vision's per-word scoring as (word, confidence)
    pairs. When supplied, each candidate is scored by the lowest confidence
    among the words overlapping it (FR-5.8). Without it — a manually pasted
    label, or a test — candidates come back at 0.0 and the caller should
    treat them as unscored rather than as certainly wrong.
    """
    now = now or datetime.utcnow()
    candidates: list[DateCandidate] = []

    for match in DATE_PATTERN.finditer(text):
        raw = match.group(0).strip()
        if match.lastgroup == "numeric":
            parsed = _parse_numeric(raw, now)
        else:
            parsed = _parse_month_name(raw, now)
        if parsed is None:
            continue

        candidates.append(
            DateCandidate(
                text=raw,
                date=parsed,
                label_type=_label_type_for(text, match.start()),
                confidence=_score_span(raw, word_confidences),
            )
        )

    return candidates


def _score_span(span_text: str, word_confidences: Optional[list[tuple[str, float]]]) -> float:
    """
    The minimum confidence across the words making up a date.

    Vision reports words, not character offsets we can align to the regex
    match, so the words are matched by content: any word whose text appears
    in the date substring is part of it. Crude, and it can pull in a stray
    token that happens to share digits, but it errs toward a *lower* score,
    which sends the item to a person. That is the right direction to err.
    """
    if not word_confidences:
        return 0.0
    normalized = re.sub(r"[^0-9A-Za-z]", "", span_text).lower()
    scores = [
        confidence
        for word, confidence in word_confidences
        if (clean := re.sub(r"[^0-9A-Za-z]", "", word).lower()) and clean in normalized
    ]
    return min(scores) if scores else 0.0


# ---------- Vision ----------

def extract_text_from_image(image_path: str) -> OCRResult:
    """
    Read a label file from disk. Thin wrapper over extract_text_from_bytes.
    """
    with open(image_path, "rb") as f:
        return extract_text_from_bytes(f.read())


def extract_text_from_bytes(content: bytes) -> OCRResult:
    """
    Call Vision's document_text_detection and return text, a page-mean
    confidence, and the parsed date candidates.

    FR-5.7 / NFR-4.4.3: this never raises. A missing credential, an
    unreachable API, a quota error — all come back as an OCRResult with
    `error` set and no candidates, so the intake screen degrades to "type
    the date in yourself" instead of stopping the person unloading a
    pallet. An outage at the Vision API is not a reason to stop receiving
    food.
    """
    try:
        from google.cloud import vision  # imported lazily so the rest of the
        # app doesn't need GCP credentials configured just to import this file

        client = vision.ImageAnnotatorClient()
        response = client.document_text_detection(image=vision.Image(content=content))

        if response.error.message:
            return OCRResult(raw_text="", confidence=0.0, error=f"Vision API error: {response.error.message}")
    except Exception as exc:  # noqa: BLE001 — see the docstring: never raise
        return OCRResult(raw_text="", confidence=0.0, error=f"OCR unavailable: {exc}")

    annotation = response.full_text_annotation
    full_text = annotation.text if annotation else ""

    # Flatten Vision's page/block/paragraph/word tree into (word, score).
    words: list[tuple[str, float]] = []
    if annotation:
        for page in annotation.pages:
            for block in page.blocks:
                for paragraph in block.paragraphs:
                    for word in paragraph.words:
                        text = "".join(symbol.text for symbol in word.symbols)
                        words.append((text, word.confidence))

    mean_confidence = sum(c for _, c in words) / len(words) if words else 0.0

    return OCRResult(
        raw_text=full_text,
        confidence=mean_confidence,
        candidates=parse_dates(full_text, words),
    )


def cross_check_sku(detected_text: str, known_skus: dict[str, str]) -> Optional[str]:
    """
    Legacy free-text SKU matcher, kept for the pre-scanner code path.

    The UPC scanner supersedes this (FR-5.9): a barcode with a valid check
    digit identifies a product, where a substring match against OCR text
    identifies whatever happens to share a few characters. Prefer
    app/upc.py:resolve for anything new.

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
    if result.error:
        print(f"!! {result.error}")
    print("---- Raw text ----")
    print(result.raw_text)
    print(f"\n---- Page mean confidence ----\n{result.confidence:.2%}")
    print("\n---- Date candidates ----")
    for candidate in result.candidates:
        print(
            f"  {candidate.text!r:24} -> {candidate.date.date()}  "
            f"[{candidate.label_type.value}]  min-confidence {candidate.confidence:.2%}"
        )
    if not result.candidates:
        print("  (none found — check DATE_PATTERN against this label's format)")
    elif result.best:
        print(f"\nProposing: {result.best.date.date()} ({result.best.label_type.value})")
        if result.ambiguous:
            print("  ⚠ multiple distinct dates on this label — a person must choose.")
