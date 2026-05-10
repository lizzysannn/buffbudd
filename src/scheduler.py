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
        CronTrigger(day_of_week="mon", hour=12, minute=0, timezone=TIMEZONE),
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

    sleep_notes = (sleep.get("Notes") or sleep.get("Quality") or "").strip() if sleep else ""
    sleep_str = f"{sleep['Hours']}h" + (f" · {sleep_notes}" if sleep_notes else "") if sleep else "Not logged"
    gym_str = f"{len(gym)} sets" if gym else "Rest day"
    recovery = "—"
    if sleep:
        h = float(sleep["Hours"])
        recovery = "High" if h >= 7 else "Medium" if h >= 6 else "Low"

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
    """Runs Monday 12pm — summarises the previous Mon–Sun week."""
    from datetime import date, timedelta
    today = date.today()  # Monday
    prev_mon = today - timedelta(days=7)
    prev_sun = today - timedelta(days=1)
    week_label = f"{prev_mon.strftime('%d %b')} – {prev_sun.strftime('%d %b')}"

    food  = sheets.get_prev_week_food()
    gym   = sheets.get_prev_week_gym()
    sleep = sheets.get_prev_week_sleep()
    body  = sheets.get_prev_week_body()

    # ── Nutrition ─────────────────────────────────────────────────────────────
    days_with_food = len({r.get("Date") for r in food}) or 1
    avg_cal   = sum(int(r.get("Calories", 0)) for r in food) / days_with_food
    avg_pro   = sum(float(r.get("Protein", 0)) for r in food) / days_with_food
    avg_carb  = sum(float(r.get("Carbs", 0)) for r in food) / days_with_food
    avg_fat   = sum(float(r.get("Fats", 0)) for r in food) / days_with_food
    avg_sugar = sum(float(r.get("Sugar (g)", 0)) for r in food) / days_with_food
    SUGAR_TARGET = 25.0

    # ── Gym ───────────────────────────────────────────────────────────────────
    from src.config import DEFAULT_GYM_SESSIONS_WEEK
    gym_days = len({r.get("Date") for r in gym if r.get("Date")})
    gym_hit = gym_days >= DEFAULT_GYM_SESSIONS_WEEK

    # ── Sleep ─────────────────────────────────────────────────────────────────
    sleep_hours = [float(r.get("Hours", 0)) for r in sleep if r.get("Hours")]
    avg_sleep = sum(sleep_hours) / len(sleep_hours) if sleep_hours else 0
    nights_7h = sum(1 for h in sleep_hours if h >= 7)

    # ── Body / Weight ─────────────────────────────────────────────────────────
    weight_rows = [r for r in body if r.get("Weight (kg)")]
    weight_start = float(weight_rows[0]["Weight (kg)"]) if weight_rows else None
    weight_end   = float(weight_rows[-1]["Weight (kg)"]) if weight_rows else None
    weight_change = round(weight_end - weight_start, 1) if (weight_start and weight_end) else None

    bf_rows = [r for r in body if r.get("Body Fat (%)")]
    bf_start = float(bf_rows[0]["Body Fat (%)"]) if bf_rows else None
    bf_end   = float(bf_rows[-1]["Body Fat (%)"]) if bf_rows else None

    sm_rows = [r for r in body if r.get("Skeletal Muscle (kg)")]
    sm_end = float(sm_rows[-1]["Skeletal Muscle (kg)"]) if sm_rows else None

    # Body feel tags frequency
    all_tags = []
    for r in body:
        tags_str = str(r.get("Body Feel", "")).strip()
        if tags_str:
            all_tags.extend([t.strip() for t in tags_str.split(",") if t.strip()])
    tag_counts: dict[str, int] = {}
    for t in all_tags:
        tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:3]

    # ── Goal score via Claude ─────────────────────────────────────────────────
    week_data = (
        f"Avg daily calories: {avg_cal:.0f} (target {DEFAULT_CALORIES})\n"
        f"Avg daily protein: {avg_pro:.0f}g (target {DEFAULT_PROTEIN}g)\n"
        f"Avg daily sugar: {avg_sugar:.1f}g (target <{SUGAR_TARGET}g)\n"
        f"Gym sessions: {gym_days} (target {DEFAULT_GYM_SESSIONS_WEEK})\n"
        f"Avg sleep: {avg_sleep:.1f}h · Nights ≥7h: {nights_7h}/7\n"
        f"Food logged: {days_with_food} days"
    )
    try:
        goals_text = sheets.get_weekly_goals()
        score_report = claude_ai.score_weekly_goals(goals_text, week_data)
    except Exception:
        score_report = "Goal scoring unavailable this week."

    # ── Log to Weekly Summary sheet ───────────────────────────────────────────
    sheets.log_weekly_summary({
        "week_start": prev_mon.strftime("%Y-%m-%d"),
        "avg_calories": f"{avg_cal:.0f}",
        "avg_protein": f"{avg_pro:.0f}",
        "gym_sessions": gym_days,
        "avg_sleep": f"{avg_sleep:.1f}",
        "goal_score": "",
        "notes": score_report[:200],
        # Body columns
        "weight_start": f"{weight_start}" if weight_start else "",
        "weight_end": f"{weight_end}" if weight_end else "",
        "weight_change": f"{weight_change:+.1f}" if weight_change is not None else "",
        "bf_start": f"{bf_start}" if bf_start else "",
        "bf_end": f"{bf_end}" if bf_end else "",
        "skeletal_muscle": f"{sm_end}" if sm_end else "",
        "top_feel_tags": ", ".join(f"{t} ({c}x)" for t, c in top_tags) if top_tags else "",
    })

    # ── Send message ──────────────────────────────────────────────────────────
    cal_status  = "✅" if avg_cal <= DEFAULT_CALORIES else f"⚠️ over by {avg_cal - DEFAULT_CALORIES:.0f}"
    pro_status  = "✅" if avg_pro >= DEFAULT_PROTEIN else f"❌ avg {avg_pro:.0f}g"
    sugar_status = "✅" if avg_sugar <= SUGAR_TARGET else f"⚠️ avg {avg_sugar:.1f}g"
    gym_status  = "✅" if gym_hit else f"❌ {gym_days}/{DEFAULT_GYM_SESSIONS_WEEK}"

    # Build weight line
    if weight_start and weight_end and len(weight_rows) > 1:
        sign = "+" if weight_change >= 0 else ""
        weight_line = f"⚖️ Weight: {weight_start}kg → {weight_end}kg ({sign}{weight_change}kg)"
    elif weight_end:
        weight_line = f"⚖️ Weight: {weight_end}kg"
    else:
        weight_line = "⚖️ Weight: not logged this week"

    if bf_end:
        bf_change = f" · BF: {bf_start}% → {bf_end}%" if bf_start and bf_start != bf_end else f" · BF: {bf_end}%"
        weight_line += bf_change
    if sm_end:
        weight_line += f" · Muscle: {sm_end}kg"

    feel_line = ""
    if top_tags:
        feel_line = "\n🏷 Body feel: " + " · ".join(f"{t} ({c}x)" for t, c in top_tags)

    msg = (
        f"*Weekly Report — {week_label}*\n\n"
        f"🍱 Calories: {avg_cal:.0f} / {DEFAULT_CALORIES} {cal_status}\n"
        f"💪 Protein: {avg_pro:.0f}g / {DEFAULT_PROTEIN}g {pro_status}\n"
        f"🍬 Sugar: {avg_sugar:.1f}g / {SUGAR_TARGET:.0f}g {sugar_status}\n"
        f"🏋️ Gym: {gym_days} / {DEFAULT_GYM_SESSIONS_WEEK} sessions {gym_status}\n"
        f"😴 Sleep: avg {avg_sleep:.1f}h · {nights_7h}/7 nights ≥7h\n"
        f"{weight_line}{feel_line}\n\n"
        f"*Goal Review*\n_{score_report}_"
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
