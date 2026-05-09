"""Entry point — builds the bot, registers handlers, starts the scheduler."""
import logging
import asyncio
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from src.config import TELEGRAM_BOT_TOKEN
from src.handlers import (
    cmd_start, cmd_summary, cmd_week, cmd_goals, cmd_setgoals,
    cmd_recovery, cmd_streak, cmd_pb,
    handle_photo, handle_gym_text, handle_sleep_reply, handle_food_text,
)
from src.router import classify_text
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


async def route_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Route plain text to gym or sleep handler."""
    text = update.message.text or ""
    kind = classify_text(text)
    if kind == "gym":
        await handle_gym_text(update, ctx)
    elif kind == "sleep":
        await handle_sleep_reply(update, ctx)
    elif kind == "food":
        await handle_food_text(update, ctx)
    else:
        await update.message.reply_text(
            "Logged as food — if that's wrong, prefix your message:\n"
            "• `GYM Bench 80kg 4x5 RPE 8` for gym\n"
            "• `SLEEP 7.5 4` or just `7.5 4` for sleep\n"
            "• Anything else is treated as food",
            parse_mode="Markdown",
        )


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(CommandHandler("setgoals", cmd_setgoals))
    app.add_handler(CommandHandler("recovery", cmd_recovery))
    app.add_handler(CommandHandler("streak", cmd_streak))
    app.add_handler(CommandHandler("pb", cmd_pb))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_text))

    logging.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
