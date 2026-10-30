from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message
from sqlalchemy import select

from app.adapters.base import MarketplaceAdapter
from app.config import Settings
from app.db import Database
from app.domain.analysis import decimal_text
from app.scanner import Scanner
from app.storage.models import ListingRow
from app.storage.repository import Repository

logger = logging.getLogger(__name__)


def build_dispatcher(
    settings: Settings, database: Database, scanner: Scanner, adapters: list[MarketplaceAdapter]
) -> Dispatcher:
    router = Router()

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if message.chat.type != "private":
            await message.answer("Откройте личный чат с ботом, чтобы подписаться на уведомления.")
            return
        async with database.sessions() as session:
            await Repository(session).subscribe(message.chat.id)
            await session.commit()
        await message.answer(
            "Бот запущен в режиме мониторинга. Уведомления включены для этого чата.\n"
            "Доступны /status, /scan, /floor, /opportunities, /collections, /settings, "
            "/history и /stats."
        )
        await scanner.notify_recent(message.bot, message.chat.id)

    @router.message(Command("stop"))
    async def stop(message: Message) -> None:
        async with database.sessions() as session:
            await Repository(session).unsubscribe(message.chat.id)
            await session.commit()
        await message.answer("Уведомления отключены. Команды бота остаются доступны.")

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        rows = [f"Режим: {settings.app_mode.value}", "Состояние источников:"]
        for adapter in adapters:
            item = await adapter.status()
            rows.append(f"• {item.marketplace.value}: {item.state.value} — {item.message}")
        rows.append(f"Интервал сканирования: {settings.scan_interval_seconds} сек.")
        await message.answer("\n".join(rows))

    @router.message(Command("scan"))
    async def scan(message: Message) -> None:
        await message.answer("Сканирую доступные источники…")
        result = await scanner.scan(bot=message.bot)
        if result.skipped:
            await message.answer("Сканирование уже выполняется. Попробуйте чуть позже.")
            return
        state = [
            f"Режим: {settings.app_mode.value}",
            f"Получено объявлений: {result.fetched_listings}",
            f"Актуальных объявлений: {result.fresh_listings}",
            f"Новых возможностей: {result.new_opportunities}",
        ]
        unavailable = [row.marketplace.value for row in result.statuses if not row.enabled]
        if unavailable:
            state.append("Не подключены источники: " + ", ".join(unavailable))
        await message.answer("\n".join(state))

    @router.message(Command("floor"))
    async def floor(message: Message) -> None:
        async with database.sessions() as session:
            rows = await Repository(session).latest_floors(settings.app_mode, limit=30)
        if not rows:
            await message.answer("Пока нет данных о флорах. Выполните /scan.")
            return
        title = "DEMO флоры (тестовые данные):" if settings.app_mode.value == "DEMO" else "LIVE флоры:"
        lines = [title + " Цены по валютам разделены."]
        for row in rows:
            lines.append(
                f"• {row.collection}"
                + (f" / {row.dimension}: {row.value}" if row.dimension != "collection" else "")
                + f": {decimal_text(row.floor)} {row.currency} "
                f"({row.listing_count} объявл.)"
            )
        await message.answer("\n".join(lines))

    @router.message(Command("opportunities"))
    async def opportunities(message: Message) -> None:
        async with database.sessions() as session:
            rows = await Repository(session).recent_opportunities(settings.app_mode, limit=10)
        if not rows:
            await message.answer("Возможностей пока нет. Сканирование не гарантирует прибыль.")
            return
        title = (
            "DEMO — последние тестовые обнаружения:"
            if settings.app_mode.value == "DEMO"
            else "LIVE — последние обнаружения:"
        )
        lines = [title]
        for row in rows:
            ref = (
                f"; сравнение {decimal_text(row.reference_price)} {row.currency}"
                if row.reference_price is not None
                else ""
            )
            gross = (
                f"; валовый спред до комиссий "
                f"{decimal_text(row.gross_spread)} {row.currency}"
                + (
                    f" ({Decimal(row.gross_margin_percent).quantize(Decimal('0.1'))}% "
                    "от цены продажи)"
                    if row.gross_margin_percent is not None
                    else ""
                )
                if row.gross_spread is not None
                else ""
            )
            lines.append(
                f"• {row.marketplace}: {row.listing_id} — {decimal_text(row.price)} "
                f"{row.currency}{ref}{gross}"
                + (f"\n{row.url}" if row.url else "\nТестовое объявление; внешней ссылки нет.")
            )
        await message.answer("\n\n".join(lines), disable_web_page_preview=True)

    @router.message(Command("collections"))
    async def collections(message: Message) -> None:
        async with database.sessions() as session:
            rows = await session.scalars(
                select(ListingRow.collection)
                .where(ListingRow.mode == settings.app_mode.value)
                .distinct()
                .order_by(ListingRow.collection)
                .limit(100)
            )
            names = list(rows)
        await message.answer(
            "Отслеживаемые коллекции:\n" + ("\n".join(f"• {name}" for name in names) if names else "пока нет")
        )

    @router.message(Command("settings"))
    async def settings_command(message: Message) -> None:
        await message.answer(
            "Настройки мониторинга:\n"
            f"• Режим данных: {settings.app_mode.value}\n"
            f"• Минимальная скидка: {settings.min_discount_percent}%\n"
            f"• Макс. возраст объявления: {settings.listing_max_age_seconds} сек.\n"
            f"• Минимум похожих объявлений для признака: {settings.min_trait_comparables}\n"
            "Измените значения в .env и перезапустите сервис."
        )

    @router.message(Command("history"))
    async def history(message: Message) -> None:
        async with database.sessions() as session:
            rows = await Repository(session).recent_listings(settings.app_mode, limit=15)
        if not rows:
            await message.answer("История появится после получения объявлений из источника.")
            return
        lines = ["Последние наблюдения:"]
        for row in rows:
            lines.append(
                f"• {settings.app_mode.value} / {row.marketplace}: "
                f"{row.collection} #{row.nft_id or row.listing_id} — "
                f"{decimal_text(row.price)} {row.currency}"
            )
        await message.answer("\n".join(lines))

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        async with database.sessions() as session:
            repository = Repository(session)
            totals = await repository.statistics(settings.app_mode)
            distribution = await repository.listing_distribution(settings.app_mode)
        avg = totals["average_discount"]
        avg_text = f"{Decimal(avg).quantize(Decimal('0.1'))}%" if avg is not None else "нет данных"
        lines = [
            f"Режим: {settings.app_mode.value}",
            f"Объявлений сохранено: {totals['listings']}",
            f"Возможностей обнаружено: {totals['opportunities']}",
            f"Средняя скидка: {avg_text}",
            "Распределение по меткам источника:",
        ]
        lines.extend(f"• {name}: {count}" for name, count in distribution)
        await message.answer("\n".join(lines))

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запустить и подписаться на уведомления"),
            BotCommand(command="status", description="Статус источников мониторинга"),
            BotCommand(command="scan", description="Запустить сканирование вручную"),
            BotCommand(command="floor", description="Показать наблюдаемые флоры"),
            BotCommand(command="opportunities", description="Последние возможности"),
            BotCommand(command="collections", description="Список коллекций"),
            BotCommand(command="settings", description="Настройки анализа"),
            BotCommand(command="history", description="История объявлений"),
            BotCommand(command="stats", description="Статистика мониторинга"),
            BotCommand(command="stop", description="Отключить уведомления"),
        ]
    )
