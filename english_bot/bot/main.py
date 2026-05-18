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

from .config import load_config
from .handlers import quiz as quiz_router
from .handlers import start as start_router
from .handlers import stats as stats_router
from .handlers import sync as sync_router
from .handlers import teacher as teacher_router
from .handlers import verbs as verbs_router
from .handlers import vocabulary as vocab_router
from .services import card_renderer
from .storage import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    cfg = load_config()

    # storage
    db.configure(cfg.db_path)
    await db.init()

    # playwright (long-lived browser)
    await card_renderer.startup()

    bot = Bot(
        token=cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Inject config into every handler call via dispatcher workflow data
    dp["cfg"] = cfg

    dp.include_router(start_router.router)
    dp.include_router(teacher_router.router)
    dp.include_router(sync_router.router)
    dp.include_router(stats_router.router)
    dp.include_router(vocab_router.router)
    dp.include_router(verbs_router.router)
    dp.include_router(quiz_router.router)

    logger.info("Bot starting…")
    try:
        await dp.start_polling(bot, cfg=cfg)
    finally:
        await card_renderer.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
