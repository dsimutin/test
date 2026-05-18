"""HTML → PNG card rendering via Playwright.

Renders Jinja2 templates with embedded CSS, then takes a screenshot of
the `.flashcard` element (not the viewport) at 1080×1350.

A single Playwright browser is reused across calls — much faster than
launching per render.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import Browser, async_playwright

from ..config import Config
from ..storage.models import VerbItem, VocabularyItem

logger = logging.getLogger(__name__)

CARD_W, CARD_H = 1080, 1350

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_STATIC = Path(__file__).resolve().parent.parent / "static"

_env = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    autoescape=select_autoescape(["html"]),
)


class _Renderer:
    def __init__(self) -> None:
        self._pw = None
        self._browser: Optional[Browser] = None
        self._css = (_STATIC / "card.css").read_text(encoding="utf-8")
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(args=["--no-sandbox"])

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def screenshot(self, html_str: str, out_path: Path) -> Path:
        if self._browser is None:
            await self.start()
        async with self._lock:
            context = await self._browser.new_context(  # type: ignore[union-attr]
                viewport={"width": CARD_W, "height": CARD_H},
                device_scale_factor=1,
            )
            page = await context.new_page()
            await page.set_content(html_str, wait_until="networkidle")
            element = await page.query_selector(".flashcard")
            if element is None:
                raise RuntimeError(".flashcard element not found")
            await element.screenshot(path=str(out_path), omit_background=False)
            await context.close()
        return out_path


_renderer = _Renderer()


async def startup() -> None:
    await _renderer.start()


async def shutdown() -> None:
    await _renderer.stop()


# ── helpers ───────────────────────────────────────────────────────────────

def _highlight(text: str, word: str, accent: str) -> str:
    """Wrap `word` (case-insensitive, whole-word) in <span class=hl>."""
    if not text:
        return ""
    safe = html.escape(text)
    if not word:
        return safe
    pattern = re.compile(rf"({re.escape(word)})", re.IGNORECASE)
    return pattern.sub(r'<span class="hl">\1</span>', safe)


def _cache_path(cfg: Config, kind: str, item_id: str, number: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", item_id)
    return cfg.card_cache_dir / f"{kind}_{safe_id}_{number}.png"


# ── public API ────────────────────────────────────────────────────────────

async def render_vocabulary_card(item: VocabularyItem, number: str,
                                 cfg: Config) -> Path:
    out = _cache_path(cfg, "vocab", item.id, number)
    if out.exists():
        return out
    tpl = _env.get_template("vocabulary_card.html")
    html_str = tpl.render(
        css=_renderer._css,
        accent=item.accent,
        number=number,
        word=item.word,
        transcription=item.transcription,
        translation=item.translation,
        example=item.example,
        example_html=_highlight(item.example, item.highlight, item.accent),
    )
    return await _renderer.screenshot(html_str, out)


async def render_irregular_card(item: VerbItem, number: str,
                                cfg: Config) -> Path:
    out = _cache_path(cfg, "verb", item.id, number)
    if out.exists():
        return out
    tpl = _env.get_template("irregular_card.html")
    html_str = tpl.render(
        css=_renderer._css,
        accent=item.accent,
        number=number,
        infinitive=item.infinitive,
        past_simple=item.past_simple,
        past_participle=item.past_participle,
        translation=item.translation,
        example=item.example,
        example_html=_highlight(item.example, item.highlight, item.accent),
    )
    return await _renderer.screenshot(html_str, out)
