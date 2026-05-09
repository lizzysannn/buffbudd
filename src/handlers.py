"""Telegram message and command handlers."""
import io
from telegram import Update
from telegram.ext import ContextTypes
from src import sheets, claude_ai
from src.config import (
    DEFAULT_CALORIES, DEFAULT_PROTEIN, DEFAULT_CARBS, DEFAULT_FATS,
    TELEGRAM_CHAT_ID,
)


def _is_authorised(update: Update) -> bool:
    return update.effective_chat.id == TELEGRAM_CHAT_ID


async def _deny(update: Update):
    await update.message.reply_text("Unauthorised.")


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    await update.message.reply_text(
        "Coach bot online.\n\n"
        "Send a *meal photo* to log food.\n"
        "Send gym text like `Bench 80kg 4x5 RPE 8` to log a set.\n"
        "Reply to sleep check-in with `7 4` (hours quality).\n\n"
        "Commands: /summary /week /goals /setgoals /recovery /streak /pb",
        parse_mode="Markdown",
    )


# ── Food photo ────────────────────────────────────────────────────────────────

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    await update.message.reply_text("Analysing meal...")
    try:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        macros = claude_ai.analyse_food_photo(image_bytes)
        sheets.log_food(
            macros["description"],
            macros["calories"],
            macros["protein"],
            macros["carbs"],
            macros["fats"],
        )
        totals = sheets.get_today_totals()

        msg = (
            f"*{macros['description']}* logged.\n"
            f"Cal: {macros['calories']} | P: {macros['protein']}g | "
            f"C: {macros['carbs']}g | F: {macros['fats']}g\n"
            f"_{macros['note']}_\n\n"
            f"Today so far: {totals['calories']} cal / {DEFAULT_CALORIES} | "
            f"Protein: {totals['protein']:.0f}g / {DEFAULT_PROTEIN}g"
        )
        if totals["protein"] < DEFAULT_PROTEIN * 0.5 and totals["meals"] >= 2:
            msg += "\n\n*Protein is low — prioritise it in your next meal.*"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        await update.message.reply_text(f"Error analysing photo:\n`{err[-800:]}`", parse_mode="Markdown")


# ── Gym text ──────────────────────────────────────────────────────────────────

async def handle_gym_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    text = update.message.text.strip()
    parsed = claude_ai.parse_gym_entry(text)
    last = sheets.get_last_session(parsed["exercise"])
    pb = sheets.get_pb(parsed["exercise"])

    sheets.log_gym(
        parsed["exercise"],
        parsed["sets"],
        parsed["reps"],
        parsed["weight"],
        parsed["rpe"],
        parsed["notes"],
    )

    lines = [
        f"*{parsed['exercise']}* — {parsed['sets']}x{parsed['reps']} @ {parsed['weight']}kg"
    ]
    if parsed["rpe"]:
        lines[0] += f" RPE {parsed['rpe']}"
    if last:
        last_vol = float(last.get("Sets", 0)) * float(last.get("Reps", 0)) * float(last.get("Weight", 0))
        cur_vol = parsed["sets"] * parsed["reps"] * parsed["weight"]
        delta = cur_vol - last_vol
        lines.append(f"Volume vs last: {'+' if delta >= 0 else ''}{delta:.0f} kg")
    if pb and parsed["weight"] > float(pb.get("Weight", 0)):
        lines.append("NEW PB! Keep that form tight.")
    if parsed["notes"]:
        lines.append(f"_{parsed['notes']}_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Sleep check-in reply ──────────────────────────────────────────────────────

async def handle_sleep_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Expects message text like '7 4' (hours quality)."""
    if not _is_authorised(update):
        return await _deny(update)
    parts = update.message.text.strip().split()
    if len(parts) != 2:
        await update.message.reply_text("Format: `hours quality` e.g. `7 4`", parse_mode="Markdown")
        return
    try:
        hours, quality = float(parts[0]), int(parts[1])
    except ValueError:
        await update.message.reply_text("Couldn't parse. Format: `7 4`", parse_mode="Markdown")
        return
    if not (1 <= quality <= 5):
        await update.message.reply_text("Quality must be 1-5.", parse_mode="Markdown")
        return

    sheets.log_sleep(hours, quality)
    streak = sheets.get_sleep_streak()

    recovery = "High" if hours >= 7 and quality >= 4 else "Medium" if hours >= 6 and quality >= 3 else "Low"
    msg = f"Sleep logged: {hours}h, quality {quality}/5.\nRecovery: *{recovery}*"
    if streak >= 3:
        msg += f"\n{streak}-day sleep streak. Consistent rest = consistent gains."
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    totals = sheets.get_today_totals()
    sleep = sheets.get_today_sleep()
    gym = sheets.get_today_gym()

    sleep_str = f"{sleep['Hours']}h quality {sleep['Quality']}/5" if sleep else "Not logged"
    gym_str = f"{len(gym)} sets logged" if gym else "Rest day"

    msg = (
        "*Today's Summary*\n"
        f"Calories: {totals['calories']} / {DEFAULT_CALORIES}\n"
        f"Protein: {totals['protein']:.0f}g / {DEFAULT_PROTEIN}g\n"
        f"Carbs: {totals['carbs']:.0f}g / {DEFAULT_CARBS}g\n"
        f"Fats: {totals['fats']:.0f}g / {DEFAULT_FATS}g\n"
        f"Sleep: {sleep_str}\n"
        f"Training: {gym_str}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    food = sheets.get_week_food()
    gym = sheets.get_week_gym()

    if not food:
        await update.message.reply_text("No food logged this week yet.")
        return

    avg_cal = sum(int(r.get("Calories", 0)) for r in food) / max(len(food), 1)
    avg_pro = sum(float(r.get("Protein", 0)) for r in food) / max(len(food), 1)
    unique_days = len({r.get("Date") for r in gym})

    msg = (
        "*This Week*\n"
        f"Avg calories/meal: {avg_cal:.0f}\n"
        f"Avg protein/meal: {avg_pro:.0f}g\n"
        f"Meals logged: {len(food)}\n"
        f"Training days: {unique_days}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_goals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    goals = sheets.get_weekly_goals()
    await update.message.reply_text(f"*Weekly Goals*\n{goals}", parse_mode="Markdown")


async def cmd_setgoals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    await update.message.reply_text(
        "Update your WeeklyGoals Google Doc directly, then use /goals to verify."
    )


async def cmd_recovery(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    sleep = sheets.get_today_sleep()
    if not sleep:
        await update.message.reply_text("No sleep logged today. Log it with the morning check-in.")
        return
    hours, quality = float(sleep["Hours"]), int(sleep["Quality"])
    recovery = "High" if hours >= 7 and quality >= 4 else "Medium" if hours >= 6 and quality >= 3 else "Low"
    await update.message.reply_text(f"Recovery status: *{recovery}* ({hours}h, quality {quality}/5)", parse_mode="Markdown")


async def cmd_streak(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    streak = sheets.get_sleep_streak()
    await update.message.reply_text(f"Sleep streak (7h+ nights): *{streak} days*", parse_mode="Markdown")


async def cmd_pb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: `/pb Bench Press`", parse_mode="Markdown")
        return
    exercise = " ".join(args)
    pb = sheets.get_pb(exercise)
    if not pb:
        await update.message.reply_text(f"No records found for {exercise}.")
        return
    await update.message.reply_text(
        f"*{exercise} PB*\n"
        f"{pb.get('Weight')}kg — {pb.get('Sets')}x{pb.get('Reps')} on {pb.get('Date')}",
        parse_mode="Markdown",
    )
