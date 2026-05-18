"""
Premium flashcard image renderer.
Uses only RGB (no RGBA tricks) — fully reliable on all Pillow versions.
Card: 760 × 1000 px, white bg, accent top stripe, rounded via mask.
"""
import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CW, CH   = 760, 1000
RADIUS   = 36
PAD      = 52
LINE_CLR = (220, 225, 235)   # separator colour

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_REGULAR  = os.path.join(FONTS_DIR, "NotoSans-Regular.ttf")
_BOLD     = os.path.join(FONTS_DIR, "NotoSans-Bold.ttf")


# ── colour helpers ─────────────────────────────────────────────────────────────

def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _tint(accent: str, alpha: float) -> tuple:
    """Blend accent colour with white at given opacity (0-1)."""
    r, g, b = _hex(accent)
    return (
        int(255 * (1 - alpha) + r * alpha),
        int(255 * (1 - alpha) + g * alpha),
        int(255 * (1 - alpha) + b * alpha),
    )


# ── font helpers ───────────────────────────────────────────────────────────────

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _BOLD if bold else _REGULAR
    if os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _tw(text: str, font) -> int:
    bb = font.getbbox(text)
    return bb[2] - bb[0]


def _th(font) -> int:
    bb = font.getbbox("Ag")
    return bb[3] - bb[1]


def _wrap(text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if _tw(test, font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ── base image with rounded corners ───────────────────────────────────────────

def _make_card() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """White RGB card with subtle rounded-corner mask."""
    img  = Image.new("RGB", (CW, CH), (255, 255, 255))
    mask = Image.new("L",   (CW, CH), 0)
    md   = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, CW - 1, CH - 1], radius=RADIUS, fill=255)
    # apply mask (white stays, corners go white on white — fine for Telegram)
    bg   = Image.new("RGB", (CW, CH), (246, 244, 239))   # page bg
    bg.paste(img, mask=mask)
    draw = ImageDraw.Draw(bg)
    # white card
    draw.rounded_rectangle([0, 0, CW - 1, CH - 1], radius=RADIUS,
                            fill=(255, 255, 255))
    return bg, draw


# ── shared drawing primitives ──────────────────────────────────────────────────

def _accent_stripe(draw: ImageDraw, accent: str):
    r, g, b = _hex(accent)
    draw.rounded_rectangle([0, 0, CW - 1, RADIUS + 6],
                            radius=RADIUS, fill=(r, g, b))
    draw.rectangle([0, RADIUS, CW - 1, RADIUS + 6], fill=(r, g, b))


def _pill(draw: ImageDraw, x: int, y: int, text: str,
          font, accent: str, right: bool = False) -> int:
    """Draw a rounded pill badge. Returns pill width."""
    r, g, b = _hex(accent)
    h = 40
    tw_ = _tw(text, font)
    hp  = 18
    w   = tw_ + hp * 2
    if right:
        x = x - w
    draw.rounded_rectangle([x, y, x + w, y + h],
                            radius=12, fill=_tint(accent, 0.10))
    draw.text((x + hp, y + 8), text, font=font, fill=(r, g, b))
    return w


def _separator(draw: ImageDraw, y: int):
    draw.rectangle([PAD, y, CW - PAD, y + 1], fill=LINE_CLR)


def _label_row(draw: ImageDraw, label: str, number: str, accent: str, y: int):
    f = _font(20, bold=True)
    _pill(draw, PAD, y, label, f, accent)
    _pill(draw, CW - PAD, y, number, f, accent, right=True)


def _example_block(draw: ImageDraw, example: str,
                   highlight: str, accent: str, y: int) -> int:
    """Tinted box with highlighted word. Returns bottom y."""
    f_reg  = _font(27)
    f_bold = _font(27, bold=True)
    box_w  = CW - PAD * 2
    inner  = box_w - 52
    lines  = _wrap(example, f_reg, inner)
    lh     = 40
    box_h  = len(lines) * lh + 48

    draw.rounded_rectangle([PAD, y, PAD + box_w, y + box_h],
                            radius=20, fill=_tint(accent, 0.07))

    # quotation mark
    f_q = _font(54, bold=True)
    draw.text((PAD + 18, y + 2), "“", font=f_q,
              fill=_tint(accent, 0.25))

    ty = y + 34
    hl = highlight.lower()
    r, g, b = _hex(accent)

    for line in lines:
        lo  = line.lower()
        idx = lo.find(hl)
        if idx == -1:
            cx = PAD + (box_w - _tw(line, f_reg)) // 2
            draw.text((cx, ty), line, font=f_reg, fill=(71, 85, 105))
        else:
            before = line[:idx]
            mid    = line[idx:idx + len(highlight)]
            after  = line[idx + len(highlight):]
            total  = (_tw(before, f_reg) +
                      _tw(mid,    f_bold) +
                      _tw(after,  f_reg))
            cx = PAD + (box_w - total) // 2
            if before:
                draw.text((cx, ty), before, font=f_reg,  fill=(71, 85, 105))
                cx += _tw(before, f_reg)
            draw.text((cx, ty), mid,    font=f_bold, fill=(r, g, b))
            cx += _tw(mid, f_bold)
            if after:
                draw.text((cx, ty), after,  font=f_reg,  fill=(71, 85, 105))
        ty += lh

    return y + box_h


