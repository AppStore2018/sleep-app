"""
Orveo | Telegram Bot & Predictive Notification Engine
-----------------------------------------------------
Бот на базе aiogram 3 и aiohttp с встроенным REST API и планировщиком уведомлений.

Функционал:
1. Обработка команд /start и запуск Telegram Mini App Orveo.
2. REST API эндпоинт /api/sync для приема событий сна/бодрствования из Orveo Mini App.
3. Фоновый планировщик: рассчитывает время до окна сна и присылает push-сообщение
   в Telegram родителю за 15 минут до критической усталости и кортизолового скачка.
4. Отмена уведомлений при досрочном засыпании малыша.

Установка зависимостей:
pip install aiogram aiohttp apscheduler
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, MenuButtonWebApp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==================== НАСТРОЙКИ ====================
# Вставьте сюда токен, полученный от @BotFather в Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ОТ_BOTFATHER")

# URL вашего развернутого фронтенда index.html (на Vercel или GitHub Pages)
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://your-orveo-app.vercel.app")

# Порт веб-сервера для синхронизации с Mini App
PORT = int(os.getenv("PORT", 8080))

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Инициализация бота, диспетчера и планировщика задач
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# Сессии пользователей в памяти
user_sessions: Dict[int, Dict[str, Any]] = {}


# ==================== TELEGRAM BOT HANDLERS ====================

@dp.message(CommandStart())
async def handle_start(message: types.Message):
    """
    Обработчик команды /start.
    Приветствует родителя и выдает кнопку для мгновенного запуска Orveo Mini App.
    """
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "мама"

    # Настраиваем нативную кнопку меню Telegram WebApp (слева от поля ввода)
    try:
        await bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonWebApp(
                text="Orveo 🌙",
                web_app=WebAppInfo(url=WEB_APP_URL)
            )
        )
    except Exception as e:
        logger.warning(f"Не удалось установить Chat Menu Button: {e}")

    # Инлайн-кнопка в самом сообщении
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть Orveo 👶✨",
                    web_app=WebAppInfo(url=WEB_APP_URL)
                )
            ]
        ]
    )

    welcome_text = (
        f"Здравствуйте, {first_name}! 👋\n\n"
        "Я предиктивный ассистент детского сна **Orveo**.\n\n"
        "Я помогу вам избежать истерик и перегулов при укладывании, вовремя находя "
        "**«золотое окно сна» (SweetSpot)** вашего малыша.\n\n"
        "👇 Нажмите кнопку ниже, чтобы открыть Orveo в 1 клик:"
    )

    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=kb)


# ==================== УВЕДОМЛЕНИЯ О СНЕ (PUSH ENGINE) ====================

async def send_sleep_reminder(user_id: int, child_name: str, minutes_left: int):
    """
    Отправка пуш-уведомления родителю перед наступлением окна сна.
    """
    text = (
        f"⏳ **{child_name} скоро захочет спать!**\n\n"
        f"Через **{minutes_left} минут** откроется окно сна Orveo SweetSpot™.\n\n"
        "💡 **Рекомендация Orveo:**\n"
        "• Приглушите свет в комнате\n"
        "• Перейдите к спокойным играм\n"
        "• Включите белый шум или начните привычный ритуал\n\n"
        "Так малыш уснет за 5–10 минут без слез и перегула! 🌙"
    )

    try:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отметить сон в Orveo 💤",
                        web_app=WebAppInfo(url=WEB_APP_URL)
                    )
                ]
            ]
        )
        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown", reply_markup=kb)
        logger.info(f"Напоминание Orveo отправлено {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке напоминания Orveo {user_id}: {e}")


def schedule_wake_reminders(user_id: int, child_name: str, wake_window_minutes: int):
    """
    Планирует напоминание родителю за 15 минут до конца окна бодрствования.
    """
    job_id = f"reminder_{user_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    reminder_delay_minutes = max(1, wake_window_minutes - 15)
    run_date = datetime.now() + timedelta(minutes=reminder_delay_minutes)

    scheduler.add_job(
        send_sleep_reminder,
        trigger="date",
        run_date=run_date,
        args=[user_id, child_name, 15],
        id=job_id,
        replace_existing=True
    )
    logger.info(f"Orveo: Запланировано напоминание для {user_id} на {run_date} (через {reminder_delay_minutes} мин)")


def cancel_reminders(user_id: int):
    """
    Отменяет напоминания, если малыш уже уснул.
    """
    job_id = f"reminder_{user_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info(f"Orveo: Напоминание для {user_id} отменено (малыш уснул).")


# ==================== REST API ДЛЯ MINI APP ====================

async def handle_sync_event(request: web.Request) -> web.Response:
    """
    API эндпоинт, куда Orveo Mini App отправляет данные событий.
    """
    try:
        data = await request.json()
        user_id = data.get("telegram_id")
        event_type = data.get("event")  # "fell_asleep" или "woke_up"
        child_name = data.get("child_name", "Малыш")
        wake_window_minutes = int(data.get("wake_window_minutes", 120))

        if not user_id:
            return web.json_response({"ok": False, "error": "telegram_id обязателен"}, status=400)

        user_id = int(user_id)

        if event_type == "woke_up":
            schedule_wake_reminders(user_id, child_name, wake_window_minutes)
            return web.json_response({"ok": True, "status": "reminder_scheduled", "minutes": wake_window_minutes})

        elif event_type == "fell_asleep":
            cancel_reminders(user_id)
            return web.json_response({"ok": True, "status": "reminders_cancelled"})

        return web.json_response({"ok": False, "error": "Неизвестный тип события"}, status=400)

    except Exception as e:
        logger.exception("Ошибка в handle_sync_event Orveo")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_ping(request: web.Request) -> web.Response:
    """Health check."""
    return web.json_response({"status": "running", "service": "Orveo-Bot"})


# ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================

async def main():
    scheduler.start()

    app = web.Application()
    
    async def cors_middleware(app, handler):
        async def middleware_handler(request):
            if request.method == "OPTIONS":
                response = web.Response()
            else:
                response = await handler(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response
        return middleware_handler

    app.middlewares.append(cors_middleware)
    app.router.add_get("/", handle_ping)
    app.router.add_post("/api/sync", handle_sync_event)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"API сервер Orveo запущен на порту {PORT}")

    logger.info("Запуск Telegram Polling Orveo...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Сервис Orveo остановлен.")