"""Telegram message and command handlers — Buff Buddy."""
import asyncio
import io
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from src import sheets, claude_ai
from src.config import (
    DEFAULT_CALORIES, DEFAULT_PROTEIN, DEFAULT_CARBS, DEFAULT_FATS,
    TELEGRAM_CHAT_ID,
)

SILENCE_TIMEOUT = 8  # seconds before "is that everything?" prompt


def _is_authorised(update: Update) -> bool:
    return update.effective_chat.id == TELEGRAM_CHAT_ID


async def _deny(update: Update):
    await update.message.reply_text("Unauthorised.")


# ── Session state helpers ─────────────────────────────────────────────────────

def _open_session(ctx, intent: str, first_message: str):
    ctx.user_data["session_intent"] = intent
    ctx.user_data["session_messages"] = [first_message]
    ctx.user_data["session_task"] = None


def _append_session(ctx, text: str):
    ctx.user_data.setdefault("session_messages", []).append(text)


def _close_session(ctx):
    intent = ctx.user_data.pop("session_intent", None)
    messages = ctx.user_data.pop("session_messages", [])
    ctx.user_data.pop("session_task", None)
    ctx.user_data.pop("gym_exercises", None)
    ctx.user_data.pop("awaiting_gym_results", None)
    return intent, messages


def _active_session(ctx) -> str | None:
    return ctx.user_data.get("session_intent")


# ── Silence timer ─────────────────────────────────────────────────────────────

async def _silence_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(SILENCE_TIMEOUT)
    if not _active_session(ctx):
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, log it", callback_data="session_done"),
        InlineKeyboardButton("➕ Add more", callback_data="session_more"),
    ]])
    await update.message.reply_text("That everything for this one?", reply_markup=keyboard)


def _schedule_silence(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Cancel existing timer
    old_task = ctx.user_data.get("session_task")
    if old_task and not old_task.done():
        old_task.cancel()
    task = asyncio.create_task(_silence_check(update, ctx))
    ctx.user_data["session_task"] = task


# ── Intent disambiguation buttons ─────────────────────────────────────────────

async def _ask_intent(update: Update, text: str):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🍱 Meal", callback_data="intent_meal"),
        InlineKeyboardButton("🏋️ Gym", callback_data="intent_gym"),
    ], [
        InlineKeyboardButton("😴 Recovery", callback_data="intent_recovery"),
        InlineKeyboardButton("💬 Emotions", callback_data="intent_emotions"),
    ]])
    await update.message.reply_text("What are we logging?", reply_markup=keyboard)


# ── Callback query handler ────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "session_done":
        await _finalise_session(update, ctx, via_button=True)

    elif data == "session_more":
        old_task = ctx.user_data.get("session_task")
        if old_task and not old_task.done():
            old_task.cancel()
        await query.edit_message_text("Go ahead, add more.")

    elif data.startswith("intent_"):
        intent = data.replace("intent_", "")
        messages = ctx.user_data.get("pending_messages", [])
        if messages:
            _open_session(ctx, intent, " ".join(messages))
            ctx.user_data.pop("pending_messages", None)
            _schedule_silence(update, ctx)
            await query.edit_message_text(f"Got it — logging as {intent}. Anything to add?")


# ── Finalise and log session ───────────────────────────────────────────────────

async def _finalise_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE, via_button: bool = False):
    intent, messages = _close_session(ctx)
    combined = " ".join(messages)

    if via_button:
        target = update.callback_query.message
        reply = target.reply_text
    else:
        reply = update.message.reply_text

    if intent == "meal":
        await _log_meal_text(combined, reply)
    elif intent == "gym":
        await _log_gym_session(combined, ctx, reply)
    elif intent == "recovery":
        await _log_recovery(combined, reply)
    elif intent == "emotions":
        await _log_emotions(combined, reply)
    elif intent == "period":
        bot = update.get_bot() if not via_button else update.callback_query.get_bot()
        chat_id = TELEGRAM_CHAT_ID
        await _log_period(combined, reply, bot=bot, chat_id=chat_id)
    else:
        await reply("Couldn't figure out what to log. Try again.")


# ── Meal logging ──────────────────────────────────────────────────────────────

async def _log_meal_text(text: str, reply):
    try:
        macros = claude_ai.analyse_food_text(text)
        if macros["calories"] == 0 and macros["protein"] == 0:
            await reply("No food found in that. Use GYM for gym, SLEEP for recovery.")
            return
        sheets.log_food(macros["description"], macros["calories"], macros["protein"], macros["carbs"], macros["fats"])
        totals = sheets.get_today_totals()
        msg = (
            f"*{macros['description']}*\n"
            f"Fuelled. {macros['calories']} cal · {macros['protein']}g protein · {macros['carbs']}g carbs · {macros['fats']}g fat\n"
            f"_{macros['note']}_\n\n"
            f"Today: {totals['calories']} / {DEFAULT_CALORIES} cal · Protein {totals['protein']:.0f} / {DEFAULT_PROTEIN}g"
        )
        if totals["protein"] < DEFAULT_PROTEIN * 0.5 and totals["meals"] >= 2:
            msg += "\n\nProtein's low, Liz. Next meal, prioritise it."
        await reply(msg, parse_mode="Markdown")
    except Exception as e:
        import traceback
        await reply(f"Error: `{traceback.format_exc()[-600:]}`", parse_mode="Markdown")


