"""Entry point — Buff Buddy fitness bot."""
import logging
import asyncio
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from src.config import TELEGRAM_BOT_TOKEN
from src.handlers import (
    cmd_start, cmd_menu, cmd_summary, cmd_week, cmd_goals, cmd_setgoals,
    cmd_recovery, cmd_streak, cmd_pb, cmd_deletelast, cmd_weight,
    cmd_weeklysummary, cmd_content,
    handle_photo, handle_message, handle_callback,
)
from src.scheduler import build_scheduler

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)


async def _post_init(app: Application) -> None:
    scheduler = build_scheduler(app.bot, event_loop=asyncio.get_running_loop())
    scheduler.start()
    app.bot_data["scheduler"] = scheduler


async def _post_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        scheduler.shutdown(wait=False)


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(CommandHandler("setgoals", cmd_setgoals))
    app.add_handler(CommandHandler("recovery", cmd_recovery))
    app.add_handler(CommandHandler("streak", cmd_streak))
    app.add_handler(CommandHandler("pb", cmd_pb))
    app.add_handler(CommandHandler("deletelast", cmd_deletelast))
    app.add_handler(CommandHandler("weight", cmd_weight))
    app.add_handler(CommandHandler("weeklysummary", cmd_weeklysummary))
    app.add_handler(CommandHandler("content", cmd_content))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logging.info("Buff Buddy starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
