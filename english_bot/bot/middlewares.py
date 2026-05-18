"""Outer middlewares applied to every update."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .config import Config
from .services.sheets import append_student_row
from .storage import db

logger = logging.getLogger(__name__)


class EnsureUserMiddleware(BaseMiddleware):
    """Registers every Telegram user in the `users` table on first
    interaction — even if they skipped /start (tapped a button).

    Also makes sure they appear in the `_students` Google Sheet. We try
    once per process lifetime per user (cached in `_sheet_synced`) — the
    underlying append is idempotent (checks for duplicates inside).
    """

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self._cfg = cfg
        # process-level cache: users we've already tried to add this session
        self._sheet_synced: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None and isinstance(event, (Message, CallbackQuery)):
            user = event.from_user

        if user is not None and not user.is_bot:
            try:
                await db.ensure_user(
                    user.id, user.username, user.first_name
                )
            except Exception as e:
                logger.warning("ensure_user failed: %s", e)

            if user.id not in self._sheet_synced:
                self._sheet_synced.add(user.id)
                display = (user.first_name
                           or (f"@{user.username}" if user.username
                               else f"id {user.id}"))
                logger.info("Scheduling _students append for %s (%s)",
                            user.id, display)
                asyncio.create_task(self._safe_append(user.id, display))

        return await handler(event, data)

    async def _safe_append(self, telegram_id: int, name: str) -> None:
        try:
            await asyncio.to_thread(
                append_student_row, self._cfg, telegram_id, name
            )
        except Exception as e:
            logger.warning("append_student_row failed for %s: %s",
                           telegram_id, e)
