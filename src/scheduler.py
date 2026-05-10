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
    from datetime import date as _date
    SUGAR_TARGET = 25.0

    totals   = sheets.get_today_totals()
    sleep    = sheets.get_today_sleep()
    gym      = sheets.get_today_gym()
    emotions = sheets.get_today_emotions()
    body     = sheets.get_body_by_date(_date.today().isoformat())

    today_str = _date.today().strftime("%a, %d %b")
    lines = [f"*Evening Check-in — {today_str}*\n"]

    # ── Nutrition ─────────────────────────────────────────────────────────────
    cal  = totals["calories"]
    pro  = totals["protein"]
    carb = totals["carbs"]
    fat  = totals["fats"]
    sug  = totals["sugar"]
    cal_pct = int(cal / DEFAULT_CALORIES * 100)
    pro_pct = int(pro / DEFAULT_PROTEIN * 100)

    cal_gap = DEFAULT_CALORIES - cal
    cal_status = f"↓{abs(int(cal_gap))} under" if cal_gap > 0 else f"↑{abs(int(cal_gap))} over"
    pro_status = "✅" if pro >= DEFAULT_PROTEIN else f"↓{abs(int(DEFAULT_PROTEIN - pro))}g short"
    sug_status = "✅" if sug <= SUGAR_TARGET else f"⚠️ +{sug - SUGAR_TARGET:.0f}g over"

    lines.append("🍱 *Nutrition*")
    lines.append(f"Calories: {cal} / {DEFAULT_CALORIES} ({cal_pct}%) · {cal_status}")
    lines.append(f"Protein:  {pro:.0f}g / {DEFAULT_PROTEIN}g ({pro_pct}%) · {pro_status}")
    lines.append(f"Carbs: {carb:.0f}g · Fats: {fat:.0f}g · Sugar: {sug:.0f}g {sug_status}")
    if totals["meals"] == 0:
        lines.append("_Nothing logged today_")
    lines.append("")

    # ── Training ──────────────────────────────────────────────────────────────
    lines.append("🏋️ *Training*")
    if gym:
        strength = [r for r in gym if str(r.get("Type", "strength")).lower() != "cardio"]
        cardio   = [r for r in gym if str(r.get("Type", "")).lower() == "cardio"]
        exercises = list({str(r.get("Exercise", "")) for r in strength if r.get("Exercise")})
        if exercises:
            lines.append(f"{len(strength)} strength sets — " + ", ".join(exercises))
        if cardio:
            total_min = sum(int(r.get("Duration (min)", 0) or 0) for r in cardio)
            cardio_names = list({str(r.get("Exercise", "Cardio")) for r in cardio})
            lines.append(f"{total_min}min cardio — " + ", ".join(cardio_names))
    else:
        lines.append("Rest day")
    lines.append("")

    # ── Recovery ──────────────────────────────────────────────────────────────
    lines.append("😴 *Recovery*")
    if sleep:
        h = float(sleep.get("Hours", 0))
        notes = (sleep.get("Notes") or sleep.get("Quality") or "").strip()
        recovery_label = "High 🟢" if h >= 7 else "Medium 🟡" if h >= 6 else "Low 🔴"
        lines.append(f"Sleep: {h}h · {recovery_label}" + (f" · _{notes}_" if notes else ""))
    else:
        lines.append("Sleep: not logged")
    lines.append("")

    # ── Mood / Emotions ───────────────────────────────────────────────────────
    lines.append("💬 *Mood*")
    if emotions:
        mood   = emotions.get("Mood", "")
        energy = emotions.get("Energy", "")
        notes  = str(emotions.get("Notes", "")).strip()
        parts = []
        if mood:   parts.append(f"Mood {mood}/10")
        if energy: parts.append(f"Energy {energy}/10")
        lines.append(" · ".join(parts) + (f" · _{notes}_" if notes else ""))
    else:
        lines.append("Not logged")
    lines.append("")

    # ── Body check-in ─────────────────────────────────────────────────────────
    lines.append("⚖️ *Body*")
    if body and (body.get("Weight (kg)") or body.get("Body Feel")):
        w    = body.get("Weight (kg)", "")
        bf   = body.get("Body Fat (%)", "")
        tags = body.get("Body Feel", "")
        parts = []
        if w:    parts.append(f"{w}kg")
        if bf:   parts.append(f"BF {bf}%")
        if tags: parts.append(f"_{tags}_")
        lines.append(" · ".join(parts))
    else:
        lines.append("Not logged")
    lines.append("")

    # ── What's missing + coaching note ────────────────────────────────────────
    missing = []
    if not sleep:    missing.append("sleep")
    if not emotions: missing.append("mood")
    if not body:     missing.append("morning weight / body feel")

    sleep_str_ctx  = f"{float(sleep.get('Hours', 0))}h" if sleep else "not logged"
    mood_str_ctx   = f"{emotions.get('Mood')}/10" if emotions else "not logged"
    weight_str_ctx = str(body.get("Weight (kg)", "not logged")) if body else "not logged"
    context_for_claude = (
        f"Calories: {cal} / {DEFAULT_CALORIES} ({cal_pct}%)\n"
        f"Protein: {pro:.0f}g / {DEFAULT_PROTEIN}g ({pro_pct}%)\n"
        f"Carbs: {carb:.0f}g, Fats: {fat:.0f}g, Sugar: {sug:.0f}g\n"
        f"Training: {'rest day' if not gym else f'{len(gym)} sets'}\n"
        f"Sleep: {sleep_str_ctx}\n"
        f"Mood: {mood_str_ctx}\n"
        f"Body weight: {weight_str_ctx}"
    )
    try:
        note = claude_ai.generate_daily_summary_note(context_for_claude, missing)
    except Exception:
        note = "Day's logged. Keep going."

    lines.append(f"_{note}_")

    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="\n".join(lines), parse_mode="Markdown")


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

    # ── Gym + Cardio ──────────────────────────────────────────────────────────
    from src.config import DEFAULT_GYM_SESSIONS_WEEK, DEFAULT_CARDIO_SESSIONS_WEEK, DEFAULT_CARDIO_MIN
    from collections import defaultdict
    gym_days = len({r.get("Date") for r in gym if r.get("Date")})
    gym_hit = gym_days >= DEFAULT_GYM_SESSIONS_WEEK

    # Count cardio sessions ≥ DEFAULT_CARDIO_MIN minutes
    day_cardio: dict[str, int] = defaultdict(int)
    for r in gym:
        if str(r.get("Type", "")).lower() == "cardio":
            try:
                day_cardio[r.get("Date", "")] += int(r.get("Duration (min)", 0) or 0)
            except (ValueError, TypeError):
                pass
    cardio_sessions = sum(1 for d in day_cardio.values() if d >= DEFAULT_CARDIO_MIN)
    cardio_hit = cardio_sessions >= DEFAULT_CARDIO_SESSIONS_WEEK

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
    cal_status     = "✅" if avg_cal <= DEFAULT_CALORIES else f"⚠️ over by {avg_cal - DEFAULT_CALORIES:.0f}"
    pro_status     = "✅" if avg_pro >= DEFAULT_PROTEIN else f"❌ avg {avg_pro:.0f}g"
    sugar_status   = "✅" if avg_sugar <= SUGAR_TARGET else f"⚠️ avg {avg_sugar:.1f}g"
    gym_status     = "✅" if gym_hit else f"❌ {gym_days}/{DEFAULT_GYM_SESSIONS_WEEK}"
    cardio_status  = "✅" if cardio_hit else f"❌ {cardio_sessions}/{DEFAULT_CARDIO_SESSIONS_WEEK}"

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
        f"🏃 Cardio: {cardio_sessions} / {DEFAULT_CARDIO_SESSIONS_WEEK} sessions (≥{DEFAULT_CARDIO_MIN}min) {cardio_status}\n"
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
