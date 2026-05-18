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
from .middlewares import EnsureUserMiddleware
from .handlers import quiz as quiz_router
from .handlers import start as start_router
from .handlers import stats as stats_router
from .handlers import sync as sync_router
from .handlers import teacher as teacher_router
from .handlers import verbs as verbs_router
from .handlers import vocabulary as vocab_router
from .services import card_renderer
from .services.sheets import sync_from_sheets
from .services.snapshot import dump_to_sheets, restore_from_sheets
from .storage import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS     = 30 * 60   # 30 min — refresh content from sheets
SNAPSHOT_INTERVAL_SECONDS = 5 * 60    # 5 min  — back up progress to sheets


async def _safe_sync(cfg: Config, tag: str) -> None:
    try:
        nv, nb = await sync_from_sheets(cfg)
        logger.info("[%s] content sync: %d vocab, %d verbs", tag, nv, nb)
    except Exception as e:
        logger.warning("[%s] content sync failed: %s", tag, e)


async def _safe_snapshot(cfg: Config, tag: str) -> None:
    try:
        await dump_to_sheets(cfg)
        logger.info("[%s] progress snapshot pushed", tag)
    except Exception as e:
        logger.warning("[%s] progress snapshot failed: %s", tag, e)


async def _safe_restore(cfg: Config) -> None:
    if not await db.is_progress_empty():
        logger.info("SQLite already has progress data, skipping restore")
        return
    try:
        result = await restore_from_sheets(cfg)
        logger.info("Restored from sheets: %s", result)
    except Exception as e:
        logger.warning("Restore from sheets failed: %s", e)


async def _content_sync_loop(cfg: Config) -> None:
    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        await _safe_sync(cfg, tag="bg")


async def _snapshot_loop(cfg: Config) -> None:
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
        await _safe_snapshot(cfg, tag="bg")


async def main() -> None:
    cfg = load_config()

    db.configure(cfg.db_path)
    await db.init()

    await card_renderer.startup()

    # Step 1: pull fresh content from the user's sheets
    await _safe_sync(cfg, tag="startup")
    # Step 2: if SQLite is empty (fresh container) — restore progress from
    #         the snapshot sheets so a redeploy doesn't wipe student data.
    await _safe_restore(cfg)

    bot = Bot(
        token=cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp["cfg"] = cfg

    dp.message.outer_middleware(EnsureUserMiddleware())
    dp.callback_query.outer_middleware(EnsureUserMiddleware())

    dp.include_router(start_router.router)
    dp.include_router(teacher_router.router)
    dp.include_router(sync_router.router)
    dp.include_router(stats_router.router)
    dp.include_router(vocab_router.router)
    dp.include_router(verbs_router.router)
    dp.include_router(quiz_router.router)

    sync_task = asyncio.create_task(_content_sync_loop(cfg),
                                     name="bg-sync")
    snap_task = asyncio.create_task(_snapshot_loop(cfg),
                                     name="bg-snapshot")

    logger.info("Bot starting…")
    try:
        await dp.start_polling(bot, cfg=cfg)
    finally:
        sync_task.cancel()
        snap_task.cancel()
        # final best-effort snapshot before container dies
        await _safe_snapshot(cfg, tag="shutdown")
        await card_renderer.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
