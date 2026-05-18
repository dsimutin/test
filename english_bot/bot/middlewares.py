"""Outer middlewares applied to every update."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .storage import db


class EnsureUserMiddleware(BaseMiddleware):
    """Registers every Telegram user in the `users` table on first
    interaction — even if they skipped /start (e.g. tapped a button).
    Without this they'd never appear in /students."""

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
                await db.ensure_user(user.id, user.username, user.first_name)
            except Exception:
                pass  # don't block the update on a DB hiccup
        return await handler(event, data)