def _to_bytes(img: Image.Image) -> io.BytesIO:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ── Public: Vocabulary card ────────────────────────────────────────────────────

def render_word_card(english: str, russian: str,
                     transcription: str = "",
                     example: str = "",
                     number: str = "—",
                     accent: str = "#2F7D5B") -> io.BytesIO:

    img, d = _make_card()
    r, g, b = _hex(accent)

    _accent_stripe(d, accent)

    y = 24
    _label_row(d, "VOCABULARY", number, accent, y)
    y += 72

    # Word
    f_word = _font(72, bold=True)
    d.text((PAD, y), english, font=f_word, fill=(10, 15, 30))
    y += _th(f_word) + 14

    # Accent underline
    d.rectangle([PAD, y, CW - PAD, y + 3], fill=_tint(accent, 0.40))
    y += 22

    # Transcription
    if transcription:
        f_tr = _font(32)
        d.text((PAD, y), transcription, font=f_tr, fill=(r, g, b))
        y += _th(f_tr) + 20

    y += 8
    _separator(d, y)
    y += 30

    # Translation (no emoji — PIL can't render flags)
    f_ru_lbl = _font(18, bold=True)
    d.text((PAD, y), "ПЕРЕВОД", font=f_ru_lbl, fill=_tint(accent, 0.55))
    y += 28

    f_ru = _font(40, bold=True)
    for line in _wrap(russian, f_ru, CW - PAD * 2)[:2]:
        d.text((PAD, y), line, font=f_ru, fill=(15, 25, 50))
        y += _th(f_ru) + 6
    y += 18

    _separator(d, y)
    y += 30

    # Example
    if example:
        _example_block(d, example, english, accent, y)

    return _to_bytes(img)


# ── Public: Irregular verb card ────────────────────────────────────────────────

def render_verb_card(infinitive: str, russian: str,
                     past_simple: str = "",
                     past_participle: str = "",
                     number: str = "—",
                     accent: str = "#D97A2A",
                     example: str = "") -> io.BytesIO:

    img, d = _make_card()
    r, g, b = _hex(accent)

    _accent_stripe(d, accent)

    y = 24
    _label_row(d, "IRREGULAR VERB", number, accent, y)
    y += 72

    # Three form boxes
    forms = [
        (infinitive,               "INFINITIVE",       False),
        (past_simple or "—",       "PAST SIMPLE",      True),
        (past_participle or "—",   "PAST PARTICIPLE",  False),
    ]
    col_gap = 16
    col_w   = (CW - PAD * 2 - col_gap * 2) // 3
    box_h   = 140
    f_form  = _font(42, bold=True)
    f_lbl   = _font(17, bold=True)

    for i, (form, lbl, is_accent) in enumerate(forms):
        cx = PAD + i * (col_w + col_gap)
        box_fill = _tint(accent, 0.10) if is_accent else (248, 249, 252)
        d.rounded_rectangle([cx, y, cx + col_w, y + box_h],
                             radius=18, fill=box_fill)
        fw = _tw(form, f_form)
        fc = (r, g, b) if is_accent else (10, 15, 30)
        d.text((cx + (col_w - fw) // 2, y + 26), form, font=f_form, fill=fc)
        lw = _tw(lbl, f_lbl)
        d.text((cx + (col_w - lw) // 2, y + 96), lbl,
               font=f_lbl, fill=(160, 170, 185))

    y += box_h + 24

    # Dots connector
    centres = [PAD + i * (col_w + col_gap) + col_w // 2 for i in range(3)]
    for i, cx in enumerate(centres):
        d.ellipse([cx - 7, y - 7, cx + 7, y + 7], fill=(r, g, b))
        if i < 2:
            nx = centres[i + 1] - 7
            d.rectangle([cx + 8, y - 1, nx, y + 1], fill=LINE_CLR)
    y += 32

    _separator(d, y)
    y += 30

    # Translation
    f_ru_lbl = _font(18, bold=True)
    d.text((PAD, y), "ПЕРЕВОД", font=f_ru_lbl, fill=_tint(accent, 0.55))
    y += 28

    f_ru = _font(36, bold=True)
    for line in _wrap(russian, f_ru, CW - PAD * 2)[:2]:
        d.text((PAD, y), line, font=f_ru, fill=(15, 25, 50))
        y += _th(f_ru) + 6
    y += 18

    _separator(d, y)
    y += 30

    # Example
    hl = past_simple or infinitive
    if example:
        _example_block(d, example, hl, accent, y)

    return _to_bytes(img)
