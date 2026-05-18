"""Bot entry point. Run as a module:

    python -m bot.main
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import Config, load_config
from .handlers import quiz as quiz_router
from .handlers import start as start_router
from .handlers import stats as stats_router
from .handlers import sync as sync_router
from .handlers import teacher as teacher_router
from .handlers import verbs as verbs_router
from .handlers import vocabulary as vocab_router
from .services import card_renderer
from .services.sheets import sync_from_sheets
from .storage import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 30 * 60   # 30 min


async def _safe_sync(cfg: Config, tag: str) -> None:
    try:
        nv, nb = await sync_from_sheets(cfg)
        logger.info("[%s] sync ok: %d vocab, %d verbs", tag, nv, nb)
    except Exception as e:
        logger.warning("[%s] sync failed: %s", tag, e)


async def _background_sync_loop(cfg: Config) -> None:
    """Wakes every SYNC_INTERVAL_SECONDS, calls sync_from_sheets."""
    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        await _safe_sync(cfg, tag="bg")


async def main() -> None:
    cfg = load_config()

    db.configure(cfg.db_path)
    await db.init()

    await card_renderer.startup()

    # Initial sync on startup — keep DB fresh on every redeploy
    await _safe_sync(cfg, tag="startup")

    bot = Bot(
        token=cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp["cfg"] = cfg

    dp.include_router(start_router.router)
    dp.include_router(teacher_router.router)
    dp.include_router(sync_router.router)
    dp.include_router(stats_router.router)
    dp.include_router(vocab_router.router)
    dp.include_router(verbs_router.router)
    dp.include_router(quiz_router.router)

    bg_task = asyncio.create_task(_background_sync_loop(cfg),
                                   name="bg-sync")

    logger.info("Bot starting…")
    try:
        await dp.start_polling(bot, cfg=cfg)
    finally:
        bg_task.cancel()
        await card_renderer.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