# ── Gym logging ───────────────────────────────────────────────────────────────

async def _log_gym_session(text: str, ctx, reply):
    try:
        exercise_list = ctx.user_data.get("gym_exercises", [])
        has_pr = False
        lines = []

        if exercise_list:
            results = claude_ai.parse_session_results(exercise_list, text)
            for r in results:
                if r.get("skipped"):
                    lines.append(f"{r['number']}. {r['exercise']} — skipped")
                    continue
                sheets.log_gym(r["exercise"], r["sets"], r["reps"], r["weight_kg"], r.get("rpe"), r.get("notes", ""))
                last = sheets.get_last_session(r["exercise"])
                entry = f"{r['number']}. {r['exercise']} — {r['weight_kg']}kg {r['sets']}x{r['reps']}"
                if r.get("rpe"):
                    entry += f" RPE {r['rpe']}"
                if last and float(last.get("Weight", 0)) > 0:
                    prev = float(last.get("Weight", 0))
                    if r["weight_kg"] > prev:
                        entry += f" 🔺 +{r['weight_kg'] - prev}kg"
                        has_pr = True
                    elif r["weight_kg"] < prev:
                        entry += f" ↓ was {prev}kg"
                lines.append(entry)
        else:
            parsed = claude_ai.parse_gym_entry(text)
            sheets.log_gym(parsed["exercise"], parsed["sets"], parsed["reps"], parsed["weight"], parsed["rpe"], parsed["notes"])
            last = sheets.get_last_session(parsed["exercise"])
            pb = sheets.get_pb(parsed["exercise"])
            entry = f"{parsed['exercise']} — {parsed['weight']}kg {parsed['sets']}x{parsed['reps']}"
            if parsed["rpe"]:
                entry += f" RPE {parsed['rpe']}"
            if pb and parsed["weight"] > float(pb.get("Weight", 0)):
                entry += " 🔺 PR!"
                has_pr = True
            lines.append(entry)

        buddy_reply = claude_ai.gym_session_reply(lines, has_pr)
        session_summary = "\n".join(lines)
        await reply(f"{session_summary}\n\n{buddy_reply}", parse_mode="Markdown")
    except Exception as e:
        import traceback
        await reply(f"Error: `{traceback.format_exc()[-600:]}`", parse_mode="Markdown")


# ── Recovery logging ──────────────────────────────────────────────────────────

async def _log_recovery(text: str, reply):
    try:
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if len(numbers) >= 2:
            hours, quality = float(numbers[0]), int(float(numbers[1]))
        elif len(numbers) == 1:
            hours, quality = float(numbers[0]), 3
        else:
            await reply("How many hours and quality? e.g. `7.5 4`", parse_mode="Markdown")
            return
        quality = max(1, min(5, quality))
        sheets.log_sleep(hours, quality)
        streak = sheets.get_sleep_streak()
        buddy_reply = claude_ai.recovery_reply(hours, quality, streak)
        await reply(buddy_reply)
    except Exception as e:
        import traceback
        await reply(f"Error: `{traceback.format_exc()[-400:]}`", parse_mode="Markdown")


# ── Emotions logging ──────────────────────────────────────────────────────────

async def _log_emotions(text: str, reply):
    try:
        parsed = claude_ai.parse_emotions(text)
        mood, energy, notes = parsed["mood"], parsed["energy"], parsed["notes"]
        sheets.log_emotions(mood, energy, notes)
        cycle_day, phase = sheets.get_cycle_info()
        buddy_reply = claude_ai.emotions_reply(mood, energy, notes, cycle_day, phase)
        await reply(buddy_reply)
    except Exception as e:
        import traceback
        await reply(f"Error: `{traceback.format_exc()[-400:]}`", parse_mode="Markdown")


# ── Period logging ────────────────────────────────────────────────────────────

async def _log_period(text: str, reply, bot=None, chat_id=None):
    try:
        had_previous = sheets.get_last_period_start() is not None
        sheets.log_period_start()
        await reply(
            "Logged. Day 1. Recovery mission starts now, Lizzy.\n"
            "Cycle tracker's running — mood and energy track against your phase automatically."
        )
        # Trigger cycle summary if there was a previous cycle
        if had_previous and bot and chat_id:
            from src.scheduler import send_cycle_summary
            asyncio.create_task(send_cycle_summary(bot))
    except Exception as e:
        await reply(f"Error logging period: {e}")


