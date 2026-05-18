"""
Renders flashcards by screenshotting the exact same HTML used in the web preview.
Uses Playwright (Chromium) so the output is pixel-perfect identical to the browser.
Falls back to Pillow if Playwright is not available.
"""
import io
import logging
import os
import re

logger = logging.getLogger(__name__)

# Accent colour palette — cycles by word_id
ACCENT_PALETTE = [
    "#2F7D5B",  # green
    "#2F6FD6",  # blue
    "#7A4FB3",  # purple
    "#C44B6A",  # rose
    "#D97A2A",  # orange
    "#1A7A8A",  # teal
]

def pick_accent(word_id: int) -> str:
    return ACCENT_PALETTE[word_id % len(ACCENT_PALETTE)]


# ── HTML card templates ────────────────────────────────────────────────────────

_HEAD = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Inter',system-ui,sans-serif;
  background:#F0EDE8;
  padding:32px;
  display:inline-block;
}
.card{
  width:480px;
  background:#fff;
  border-radius:32px;
  padding:36px;
  box-shadow:0 2px 4px rgba(15,23,42,.05),0 16px 40px rgba(15,23,42,.13);
  display:flex;
  flex-direction:column;
  gap:0;
  position:relative;
  overflow:hidden;
}
.card::before{
  content:'';
  position:absolute;
  inset:0 0 auto 0;
  height:5px;
  border-radius:32px 32px 0 0;
  background:ACCENT;
}
.header{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:26px;
}
.label{
  display:flex;align-items:center;gap:8px;
  font-size:11px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;
  color:ACCENT;
}
.label-icon{
  width:28px;height:28px;border-radius:50%;
  background:ACCENT18;
  display:flex;align-items:center;justify-content:center;
  font-size:13px;font-weight:900;color:ACCENT;
}
.number{
  font-size:13px;font-weight:700;
  padding:5px 13px;border-radius:10px;
  background:ACCENT12;color:ACCENT;
}
.word{
  font-size:58px;font-weight:800;color:#0F172A;
  letter-spacing:-.04em;line-height:1;
  margin-bottom:14px;
}
.accent-line{
  height:2px;border-radius:2px;
  background:ACCENT50;
  margin-bottom:14px;
}
.transcription{
  font-size:26px;font-weight:500;color:ACCENT;
}
.sep{
  height:1px;background:#E8ECF0;
  margin:20px 0;
}
.translation-lbl{
  font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:ACCENT60;margin-bottom:8px;
}
.translation{
  font-size:30px;font-weight:700;color:#1E293B;line-height:1.25;
}
.example-box{
  margin-top:20px;
  padding:16px 20px;
  border-radius:20px;
  background:ACCENT08;
  font-size:20px;line-height:1.6;color:#475569;
}
.example-box .q{
  font-family:Georgia,serif;font-size:26px;opacity:.3;
  margin-right:3px;vertical-align:-.1em;
}
.example-box .hl{color:ACCENT;font-weight:700;}
/* verb forms */
.forms{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px;}
.form-cell{display:flex;flex-direction:column;align-items:center;gap:8px;}
.form-box{
  width:100%;padding:16px 8px;border-radius:18px;
  background:#F7F9FC;text-align:center;
  font-size:30px;font-weight:800;color:#0F172A;line-height:1;
}
.form-box.acc{background:ACCENT12;color:ACCENT;}
.form-lbl{
  font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:#94A3B8;
}
.dots{display:flex;align-items:center;padding:0 12%;margin-bottom:4px;}
.dot{width:9px;height:9px;border-radius:50%;background:ACCENT;flex-shrink:0;}
.dot-line{flex:1;height:1px;background:#E2E8F0;}
</style>
</head>
<body>
"""

def _css(tmpl: str, accent: str) -> str:
    """Replace ACCENT placeholders with actual colour and its tints."""
    r, g, b = int(accent[1:3],16), int(accent[3:5],16), int(accent[5:7],16)
    def tint(a):
        return f"rgb({int(255*(1-a)+r*a)},{int(255*(1-a)+g*a)},{int(255*(1-a)+b*a)})"
    return (tmpl
        .replace("ACCENT60", tint(.60))
        .replace("ACCENT50", tint(.45))
        .replace("ACCENT18", tint(.12))
        .replace("ACCENT12", tint(.10))
        .replace("ACCENT08", tint(.07))
        .replace("ACCENT",   accent))


def _hl(text: str, word: str, accent: str) -> str:
    """Wrap highlight word in <span class=hl>."""
    return re.sub(
        rf'(\b{re.escape(word)}\b)',
        f'<span class="hl">{word}</span>',
        text, flags=re.IGNORECASE
    )


def _vocab_html(english, russian, transcription, example, number, accent) -> str:
    css = _css(_HEAD, accent)
    hl_ex = _hl(example, english, accent) if example else ""
    ex_block = (f'<div class="example-box"><span class="q">"</span>{hl_ex}</div>'
                if example else "")
    tr_block = (f'<div class="transcription">{transcription}</div>' if transcription else "")
    return f"""{css}
<div class="card">
  <div class="header">
    <div class="label">
      <div class="label-icon">📖</div>
      VOCABULARY
    </div>
    <div class="number">{number}</div>
  </div>
  <div class="word">{english}</div>
  <div class="accent-line"></div>
  {tr_block}
  <div class="sep"></div>
  <div class="translation-lbl">ПЕРЕВОД</div>
  <div class="translation">{russian}</div>
  {ex_block}
</div>
</body></html>"""


def _verb_html(infinitive, russian, past_simple, past_participle,
               example, number, accent) -> str:
    css = _css(_HEAD, accent)
    ps  = past_simple      or "—"
    pp  = past_participle  or "—"
    hl_ex = _hl(example, past_simple or infinitive, accent) if example else ""
    ex_block = (f'<div class="example-box"><span class="q">"</span>{hl_ex}</div>'
                if example else "")
    return f"""{css}
<div class="card">
  <div class="header">
    <div class="label">
      <div class="label-icon" style="font-size:16px">↻</div>
      IRREGULAR VERB
    </div>
    <div class="number">{number}</div>
  </div>
  <div class="forms">
    <div class="form-cell">
      <div class="form-box">{infinitive}</div>
      <div class="form-lbl">infinitive</div>
    </div>
    <div class="form-cell">
      <div class="form-box acc">{ps}</div>
      <div class="form-lbl">past simple</div>
    </div>
    <div class="form-cell">
      <div class="form-box">{pp}</div>
      <div class="form-lbl">participle</div>
    </div>
  </div>
  <div class="dots">
    <div class="dot"></div><div class="dot-line"></div>
    <div class="dot"></div><div class="dot-line"></div>
    <div class="dot"></div>
  </div>
  <div class="sep"></div>
  <div class="translation-lbl">ПЕРЕВОД</div>
  <div class="translation">{russian}</div>
  {ex_block}
</div>
</body></html>"""


# ── Playwright renderer ────────────────────────────────────────────────────────

def _render_html(html: str) -> io.BytesIO:
    """Screenshot an HTML string with headless Chromium via Playwright."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 600, "height": 900})
        page.set_content(html, wait_until="networkidle")
        card = page.query_selector(".card")
        png = card.screenshot()
        browser.close()
    buf = io.BytesIO(png)
    buf.seek(0)
    return buf


# ── Pillow fallback ────────────────────────────────────────────────────────────

def _pillow_fallback(html: str) -> io.BytesIO:
    """Ultra-simple Pillow render when Playwright isn't available."""
    from card_renderer_pillow import render_from_data
    return render_from_data(html)


def _render(html: str) -> io.BytesIO:
    try:
        return _render_html(html)
    except Exception as e:
        logger.warning("Playwright failed (%s), using Pillow fallback", e)
        return _pillow_fallback(html)


# ── Public API ─────────────────────────────────────────────────────────────────

def render_word_card(english: str, russian: str,
                     transcription: str = "",
                     example: str = "",
                     number: str = "—",
                     accent: str = "#2F7D5B") -> io.BytesIO:
    html = _vocab_html(english, russian, transcription, example, number, accent)
    return _render(html)


def render_verb_card(infinitive: str, russian: str,
                     past_simple: str = "",
                     past_participle: str = "",
                     number: str = "—",
                     accent: str = "#D97A2A",
                     example: str = "") -> io.BytesIO:
    html = _verb_html(infinitive, russian, past_simple, past_participle,
                      example, number, accent)
    return _render(html)
