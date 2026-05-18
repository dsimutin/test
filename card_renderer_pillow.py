"""Minimal Pillow fallback when Playwright is unavailable."""
import io
import os
import re

from PIL import Image, ImageDraw, ImageFont

W, H = 544, 700
PAD = 40
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _font(size: int, bold: bool = False):
    name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    path = os.path.join(FONT_DIR, name)
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _tint(accent_rgb, alpha: float):
    r, g, b = accent_rgb
    return (
        int(255 * (1 - alpha) + r * alpha),
        int(255 * (1 - alpha) + g * alpha),
        int(255 * (1 - alpha) + b * alpha),
    )


def _draw_card(lines: list[tuple], accent: str) -> io.BytesIO:
    """lines: list of (text, font_size, bold, color_or_None)"""
    bg = (240, 237, 232)
    card_bg = (255, 255, 255)
    ac = _hex_to_rgb(accent)

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # Card shadow (fake, just slightly darker rect)
    d.rounded_rectangle([24, 28, W - 24, H - 28], radius=28, fill=(220, 217, 212))
    # Card body
    d.rounded_rectangle([20, 24, W - 20, H - 24], radius=28, fill=card_bg)
    # Accent stripe
    d.rounded_rectangle([20, 24, W - 20, 30], radius=28, fill=ac)

    y = 24 + PAD
    for text, size, bold, color in lines:
        if text == "__sep__":
            d.line([(PAD + 20, y + 6), (W - PAD - 20, y + 6)], fill=(232, 236, 240), width=1)
            y += 20
            continue
        if color is None:
            color = (15, 23, 42)
        f = _font(size, bold)
        d.text((PAD + 20, y), text, font=f, fill=color)
        bbox = d.textbbox((PAD + 20, y), text, font=f)
        y += (bbox[3] - bbox[1]) + 10

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def render_from_data(html: str) -> io.BytesIO:
    """Very rough extraction of card data from HTML string."""
    def find(pattern):
        m = re.search(pattern, html, re.DOTALL)
        return m.group(1).strip() if m else ""

    accent = find(r'background:(#[0-9A-Fa-f]{6})')
    if not accent:
        accent = "#2F7D5B"
    ac = _hex_to_rgb(accent)

    is_verb = "IRREGULAR VERB" in html or "form-box" in html

    if is_verb:
        inf = find(r'class="form-box">([^<]+)</div>')
        forms = re.findall(r'class="form-box[^"]*">([^<]+)</div>', html)
        rus = find(r'class="translation">([^<]+)</div>')
        lines = [
            ("IRREGULAR VERB", 11, True, _tint(ac, 0.7)),
            (forms[0] if forms else "?", 42, True, (15, 23, 42)),
            ("__sep__", 0, False, None),
            (forms[1] if len(forms) > 1 else "—", 32, True, ac),
            ("past simple", 10, False, (148, 163, 184)),
            (forms[2] if len(forms) > 2 else "—", 32, False, (15, 23, 42)),
            ("past participle", 10, False, (148, 163, 184)),
            ("__sep__", 0, False, None),
            (rus, 26, True, (30, 41, 59)),
        ]
    else:
        word = find(r'class="word">([^<]+)</div>')
        tr = find(r'class="transcription">([^<]+)</div>')
        rus = find(r'class="translation">([^<]+)</div>')
        ex_raw = find(r'class="example-box"><span[^>]*>"</span>([^<]*)')
        lines = [
            ("VOCABULARY", 11, True, _tint(ac, 0.7)),
            (word, 52, True, (15, 23, 42)),
            ("__sep__", 0, False, None),
            (tr, 22, False, ac),
            ("__sep__", 0, False, None),
            ("ПЕРЕВОД", 10, True, _tint(ac, 0.6)),
            (rus, 26, True, (30, 41, 59)),
        ]
        if ex_raw:
            lines.append((f'"{ex_raw}', 16, False, (71, 85, 105)))

    return _draw_card(lines, accent)
