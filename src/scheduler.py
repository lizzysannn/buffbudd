"""APScheduler jobs: 8am sleep check-in, 9pm daily summary, Sunday weekly report."""
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE
from src import sheets, claude_ai
from src.config import DEFAULT_CALORIES, DEFAULT_PROTEIN, DEFAULT_CARBS, DEFAULT_FATS


def build_scheduler(bot: Bot, event_loop: asyncio.AbstractEventLoop | None = None) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE, event_loop=event_loop)

    scheduler.add_job(
        lambda: asyncio.create_task(_sleep_checkin(bot)),
        CronTrigger(hour=8, minute=0, timezone=TIMEZONE),
        id="sleep_checkin",
    )
    scheduler.add_job(
        lambda: asyncio.create_task(_daily_summary(bot)),
        CronTrigger(hour=21, minute=0, timezone=TIMEZONE),
        id="daily_summary",
    )
    scheduler.add_job(
        lambda: asyncio.create_task(_weekly_report(bot)),
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=TIMEZONE),
        id="weekly_report",
    )
    return scheduler


async def _sleep_checkin(bot: Bot):
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=(
            "Good morning. How did you sleep?\n"
            "Reply with: `hours quality` (quality 1-5)\n"
            "Example: `7.5 4`"
        ),
        parse_mode="Markdown",
    )


async def _daily_summary(bot: Bot):
    totals = sheets.get_today_totals()
    sleep = sheets.get_today_sleep()
    gym = sheets.get_today_gym()

    sleep_str = f"{sleep['Hours']}h quality {sleep['Quality']}/5" if sleep else "Not logged"
    gym_str = f"{len(gym)} sets" if gym else "Rest day"
    recovery = "—"
    if sleep:
        h, q = float(sleep["Hours"]), int(sleep["Quality"])
        recovery = "High" if h >= 7 and q >= 4 else "Medium" if h >= 6 and q >= 3 else "Low"

    cal_pct = int(totals["calories"] / DEFAULT_CALORIES * 100)
    pro_pct = int(totals["protein"] / DEFAULT_PROTEIN * 100)

    context = (
        f"Calories: {totals['calories']} ({cal_pct}% of target)\n"
        f"Protein: {totals['protein']:.0f}g ({pro_pct}% of target)\n"
        f"Sleep: {sleep_str}, Recovery: {recovery}\n"
        f"Training: {gym_str}"
    )

    try:
        nutrition_doc = sheets.get_coach_nutrition()
        training_doc = sheets.get_coach_training()
        note = claude_ai.generate_coaching_note(context, nutrition_doc, training_doc)
    except Exception:
        note = "Keep showing up. Consistency is the strategy."

    protein_warn = ""
    if totals["protein"] < DEFAULT_PROTEIN * 0.7:
        protein_warn = f"\nProtein at {pro_pct}% — you need to hit your target."

    msg = (
        "*Evening Check-in*\n"
        f"Calories: {totals['calories']} / {DEFAULT_CALORIES} ({cal_pct}%)\n"
        f"Protein: {totals['protein']:.0f}g / {DEFAULT_PROTEIN}g ({pro_pct}%)\n"
        f"Sleep: {sleep_str}\n"
        f"Recovery: {recovery}\n"
        f"Training: {gym_str}"
        f"{protein_warn}\n\n"
        f"Coach: _{note}_"
    )
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")


async def _weekly_report(bot: Bot):
    food = sheets.get_week_food()
    gym = sheets.get_week_gym()
    goals_text = sheets.get_weekly_goals()

    days_with_food = len({r.get("Date") for r in food})
    avg_cal = sum(int(r.get("Calories", 0)) for r in food) / max(days_with_food, 1)
    avg_pro = sum(float(r.get("Protein", 0)) for r in food) / max(days_with_food, 1)
    gym_days = len({r.get("Date") for r in gym})

    week_data = (
        f"Avg daily calories: {avg_cal:.0f} (target {DEFAULT_CALORIES})\n"
        f"Avg daily protein: {avg_pro:.0f}g (target {DEFAULT_PROTEIN}g)\n"
        f"Training days: {gym_days}\n"
        f"Food logging days: {days_with_food}"
    )

    try:
        score_report = claude_ai.score_weekly_goals(goals_text, week_data)
    except Exception:
        score_report = "Goal scoring unavailable this week."

    sheets.log_weekly_summary({
        "week_start": _week_start(),
        "avg_calories": f"{avg_cal:.0f}",
        "avg_protein": f"{avg_pro:.0f}",
        "gym_sessions": gym_days,
        "avg_sleep": "",
        "goal_score": "",
        "notes": score_report[:200],
    })

    msg = (
        "*Weekly Report*\n\n"
        f"{week_data}\n\n"
        f"*Goal Review*\n{score_report}"
    )
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")


def _week_start() -> str:
    from datetime import date, timedelta
    today = date.today()
    return (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")


async def send_cycle_summary(bot: Bot):
    """Called when a new period is logged — summarise the previous cycle."""
    try:
        cycle_data = sheets.get_cycle_summary_data()
        if not cycle_data:
            return
        summary = claude_ai.generate_cycle_summary(cycle_data)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"*Monthly Cycle Summary*\n\n{summary}",
            parse_mode="Markdown",
        )
    except Exception as e:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"Cycle summary couldn't be generated: {e}",
        )
