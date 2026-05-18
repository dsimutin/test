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
    interaction — even if they skipped /start (e.g. tapped a button).

    When a brand-new user appears, schedules a background task to
    append them to the `_students` Google Sheet immediately, so the
    teacher can configure their personal spreadsheet without waiting
    for the next 30-min sync.
    """

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self._cfg = cfg

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
                was_new = await db.ensure_user(
                    user.id, user.username, user.first_name
                )
            except Exception:
                was_new = False
            if was_new:
                display = (user.first_name
                           or (f"@{user.username}" if user.username
                               else f"id {user.id}"))
                asyncio.create_task(
                    asyncio.to_thread(
                        append_student_row, self._cfg, user.id, display
                    )
                )
                logger.info("New user registered: %s (%s)", user.id, display)
        return await handler(event, data)
