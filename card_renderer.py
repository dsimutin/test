"""Renders flashcard images using Pillow."""
import io
import os
import urllib.request

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# Inter font URLs (GitHub releases — stable)
_FONT_URLS = {
    "bold":        "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
    "regular":     "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
}

# We'll use one variable-font file for all weights
_FONT_FILE = os.path.join(FONTS_DIR, "Inter.ttf")


def _ensure_fonts():
    os.makedirs(FONTS_DIR, exist_ok=True)
    if not os.path.exists(_FONT_FILE):
        try:
            url = "https://github.com/rsms/inter/releases/download/v3.19/Inter-3.19.zip"
            # Fallback: use DejaVu from system (always present on Linux/Railway)
            raise FileNotFoundError("use system font")
        except Exception:
            pass  # will use system or default


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    _ensure_fonts()
    candidates = []
    if bold:
        candidates = [
            _FONT_FILE,
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    else:
        candidates = [
            _FONT_FILE,
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── colour palettes ────────────────────────────────────────────────────────────

WORD_PALETTE = {
    "bg":          "#FFFFFF",
    "accent":      "#4A90D9",
    "accent2":     "#7EC8F0",
    "word":        "#1A1A2E",
    "transcr":     "#8A9BB0",
    "separator":   "#E8ECF0",
    "label_ru":    "#3B7DD8",
    "example":     "#9AA5B4",
    "card_border": "#E2E8F0",
}

VERB_PALETTE = {
    "bg":          "#FFFFFF",
    "accent":      "#E8874A",
    "accent2":     "#F0B060",
    "word":        "#1A1A2E",
    "transcr":     "#8A9BB0",
    "separator":   "#E8ECF0",
    "v2_color":    "#D97A2A",
    "v3_color":    "#3A9A5C",
    "box_bg":      "#F7F9FC",
    "card_border": "#E2E8F0",
}

W, H = 640, 400  # card size


def _draw_card_base(draw: ImageDraw, p: dict, accent_colors: tuple):
    """Draw white card background with top gradient accent bar."""
    # Rounded-rect simulation: white bg
    draw.rectangle([0, 0, W, H], fill=p["bg"])

    # Top accent bar with 2-stop gradient simulation
    bar_h = 8
    c1 = _hex(accent_colors[0])
    c2 = _hex(accent_colors[1])
    for x in range(W):
        t = x / W
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        draw.line([(x, 0), (x, bar_h)], fill=(r, g, b))

    # Subtle border
    draw.rectangle([0, 0, W - 1, H - 1], outline=_hex(p["card_border"]), width=1)


def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _shadow_text(draw, xy, text, font, color, shadow_color="#00000015", offset=2):
    """Draw text with subtle drop shadow."""
    x, y = xy
    draw.text((x + offset, y + offset), text, font=font, fill=shadow_color)
    draw.text((x, y), text, font=font, fill=color)


def render_word_card(english: str, russian: str,
                     transcription: str = "", example: str = "") -> io.BytesIO:
    p  = WORD_PALETTE
    img = Image.new("RGB", (W, H), p["bg"])
    d  = ImageDraw.Draw(img)

    _draw_card_base(d, p, (p["accent"], p["accent2"]))

    pad = 44
    y = 36

    # Emoji + English word
    f_word = _font(46, bold=True)
    d.text((pad, y), "📖  " + english, font=f_word, fill=_hex(p["word"]))
    y += 60

    # Transcription
    if transcription:
        f_tr = _font(20)
        d.text((pad + 4, y), transcription, font=f_tr, fill=_hex(p["transcr"]))
        y += 32

    # Separator
    y += 10
    d.line([(pad, y), (W - pad, y)], fill=_hex(p["separator"]), width=1)
    y += 20

    # Russian translation
    f_ru = _font(34, bold=True)
    d.text((pad, y), russian, font=f_ru, fill=_hex(p["label_ru"]))
    y += 50

    # Example
    if example:
        f_ex = _font(19)
        # Word wrap at ~60 chars
        words = example.split()
        lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= 58:
                cur += (" " if cur else "") + w
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        for line in lines[:2]:
            d.text((pad, y), line, font=f_ex, fill=_hex(p["example"]))
            y += 26

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def render_verb_card(infinitive: str, russian: str,
                     past_simple: str = "", past_participle: str = "") -> io.BytesIO:
    p   = VERB_PALETTE
    img = Image.new("RGB", (W, H), p["bg"])
    d   = ImageDraw.Draw(img)

    _draw_card_base(d, p, (p["accent"], p["accent2"]))

    pad = 44
    y = 36

    # Emoji + Infinitive
    f_word = _font(46, bold=True)
    d.text((pad, y), "⚡  " + infinitive, font=f_word, fill=_hex(p["word"]))
    y += 56

    # Translation
    f_tr = _font(21)
    d.text((pad + 4, y), russian, font=f_tr, fill=_hex(p["transcr"]))
    y += 42

    # Forms box background
    box_y = y
    box_h = 118
    d.rectangle([pad - 4, box_y, W - pad + 4, box_y + box_h],
                fill=_hex(p["box_bg"]))

    y = box_y + 18

    # V2 row
    f_label = _font(13, bold=True)
    f_form  = _font(30, bold=True)

    d.text((pad + 8, y), "PAST SIMPLE", font=f_label, fill=_hex("#AABBCC"))
    y += 20
    ps_text = past_simple or "—"
    d.text((pad + 8, y), ps_text, font=f_form, fill=_hex(p["v2_color"]))
    y += 42

    # V3 row
    d.text((pad + 8, y), "PAST PARTICIPLE", font=f_label, fill=_hex("#AABBCC"))
    y += 20
    pp_text = past_participle or "—"
    d.text((pad + 8, y), pp_text, font=f_form, fill=_hex(p["v3_color"]))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
