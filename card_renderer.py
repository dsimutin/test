"""Renders flashcard images using Pillow."""
import io
import logging
import os
import urllib.request

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_REGULAR = os.path.join(FONTS_DIR, "NotoSans-Regular.ttf")
_BOLD    = os.path.join(FONTS_DIR, "NotoSans-Bold.ttf")

_NOTO_REGULAR_URL = (
    "https://github.com/googlefonts/noto-fonts/raw/main/"
    "hinted/ttf/NotoSans/NotoSans-Regular.ttf"
)
_NOTO_BOLD_URL = (
    "https://github.com/googlefonts/noto-fonts/raw/main/"
    "hinted/ttf/NotoSans/NotoSans-Bold.ttf"
)


def _ensure_fonts():
    os.makedirs(FONTS_DIR, exist_ok=True)
    for path, url in [(_REGULAR, _NOTO_REGULAR_URL), (_BOLD, _NOTO_BOLD_URL)]:
        if not os.path.exists(path):
            logger.info("Downloading font: %s", url)
            try:
                urllib.request.urlretrieve(url, path)
            except Exception as e:
                logger.warning("Font download failed: %s", e)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    _ensure_fonts()
    path = _BOLD if bold else _REGULAR
    if os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _gradient_bar(draw: ImageDraw, width: int, c1: str, c2: str, height: int = 6):
    h1, h2 = _hex(c1), _hex(c2)
    for x in range(width):
        t = x / width
        r = int(h1[0] + (h2[0] - h1[0]) * t)
        g = int(h1[1] + (h2[1] - h1[1]) * t)
        b = int(h1[2] + (h2[2] - h1[2]) * t)
        draw.line([(x, 0), (x, height - 1)], fill=(r, g, b))


def _wrap(text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if font.getlength(test) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ── Word card ──────────────────────────────────────────────────────────────────

def render_word_card(english: str, russian: str,
                     transcription: str = "", example: str = "") -> io.BytesIO:
    W, PAD = 640, 44

    # Measure content height
    f_tag   = _font(11, bold=True)
    f_word  = _font(48, bold=True)
    f_tr    = _font(19)
    f_ru    = _font(32, bold=True)
    f_ex    = _font(18)

    ex_lines = _wrap(example, f_ex, W - PAD * 2) if example else []
    ex_lines = ex_lines[:2]

    H = 40 + 8 + 16 + 56 + (28 if transcription else 0) + 16 + 1 + 20 + 44 + (len(ex_lines) * 26) + 36
    H = max(H, 320)

    img = Image.new("RGB", (W, H), "#FFFFFF")
    d   = ImageDraw.Draw(img)

    # Gradient top bar
    _gradient_bar(d, W, "#4A90D9", "#7EC8F0", height=6)

    # Border
    d.rectangle([0, 0, W - 1, H - 1], outline=_hex("#E2E8F0"), width=1)

    y = 30

    # Tag "СЛОВО"
    d.text((PAD, y), "СЛОВО", font=f_tag, fill=_hex("#A0B4CC"))
    y += 20

    # English word
    d.text((PAD, y), english, font=f_word, fill=_hex("#1A1A2E"))
    y += 58

    # Transcription
    if transcription:
        d.text((PAD, y), transcription, font=f_tr, fill=_hex("#8A9BB0"))
        y += 28

    # Separator
    y += 12
    d.line([(PAD, y), (W - PAD, y)], fill=_hex("#EDF0F4"), width=1)
    y += 18

    # Russian
    d.text((PAD, y), russian, font=f_ru, fill=_hex("#3B7DD8"))
    y += 46

    # Example
    for line in ex_lines:
        d.text((PAD, y), line, font=f_ex, fill=_hex("#9AA5B4"))
        y += 26

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ── Verb card ──────────────────────────────────────────────────────────────────

def render_verb_card(infinitive: str, russian: str,
                     past_simple: str = "", past_participle: str = "") -> io.BytesIO:
    W, PAD = 640, 44

    f_tag   = _font(11, bold=True)
    f_word  = _font(48, bold=True)
    f_ru    = _font(19)
    f_label = _font(11, bold=True)
    f_form  = _font(30, bold=True)

    H = 30 + 20 + 58 + 28 + 20 + 120 + 36
    H = max(H, 340)

    img = Image.new("RGB", (W, H), "#FFFFFF")
    d   = ImageDraw.Draw(img)

    # Gradient top bar (orange)
    _gradient_bar(d, W, "#E8874A", "#F0B878", height=6)

    # Border
    d.rectangle([0, 0, W - 1, H - 1], outline=_hex("#E2E8F0"), width=1)

    y = 30

    # Tag
    d.text((PAD, y), "НЕПРАВИЛЬНЫЙ ГЛАГОЛ", font=f_tag, fill=_hex("#C0A080"))
    y += 20

    # Infinitive
    d.text((PAD, y), infinitive, font=f_word, fill=_hex("#1A1A2E"))
    y += 58

    # Translation
    d.text((PAD, y), russian, font=f_ru, fill=_hex("#8A9BB0"))
    y += 28

    # Forms box
    y += 16
    box_h = 108
    d.rectangle([PAD - 8, y, W - PAD + 8, y + box_h], fill=_hex("#F7F9FC"))
    d.rectangle([PAD - 8, y, W - PAD + 8, y + box_h], outline=_hex("#E8ECF2"), width=1)

    by = y + 14

    # V2
    d.text((PAD + 4, by), "PAST SIMPLE", font=f_label, fill=_hex("#B0C0D0"))
    by += 16
    d.text((PAD + 4, by), past_simple or "—", font=f_form, fill=_hex("#D97A2A"))
    by += 40

    # V3
    d.text((PAD + 4, by), "PAST PARTICIPLE", font=f_label, fill=_hex("#B0C0D0"))
    by += 16
    d.text((PAD + 4, by), past_participle or "—", font=f_form, fill=_hex("#3A9A5C"))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