# ── Main message dispatcher ───────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)

    text = (update.message.text or "").strip()

    # Mid-session: append message, reset silence timer
    if _active_session(ctx):
        _append_session(ctx, text)
        _schedule_silence(update, ctx)
        return

    # Classify intent
    from src.router import classify_intent
    intent = classify_intent(text)

    if intent == "unknown":
        await _ask_intent(update, text)
        ctx.user_data["pending_messages"] = [text]
        return

    # Special gym flow: show exercise list first
    if intent == "gym":
        try:
            doc_text = sheets.get_coach_training()
            exercises = sheets.parse_exercise_list(doc_text)
            if exercises:
                ctx.user_data["gym_exercises"] = exercises
                lines = ["*Game time. Today's session:*"]
                for i, ex in enumerate(exercises, 1):
                    lines.append(f"{i}. {ex}")
                lines.append("\nLog results — one line per exercise or all at once:")
                lines.append("`1 - 15kg 3x8` · `2 - 80kg 4x5 RPE 7` · `3 - skip`")
                _open_session(ctx, "gym", "")
                _schedule_silence(update, ctx)
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
                return
        except Exception:
            pass  # Fall through to free-text gym logging

    _open_session(ctx, intent, text)
    _schedule_silence(update, ctx)


# ── Photo handler ─────────────────────────────────────────────────────────────

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
        sheets.log_food(macros["description"], macros["calories"], macros["protein"], macros["carbs"], macros["fats"])
        totals = sheets.get_today_totals()
        msg = (
            f"*{macros['description']}*\n"
            f"Fuelled. {macros['calories']} cal · {macros['protein']}g protein · {macros['carbs']}g carbs · {macros['fats']}g fat\n"
            f"_{macros['note']}_\n\n"
            f"Today: {totals['calories']} / {DEFAULT_CALORIES} cal · Protein {totals['protein']:.0f} / {DEFAULT_PROTEIN}g"
        )
        if totals["protein"] < DEFAULT_PROTEIN * 0.5 and totals["meals"] >= 2:
            msg += "\n\nProtein's low, Liz. Prioritise it next meal."
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        import traceback
        await update.message.reply_text(f"Error: `{traceback.format_exc()[-600:]}`", parse_mode="Markdown")


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    await update.message.reply_text(
        "Buff Buddy online. Game time.\n\n"
        "Just talk to me naturally:\n"
        "• Describe food → I'll log macros\n"
        "• Say `GYM` → I'll pull your exercise list\n"
        "• Tell me how you slept → logged\n"
        "• Tell me how you feel → logged\n"
        "• Say you got your period → cycle tracker starts\n\n"
        "Commands: /summary /week /goals /recovery /streak /pb",
        parse_mode="Markdown",
    )


async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    totals = sheets.get_today_totals()
    sleep = sheets.get_today_sleep()
    gym = sheets.get_today_gym()
    cycle_day, phase = sheets.get_cycle_info()

    sleep_str = f"{sleep['Hours']}h quality {sleep['Quality']}/5" if sleep else "Not logged"
    gym_str = f"{len(gym)} sets" if gym else "Rest day"
    cycle_str = f"Day {cycle_day} · {phase}" if cycle_day else "—"

    msg = (
        "*The Scoreboard*\n"
        f"Calories: {totals['calories']} / {DEFAULT_CALORIES}\n"
        f"Protein: {totals['protein']:.0f}g / {DEFAULT_PROTEIN}g\n"
        f"Carbs: {totals['carbs']:.0f}g · Fats: {totals['fats']:.0f}g\n"
        f"Sleep: {sleep_str}\n"
        f"Training: {gym_str}\n"
        f"Cycle: {cycle_str}"
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
    days_food = len({r.get("Date") for r in food})
    avg_cal = sum(int(r.get("Calories", 0)) for r in food) / max(days_food, 1)
    avg_pro = sum(float(r.get("Protein", 0)) for r in food) / max(days_food, 1)
    gym_days = len({r.get("Date") for r in gym})
    msg = (
        "*This Week*\n"
        f"Avg calories/day: {avg_cal:.0f}\n"
        f"Avg protein/day: {avg_pro:.0f}g\n"
        f"Food logging days: {days_food}\n"
        f"Training days: {gym_days}"
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
    await update.message.reply_text("Update your WeeklyGoals Google Doc directly, then use /goals to verify.")


async def cmd_recovery(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    sleep = sheets.get_today_sleep()
    if not sleep:
        await update.message.reply_text("No sleep logged today.")
        return
    h, q = float(sleep["Hours"]), int(sleep["Quality"])
    recovery = "High" if h >= 7 and q >= 4 else "Medium" if h >= 6 and q >= 3 else "Low"
    await update.message.reply_text(f"Recovery: *{recovery}* — {h}h, quality {q}/5", parse_mode="Markdown")


async def cmd_streak(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    streak = sheets.get_sleep_streak()
    await update.message.reply_text(f"Sleep streak: *{streak} nights* of 7h+", parse_mode="Markdown")


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
        await update.message.reply_text(f"No records for {exercise} yet.")
        return
    await update.message.reply_text(
        f"*{exercise} PB*\n{pb.get('Weight')}kg — {pb.get('Sets')}x{pb.get('Reps')} on {pb.get('Date')}",
        parse_mode="Markdown",
    )
