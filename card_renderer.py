"""
Renders flashcards with Pillow. Draws directly from structured data.
"""
import io
import os
from PIL import Image, ImageDraw, ImageFont

ACCENT_PALETTE = [
    "#2F7D5B",  # green
    "#2F6FD6",  # blue
    "#7A4FB3",  # purple
    "#C44B6A",  # rose
    "#D97A2A",  # orange
    "#1A7A8A",  # teal
]

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# Card dimensions
CW = 600   # card width
PAD = 44   # horizontal padding inside card
INNER = CW - PAD * 2


def pick_accent(word_id: int) -> str:
    return ACCENT_PALETTE[word_id % len(ACCENT_PALETTE)]


def _hex(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _tint(rgb, a: float):
    r, g, b = rgb
    return (
        int(255 * (1 - a) + r * a),
        int(255 * (1 - a) + g * a),
        int(255 * (1 - a) + b * a),
    )


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except Exception:
        return ImageFont.load_default()


def _text_w(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _wrapped_lines(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if _text_w(draw, trial, font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _line_h(font):
    bb = ImageFont.FreeTypeFont.getbbox(font, "Ag")
    return bb[3] - bb[1]


# ── shared card chrome ─────────────────────────────────────────────────────────

def _base_image(height: int, accent_rgb) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    BG = (240, 237, 232)
    img = Image.new("RGB", (CW + 64, height + 64), BG)
    d = ImageDraw.Draw(img, "RGBA")

    # shadow
    shadow_color = (15, 23, 42, 26)
    for i in range(18, 0, -1):
        alpha = int(80 * (i / 18) ** 2)
        d.rounded_rectangle(
            [32 - i // 3, 32 + i // 2, CW + 32 + i // 3, height + 32 + i // 2],
            radius=28, fill=(15, 23, 42, alpha)
        )

    # card body
    d.rounded_rectangle([32, 32, CW + 32, height + 32], radius=28, fill=(255, 255, 255))

    # accent stripe
    d.rounded_rectangle([32, 32, CW + 32, 38], radius=28, fill=accent_rgb)
    d.rectangle([32, 36, CW + 32, 38], fill=accent_rgb)  # flatten bottom of stripe

    return img, d


def _draw_header(d, accent_rgb, label: str, icon: str, number: str, y: int) -> int:
    """Draw label + number row. Returns new y."""
    f_label = _font(11, bold=True)
    f_num   = _font(13, bold=True)

    icon_r = 15
    ix = 32 + PAD
    iy = y + 1

    # icon circle
    icon_color = _tint(accent_rgb, 0.12)
    d.ellipse([ix, iy, ix + icon_r * 2, iy + icon_r * 2], fill=icon_color)
    fi = _font(14, bold=True)
    d.text((ix + icon_r - 6, iy + icon_r - 8), icon, font=fi, fill=accent_rgb)

    # label text
    d.text((ix + icon_r * 2 + 10, iy + 4), label, font=f_label, fill=accent_rgb)

    # number badge
    num_w = _text_w(d, number, f_num) + 22
    nx = 32 + CW - PAD - num_w
    num_bg = _tint(accent_rgb, 0.10)
    d.rounded_rectangle([nx, iy + 1, nx + num_w, iy + 26], radius=8, fill=num_bg)
    d.text((nx + 11, iy + 5), number, font=f_num, fill=accent_rgb)

    return y + icon_r * 2 + 16


def _draw_sep(d, y: int) -> int:
    d.line([(32 + PAD, y + 8), (32 + CW - PAD, y + 8)], fill=(232, 236, 240), width=1)
    return y + 24


# ── vocabulary card ────────────────────────────────────────────────────────────

def render_word_card(english: str, russian: str,
                     transcription: str = "",
                     example: str = "",
                     number: str = "—",
                     accent: str = "#2F7D5B") -> io.BytesIO:
    acc = _hex(accent)

    f_word  = _font(52, bold=True)
    f_tr    = _font(22)
    f_lbl   = _font(11, bold=True)
    f_rus   = _font(28, bold=True)
    f_ex    = _font(18)

    # measure
    tmp = Image.new("RGB", (CW, 10))
    d0  = ImageDraw.Draw(tmp)

    word_lines = _wrapped_lines(d0, english, f_word, INNER)
    rus_lines  = _wrapped_lines(d0, russian,  f_rus,  INNER - 52)
    ex_lines   = _wrapped_lines(d0, example,  f_ex,   INNER - 32) if example else []

    word_h = sum(_line_h(f_word) + 6 for _ in word_lines) + 4
    rus_h  = sum(_line_h(f_rus) + 6 for _ in rus_lines)
    ex_h   = (sum(_line_h(f_ex) + 5 for _ in ex_lines) + 28) if ex_lines else 0
    tr_h   = (_line_h(f_tr) + 10) if transcription else 0

    height = (
        24            # top padding after stripe
        + 32          # header
        + 18          # gap
        + word_h
        + 6           # accent line
        + tr_h
        + 28          # sep
        + 16          # label
        + rus_h
        + (20 + ex_h if ex_h else 0)
        + 36          # bottom padding
    )

    img, d = _base_image(height, acc)
    x = 32 + PAD
    y = 32 + 24

    y = _draw_header(d, acc, "VOCABULARY", "📖", number, y)
    y += 10

    # word
    for line in word_lines:
        d.text((x, y), line, font=f_word, fill=(15, 23, 42))
        y += _line_h(f_word) + 6

    # accent line
    y += 2
    d.rounded_rectangle([x, y, x + INNER, y + 3], radius=2, fill=_tint(acc, 0.45))
    y += 14

    # transcription
    if transcription:
        d.text((x, y), transcription, font=f_tr, fill=acc)
        y += _line_h(f_tr) + 10

    y = _draw_sep(d, y)

    # translation label
    d.text((x, y), "ПЕРЕВОД", font=f_lbl, fill=_tint(acc, 0.60))
    y += _line_h(f_lbl) + 10

    # flag + translation
    flag_r = 19
    d.ellipse([x, y, x + flag_r * 2, y + flag_r * 2], fill=_tint(acc, 0.10))
    d.text((x + flag_r - 9, y + flag_r - 10), "🇷🇺", font=_font(16))
    tx = x + flag_r * 2 + 10
    for line in rus_lines:
        d.text((tx, y + 2), line, font=f_rus, fill=(30, 41, 59))
        y += _line_h(f_rus) + 6
    y = max(y, 32 + 24 + height - 36 - ex_h - 20)  # push example to bottom

    # example
    if ex_lines:
        y += 16
        ex_bg = _tint(acc, 0.07)
        ex_total_h = sum(_line_h(f_ex) + 5 for _ in ex_lines) + 24
        d.rounded_rectangle([x, y, x + INNER, y + ex_total_h], radius=16, fill=ex_bg)
        ey = y + 12
        first = True
        for line in ex_lines:
            prefix = "" if not first else ""
            if first:
                # draw quote char
                fq = _font(26)
                d.text((x + 14, ey - 3), "“", font=fq, fill=(*acc, 80))
                d.text((x + 30, ey), line, font=f_ex, fill=(71, 85, 105))
            else:
                d.text((x + 30, ey), line, font=f_ex, fill=(71, 85, 105))
            ey += _line_h(f_ex) + 5
            first = False

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── verb card ──────────────────────────────────────────────────────────────────

def render_verb_card(infinitive: str, russian: str,
                     past_simple: str = "",
                     past_participle: str = "",
                     number: str = "—",
                     accent: str = "#D97A2A",
                     example: str = "") -> io.BytesIO:
    acc = _hex(accent)
    ps  = past_simple      or "—"
    pp  = past_participle  or "—"

    f_form  = _font(32, bold=True)
    f_lbl   = _font(10, bold=True)
    f_rus   = _font(26, bold=True)
    f_ex    = _font(18)
    f_tr_lbl = _font(11, bold=True)

    tmp = Image.new("RGB", (CW, 10))
    d0  = ImageDraw.Draw(tmp)

    cell_w   = (INNER - 20) // 3
    rus_lines = _wrapped_lines(d0, russian, f_rus, INNER - 52)
    ex_lines  = _wrapped_lines(d0, example, f_ex, INNER - 32) if example else []

    form_h = 70   # box height
    rus_h  = sum(_line_h(f_rus) + 6 for _ in rus_lines)
    ex_h   = (sum(_line_h(f_ex) + 5 for _ in ex_lines) + 28) if ex_lines else 0

    height = (
        24
        + 32          # header
        + 18
        + form_h + 16 + 20  # form boxes + label + dots
        + 28          # sep
        + 16 + rus_h  # label + translation
        + (20 + ex_h if ex_h else 0)
        + 36
    )

    img, d = _base_image(height, acc)
    x = 32 + PAD
    y = 32 + 24

    y = _draw_header(d, acc, "IRREGULAR VERB", "↻", number, y)
    y += 10

    # 3 form boxes
    boxes = [
        (infinitive, False, "infinitive"),
        (ps,         True,  "past simple"),
        (pp,         False, "participle"),
    ]
    for i, (text, highlighted, lbl) in enumerate(boxes):
        bx = x + i * (cell_w + 10)
        bg = _tint(acc, 0.12) if highlighted else (247, 249, 252)
        fg = acc if highlighted else (15, 23, 42)
        d.rounded_rectangle([bx, y, bx + cell_w, y + form_h], radius=16, fill=bg)
        fw = _text_w(d, text, f_form)
        d.text((bx + (cell_w - fw) // 2, y + (form_h - _line_h(f_form)) // 2),
               text, font=f_form, fill=fg)

    y += form_h + 8

    # labels under boxes
    for i, (_, _, lbl) in enumerate(boxes):
        bx = x + i * (cell_w + 10)
        lw = _text_w(d, lbl.upper(), _font(10, bold=True))
        d.text((bx + (cell_w - lw) // 2, y), lbl.upper(),
               font=_font(10, bold=True), fill=(148, 163, 184))

    y += _line_h(_font(10, bold=True)) + 12

    # dots connector
    dot_r = 5
    dot_y = y + dot_r
    positions = [x + cell_w // 2, x + cell_w + 10 + cell_w // 2, x + 2 * (cell_w + 10) + cell_w // 2]
    for i, px in enumerate(positions):
        d.ellipse([px - dot_r, dot_y - dot_r, px + dot_r, dot_y + dot_r], fill=acc)
        if i < len(positions) - 1:
            next_px = positions[i + 1]
            d.line([(px + dot_r, dot_y), (next_px - dot_r, dot_y)],
                   fill=(226, 232, 240), width=1)

    y += dot_r * 2 + 10
    y = _draw_sep(d, y)

    # translation label
    d.text((x, y), "ПЕРЕВОД", font=f_tr_lbl, fill=_tint(acc, 0.60))
    y += _line_h(f_tr_lbl) + 10

    # flag + translation
    flag_r = 19
    d.ellipse([x, y, x + flag_r * 2, y + flag_r * 2], fill=_tint(acc, 0.10))
    d.text((x + flag_r - 9, y + flag_r - 10), "🇷🇺", font=_font(16))
    tx = x + flag_r * 2 + 10
    for line in rus_lines:
        d.text((tx, y + 2), line, font=f_rus, fill=(30, 41, 59))
        y += _line_h(f_rus) + 6

    # example
    if ex_lines:
        y += 16
        ex_bg = _tint(acc, 0.07)
        ex_total_h = sum(_line_h(f_ex) + 5 for _ in ex_lines) + 24
        d.rounded_rectangle([x, y, x + INNER, y + ex_total_h], radius=16, fill=ex_bg)
        ey = y + 12
        first = True
        for line in ex_lines:
            if first:
                fq = _font(26)
                d.text((x + 14, ey - 3), "“", font=fq, fill=(*acc, 80))
                d.text((x + 30, ey), line, font=f_ex, fill=(71, 85, 105))
            else:
                d.text((x + 30, ey), line, font=f_ex, fill=(71, 85, 105))
            ey += _line_h(f_ex) + 5
            first = False

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
