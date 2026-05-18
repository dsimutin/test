"""
Premium flashcard renderer — auto-crops to content height, no empty space.
Matches the HTML/CSS design exactly.
"""
import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CW      = 760          # card width (height is dynamic)
RADIUS  = 36
PAD     = 48
GAP     = 20           # standard vertical gap between blocks
LINE_C  = (232, 236, 240)

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_REGULAR  = os.path.join(FONTS_DIR, "NotoSans-Regular.ttf")
_BOLD     = os.path.join(FONTS_DIR, "NotoSans-Bold.ttf")


# ── helpers ───────────────────────────────────────────────────────────────────

def _hex(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _tint(accent: str, a: float) -> tuple:
    r, g, b = _hex(accent)
    return (int(255*(1-a)+r*a), int(255*(1-a)+g*a), int(255*(1-a)+b*a))

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

def _th(text: str, font) -> int:
    bb = font.getbbox(text)
    return bb[3] - bb[1]

def _wrap(text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if _tw(test, font) <= max_w:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines


# ── canvas ────────────────────────────────────────────────────────────────────

class Canvas:
    """Tall scratch canvas; call finish() to get auto-cropped card."""

    def __init__(self, accent: str):
        self.accent = accent
        self.ar, self.ag, self.ab = _hex(accent)
        self._h = 1400
        self._img = Image.new("RGB", (CW, self._h), (255, 255, 255))
        self._d   = ImageDraw.Draw(self._img)
        self._y   = 0          # current draw cursor

    # ── accent top stripe ──────────────────────────────────────────────────
    def stripe(self):
        self._d.rounded_rectangle(
            [0, 0, CW-1, RADIUS+6], radius=RADIUS,
            fill=(self.ar, self.ag, self.ab)
        )
        self._d.rectangle([0, RADIUS, CW-1, RADIUS+6],
                          fill=(self.ar, self.ag, self.ab))
        self._y = RADIUS + 6 + 26

    # ── header row: LABEL … NUMBER ────────────────────────────────────────
    def header(self, label: str, number: str):
        f = _font(19, bold=True)
        ac = (self.ar, self.ag, self.ab)

        # label pill
        lw = _tw(label, f)
        ph, pv = 18, 9
        self._d.rounded_rectangle(
            [PAD, self._y, PAD+lw+ph*2, self._y+_th(label,f)+pv*2],
            radius=12, fill=_tint(self.accent, .10)
        )
        self._d.text((PAD+ph, self._y+pv), label, font=f, fill=ac)

        # number badge
        nw = _tw(number, f)
        nx = CW - PAD - nw - ph*2
        self._d.rounded_rectangle(
            [nx, self._y, CW-PAD, self._y+_th(number,f)+pv*2],
            radius=12, fill=_tint(self.accent, .10)
        )
        self._d.text((nx+ph, self._y+pv), number, font=f, fill=ac)

        self._y += _th(label, f) + pv*2 + 28

    # ── large word ────────────────────────────────────────────────────────
    def word(self, text: str):
        f = _font(68, bold=True)
        self._d.text((PAD, self._y), text, font=f, fill=(10, 15, 30))
        self._y += _th(text, f) + 16

    # ── accent underline ──────────────────────────────────────────────────
    def underline(self):
        self._d.rectangle(
            [PAD, self._y, CW-PAD, self._y+2],
            fill=_tint(self.accent, .38)
        )
        self._y += 2 + 16

    # ── transcription ─────────────────────────────────────────────────────
    def transcription(self, text: str):
        f = _font(30)
        self._d.text((PAD, self._y), text,
                     font=f, fill=(self.ar, self.ag, self.ab))
        self._y += _th(text, f) + GAP

    # ── thin separator ────────────────────────────────────────────────────
    def separator(self):
        self._y += 6
        self._d.rectangle([PAD, self._y, CW-PAD, self._y+1], fill=LINE_C)
        self._y += 1 + 20

    # ── translation row ───────────────────────────────────────────────────
    def translation(self, text: str):
        f_lbl = _font(16, bold=True)
        f_ru  = _font(34, bold=True)
        self._d.text((PAD, self._y), "ПЕРЕВОД",
                     font=f_lbl, fill=_tint(self.accent, .50))
        self._y += _th("ПЕРЕВОД", f_lbl) + 10

        for line in _wrap(text, f_ru, CW - PAD*2)[:2]:
            self._d.text((PAD, self._y), line, font=f_ru, fill=(15, 25, 50))
            self._y += _th(line, f_ru) + 6
        self._y += GAP - 6

    # ── three verb form boxes ─────────────────────────────────────────────
    def verb_forms(self, inf: str, ps: str, pp: str):
        f_form  = _font(40, bold=True)
        f_label = _font(16, bold=True)
        col_gap = 14
        col_w   = (CW - PAD*2 - col_gap*2) // 3
        box_h   = 130

        forms = [
            (inf, "INFINITIVE",      False),
            (ps,  "PAST SIMPLE",     True),
            (pp,  "PAST PARTICIPLE", False),
        ]
        for i, (form, lbl, is_acc) in enumerate(forms):
            cx = PAD + i*(col_w+col_gap)
            bg = _tint(self.accent, .12) if is_acc else (248, 249, 252)
            self._d.rounded_rectangle(
                [cx, self._y, cx+col_w, self._y+box_h],
                radius=18, fill=bg
            )
            fc = (self.ar, self.ag, self.ab) if is_acc else (10, 15, 30)
            fw = _tw(form, f_form)
            self._d.text((cx+(col_w-fw)//2, self._y+22), form, font=f_form, fill=fc)
            lw = _tw(lbl, f_label)
            self._d.text((cx+(col_w-lw)//2, self._y+82), lbl,
                         font=f_label, fill=(148, 163, 184))

        self._y += box_h + 18

        # dots connector
        centres = [PAD + i*(col_w+col_gap) + col_w//2 for i in range(3)]
        for i, cx in enumerate(centres):
            self._d.ellipse([cx-6, self._y-6, cx+6, self._y+6],
                            fill=(self.ar, self.ag, self.ab))
            if i < 2:
                nx = centres[i+1] - 7
                self._d.rectangle([cx+8, self._y-1, nx, self._y+1], fill=LINE_C)
        self._y += 24

    # ── example block with highlighted word ───────────────────────────────
    def example(self, text: str, highlight: str):
        f_reg  = _font(26)
        f_bold = _font(26, bold=True)
        f_q    = _font(44, bold=True)
        inner  = CW - PAD*2 - 48
        lines  = _wrap(text, f_reg, inner)
        lh     = 38
        box_h  = len(lines)*lh + 48

        self._d.rounded_rectangle(
            [PAD, self._y, CW-PAD, self._y+box_h],
            radius=20, fill=_tint(self.accent, .07)
        )
        # quote mark
        self._d.text((PAD+16, self._y+4), "“",
                     font=f_q, fill=_tint(self.accent, .28))

        ty  = self._y + 34
        hl  = highlight.lower()
        ac  = (self.ar, self.ag, self.ab)

        for line in lines:
            lo  = line.lower()
            idx = lo.find(hl)
            if idx == -1:
                cx = PAD + (CW-PAD*2 - _tw(line, f_reg))//2
                self._d.text((cx, ty), line, font=f_reg, fill=(100, 116, 139))
            else:
                before = line[:idx]
                mid    = line[idx:idx+len(highlight)]
                after  = line[idx+len(highlight):]
                total  = _tw(before,f_reg)+_tw(mid,f_bold)+_tw(after,f_reg)
                cx = PAD + (CW-PAD*2 - total)//2
                if before:
                    self._d.text((cx, ty), before, font=f_reg, fill=(100,116,139))
                    cx += _tw(before, f_reg)
                self._d.text((cx, ty), mid, font=f_bold, fill=ac)
                cx += _tw(mid, f_bold)
                if after:
                    self._d.text((cx, ty), after, font=f_reg, fill=(100,116,139))
            ty += lh

        self._y += box_h

    # ── finish: round corners, crop, export ───────────────────────────────
    def finish(self) -> io.BytesIO:
        final_h = self._y + PAD         # bottom padding
        img = self._img.crop((0, 0, CW, final_h))

        # apply rounded corner mask
        mask = Image.new("L", (CW, final_h), 0)
        md   = ImageDraw.Draw(mask)
        md.rounded_rectangle([0, 0, CW-1, final_h-1], radius=RADIUS, fill=255)

        bg = Image.new("RGB", (CW, final_h), (240, 237, 232))  # page bg
        bg.paste(img, mask=mask)

        buf = io.BytesIO()
        bg.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf


# ── Public API ────────────────────────────────────────────────────────────────

def render_word_card(english: str, russian: str,
                     transcription: str = "",
                     example: str = "",
                     number: str = "—",
                     accent: str = "#2F7D5B") -> io.BytesIO:
    c = Canvas(accent)
    c.stripe()
    c.header("VOCABULARY", number)
    c.word(english)
    c.underline()
    if transcription:
        c.transcription(transcription)
    c.separator()
    c.translation(russian)
    if example:
        c.separator()
        c.example(example, english)
    return c.finish()


def render_verb_card(infinitive: str, russian: str,
                     past_simple: str = "",
                     past_participle: str = "",
                     number: str = "—",
                     accent: str = "#D97A2A",
                     example: str = "") -> io.BytesIO:
    c = Canvas(accent)
    c.stripe()
    c.header("IRREGULAR VERB", number)
    c.verb_forms(infinitive, past_simple or "—", past_participle or "—")
    c.separator()
    c.translation(russian)
    if example:
        hl = past_simple or infinitive
        c.separator()
        c.example(example, hl)
    return c.finish()
