"""Telegram message and command handlers — Buff Buddy."""
import asyncio
import io
import logging
import re
import traceback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)


def _safe_error(e: Exception, context: str = "") -> str:
    """Log full error privately, return a safe message for Telegram."""
    log.error(f"Error in {context}: {traceback.format_exc()}")
    return f"Something went wrong with {context or 'that'}. Check the logs."
from telegram.ext import ContextTypes, CallbackQueryHandler
from src import sheets, claude_ai
from src.config import (
    DEFAULT_CALORIES, DEFAULT_PROTEIN, DEFAULT_CARBS, DEFAULT_FATS,
    TELEGRAM_CHAT_ID,
)


def _is_authorised(update: Update) -> bool:
    return update.effective_chat.id == TELEGRAM_CHAT_ID


async def _deny(update: Update):
    await update.message.reply_text("Unauthorised.")


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

    if data.startswith("mealtype_"):
        meal_type = data.replace("mealtype_", "")
        macros = ctx.user_data.pop("pending_macros", None)
        if macros:
            sheets.log_food(macros["description"], macros["calories"], macros["protein"], macros["carbs"], macros["fats"], meal_type)
            totals = sheets.get_today_totals()
            msg = (
                f"*{macros['description']}* · _{meal_type.capitalize()}_\n"
                f"Fuelled. {macros['calories']} cal · {macros['protein']}g protein · {macros['carbs']}g carbs · {macros['fats']}g fat\n\n"
                f"Today: {totals['calories']} / {DEFAULT_CALORIES} cal · Protein {totals['protein']:.0f} / {DEFAULT_PROTEIN}g"
            )
            await query.edit_message_text(msg, parse_mode="Markdown")

    elif data == "food_correct":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("Logged ✅")

    elif data == "food_fix":
        await query.edit_message_reply_markup(reply_markup=None)
        ctx.user_data["awaiting_food_correction"] = True
        await query.message.reply_text(
            "What's wrong with it? Just tell me — e.g. `they're half-boiled eggs not fried, and add peanut butter on the bread`"
        )

    elif data == "food_delete":
        sheets.delete_last_food_row()
        ctx.user_data.pop("last_food", None)
        totals = sheets.get_today_totals()
        await query.edit_message_text(
            f"Deleted. Today: {totals['calories']} / {DEFAULT_CALORIES} cal · Protein {totals['protein']:.0f} / {DEFAULT_PROTEIN}g"
        )

    elif data == "exercise_confirm":
        ex = ctx.user_data.pop("pending_exercise", None)
        if ex:
            added = sheets.add_exercise_to_catalogue(ex["name"], ex["muscle_group"], ex["set"], ex["sets"], ex["notes"])
            if added:
                await query.edit_message_text(f"*{ex['name']}* added to catalogue. Mission logged.", parse_mode="Markdown")
            else:
                await query.edit_message_text(f"*{ex['name']}* is already in your catalogue.", parse_mode="Markdown")

    elif data == "exercise_cancel":
        ctx.user_data.pop("pending_exercise", None)
        await query.edit_message_text("Cancelled.")

    elif data == "set_confirm":
        pending = ctx.user_data.pop("pending_set", None)
        if pending:
            sheets.create_new_set(pending["set_name"], pending["exercises"])
            await query.edit_message_text(f"*{pending['set_name']}* created with {len(pending['exercises'])} exercises. Mission logged.", parse_mode="Markdown")

    elif data == "set_cancel":
        ctx.user_data.pop("pending_set", None)
        await query.edit_message_text("Cancelled.")

    elif data == "muscle_add_suggestions":
        suggestions = ctx.user_data.pop("muscle_suggestions", [])
        added = []
        for s in suggestions:
            sheets.add_exercise_to_catalogue(s["name"], s["muscle_group"], "optional", 3, s.get("notes", ""))
            added.append(s["name"])
        await query.edit_message_text(f"Added to catalogue: {', '.join(added)}. They'll show up next time you ask for extras.", parse_mode="Markdown")

    elif data == "muscle_skip":
        ctx.user_data.pop("muscle_suggestions", None)
        await query.edit_message_text("Got it — no changes to catalogue.")

    elif data.startswith("intent_"):
        intent = data.replace("intent_", "")
        messages = ctx.user_data.pop("pending_messages", [])
        combined = " ".join(messages).strip()
        await query.edit_message_text(f"Got it — logging as {intent}...")
        reply = query.message.reply_text
        if intent == "meal":
            await _log_meal_text(combined, reply, ctx=ctx)
        elif intent == "gym":
            await _show_gym_list(update, ctx)
        elif intent == "recovery":
            await _log_recovery(combined, reply)
        elif intent == "emotions":
            await _log_emotions(combined, reply)
        elif intent == "period":
            await _log_period(combined, reply, bot=ctx.bot, chat_id=TELEGRAM_CHAT_ID)


# ── Meal logging ──────────────────────────────────────────────────────────────

MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack", "supper"]


async def _log_meal_text(text: str, reply, ctx=None, meal_type: str = ""):
    try:
        macros = claude_ai.analyse_food_text(text)
        if macros["calories"] == 0 and macros["protein"] == 0:
            await reply("What did you eat exactly? Give me weights if you have them — e.g. `100g chicken, 150g rice, side salad`")
            if ctx:
                ctx.user_data["session_intent"] = "meal"
                ctx.user_data["session_messages"] = []
            return

        # Resolve meal type: explicit arg > Claude extraction > time-of-day
        resolved_type = meal_type or macros.get("meal_type") or sheets.infer_meal_type_from_time()

        # If meal type still unknown after all inference, ask with buttons
        if not resolved_type and ctx:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🌅 Breakfast", callback_data="mealtype_breakfast"),
                InlineKeyboardButton("☀️ Lunch", callback_data="mealtype_lunch"),
            ], [
                InlineKeyboardButton("🌙 Dinner", callback_data="mealtype_dinner"),
                InlineKeyboardButton("🍎 Snack", callback_data="mealtype_snack"),
                InlineKeyboardButton("🌃 Supper", callback_data="mealtype_supper"),
            ]])
            ctx.user_data["pending_macros"] = macros
            await reply("Which meal is this?", reply_markup=keyboard)
            return

        sheets.log_food(macros["description"], macros["calories"], macros["protein"], macros["carbs"], macros["fats"], resolved_type)
        totals = sheets.get_today_totals()
        if ctx:
            ctx.user_data["last_food"] = {"macros": macros, "meal_type": resolved_type}
        msg = _build_food_reply(macros, resolved_type, totals)
        await reply(msg, parse_mode="Markdown", reply_markup=_food_confirm_keyboard())
    except Exception as e:
        import traceback
        log.error(traceback.format_exc()); await reply(_safe_error(e, "meal logging"))


# ── Exercise catalogue handlers ───────────────────────────────────────────────

async def handle_add_exercise(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    text = update.message.text.strip()
    await update.message.reply_text("Looking that up...")
    try:
        parsed = claude_ai.parse_add_exercise_request(text)
        name = parsed.get("name", "")
        if not name:
            await update.message.reply_text("Couldn't figure out the exercise name. Try: `add Romanian Deadlift`", parse_mode="Markdown")
            return
        info = claude_ai.research_exercise(name)
        muscle = info.get("muscle_group", "Unknown")
        notes = info.get("notes", "")
        # Store pending confirmation in user_data
        ctx.user_data["pending_exercise"] = {
            "name": name,
            "muscle_group": muscle,
            "set": parsed.get("set", "optional"),
            "sets": parsed.get("sets", 3),
            "notes": notes,
        }
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Add it", callback_data="exercise_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="exercise_cancel"),
        ]])
        await update.message.reply_text(
            f"*{name}*\n"
            f"Hits: {muscle}\n"
            f"_{notes}_\n\n"
            f"Set: {parsed.get('set', 'optional')} · {parsed.get('sets', 3)} sets\n\n"
            "Add to catalogue?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except Exception as e:
        import traceback
        log.error(traceback.format_exc()); await update.message.reply_text(_safe_error(e))


async def handle_create_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    text = update.message.text.strip()
    await update.message.reply_text("Building your new set...")
    try:
        parsed = claude_ai.parse_create_set_request(text)
        set_name = parsed.get("set_name", "New Set")
        exercise_names = [e.get("name", "") for e in parsed.get("exercises", []) if e.get("name")]

        # Research each exercise
        enriched = []
        for name in exercise_names:
            info = claude_ai.research_exercise(name)
            enriched.append({
                "name": name,
                "muscle_group": info.get("muscle_group", "Unknown"),
                "sets": 3,
                "notes": info.get("notes", ""),
            })

        ctx.user_data["pending_set"] = {"set_name": set_name, "exercises": enriched}
        lines = [f"*New set: {set_name}*"]
        for ex in enriched:
            lines.append(f"• {ex['name']} — {ex['muscle_group']}")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Create it", callback_data="set_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="set_cancel"),
        ]])
        await update.message.reply_text(
            "\n".join(lines) + "\n\nCreate this set?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except Exception as e:
        import traceback
        log.error(traceback.format_exc()); await update.message.reply_text(_safe_error(e))


async def handle_target_muscle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    text = update.message.text.strip()
    await update.message.reply_text("Checking your catalogue...")
    try:
        muscle = claude_ai.parse_target_muscle_request(text)
        catalogue = sheets.get_exercise_catalogue()
        matches = sheets.get_exercises_by_muscle(muscle)

        lines = [f"*{muscle.capitalize()} exercises in your catalogue:*"]
        if matches:
            for ex in matches:
                last = ex.get("Last Weight (kg)", "")
                line = f"• {ex['Exercise Name']} ({ex['Set']})"
                if last:
                    line += f" — last {last}kg"
                lines.append(line)
        else:
            lines.append("Nothing in your catalogue yet for this muscle group.")

        # Suggest new exercises to add
        existing_names = [ex["Exercise Name"] for ex in catalogue]
        suggestions = claude_ai.suggest_new_exercises_for_muscle(muscle, existing_names)

        if suggestions:
            lines.append(f"\n*Not in your catalogue — want to add any?*")
            for s in suggestions:
                lines.append(f"• {s['name']} — _{s['notes']}_")

            # Store suggestions for quick-add
            ctx.user_data["muscle_suggestions"] = suggestions
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("➕ Add suggestions", callback_data="muscle_add_suggestions"),
                InlineKeyboardButton("Skip", callback_data="muscle_skip"),
            ]])
            await update.message.reply_text("\n".join(lines), reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        import traceback
        log.error(traceback.format_exc()); await update.message.reply_text(_safe_error(e))


# ── Gym logging ───────────────────────────────────────────────────────────────

async def _show_gym_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show the exercise list and set awaiting_gym_results state."""
    try:
        available_sets = sheets.get_available_sets()
        set_name = available_sets[0] if available_sets else "Self Train"
        exercises = sheets.get_exercises_by_set(set_name)
        if exercises:
            ctx.user_data["gym_exercises"] = exercises
            ctx.user_data["gym_set_name"] = set_name
            ctx.user_data["awaiting_gym_results"] = True
            lines = [f"*Game time. {set_name}:*"]
            for i, ex in enumerate(exercises, 1):
                name = ex.get("Exercise Name", "")
                muscle = ex.get("Muscle Group", "")
                last_w = ex.get("Last Weight (kg)", "")
                line = f"{i}. {name} — {muscle}"
                if last_w:
                    line += f" — last {last_w}kg"
                lines.append(line)
            lines.append(f"\n_{len(exercises)} exercises. Optimal is 5-8 total._")
            if len(exercises) < 8:
                lines.append("Want extras? Say `suggest something` or `I want to hit [muscle]`.")
            lines.append("\nLog results when ready — one per line or all at once:")
            lines.append("`1 - 15kg 3x8` · `2 - 80kg 4x5 RPE 7` · `3 - skip`")
            await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")
        else:
            ctx.user_data["awaiting_gym_results"] = True
            await update.effective_message.reply_text(
                "No exercises in your catalogue yet. Log your session — I'll parse it:\n"
                "`Bench Press 80kg 3x8, Squat 60kg 4x5`",
                parse_mode="Markdown",
            )
    except Exception as e:
        log.error(traceback.format_exc())
        ctx.user_data["awaiting_gym_results"] = True
        await update.effective_message.reply_text("Log your gym session:")


async def _log_gym_session(text: str, ctx, reply):
    try:
        exercise_list = ctx.user_data.get("gym_exercises", [])
        has_pr = False
        lines = []

        if exercise_list:
            # exercise_list may be dicts (from catalogue) or strings (legacy)
            ex_names = [
                e.get("Exercise Name", e) if isinstance(e, dict) else e
                for e in exercise_list
            ]
            results = claude_ai.parse_session_results(ex_names, text)
            for r in results:
                if r.get("skipped"):
                    lines.append(f"{r['number']}. {r['exercise']} — skipped")
                    continue
                w = r["weight_kg"]
                sheets.log_gym(r["exercise"], r["sets"], r["reps"], w, r.get("rpe"), r.get("notes", ""))
                # Update catalogue with latest weight
                if w > 0:
                    sheets.update_exercise_weight(r["exercise"], w)
                last = sheets.get_last_session(r["exercise"])
                entry = f"{r['number']}. {r['exercise']} — {w}kg {r['sets']}x{r['reps']}"
                if r.get("rpe"):
                    entry += f" RPE {r['rpe']}"
                if last and float(last.get("Weight", 0)) > 0:
                    prev = float(last.get("Weight", 0))
                    if w > prev:
                        entry += f" 🔺 +{w - prev}kg"
                        has_pr = True
                    elif w < prev:
                        entry += f" ↓ was {prev}kg"
                lines.append(entry)
        else:
            parsed = claude_ai.parse_gym_entry(text)
            sheets.log_gym(parsed["exercise"], parsed["sets"], parsed["reps"], parsed["weight"], parsed["rpe"], parsed["notes"])
            if parsed["weight"] > 0:
                sheets.update_exercise_weight(parsed["exercise"], parsed["weight"])
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
        log.error(traceback.format_exc()); await reply(_safe_error(e, "meal logging"))


# ── Recovery logging ──────────────────────────────────────────────────────────

async def _log_recovery(text: str, reply):
    try:
        # Shorthand: two numbers like "7.5 4" → hours + quality
        if re.match(r"^\s*\d+(\.\d+)?\s+[1-5]\s*$", text.strip()):
            parts = text.strip().split()
            hours, quality = float(parts[0]), int(parts[1])
        else:
            # Natural language — let Claude calculate actual sleep duration
            parsed = claude_ai.parse_sleep(text)
            hours, quality = parsed["hours"], parsed["quality"]
        quality = max(1, min(5, quality))
        sheets.log_sleep(hours, quality)
        streak = sheets.get_sleep_streak()
        buddy_reply = claude_ai.recovery_reply(hours, quality, streak)
        await reply(buddy_reply)
    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "logging"))


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
        log.error(traceback.format_exc()); await reply(_safe_error(e, "logging"))


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
    reply = update.message.reply_text

    # Food correction flow
    if ctx.user_data.get("awaiting_food_correction"):
        ctx.user_data["awaiting_food_correction"] = False
        last = ctx.user_data.get("last_food", {})
        original = last.get("macros", {})
        await reply("Re-analysing with your correction...")
        try:
            corrected = claude_ai.analyse_food_text(
                f"Original meal: {original.get('description', '')}. Correction: {text}"
            )
            resolved_type = corrected.get("meal_type") or last.get("meal_type") or sheets.infer_meal_type_from_time()
            sheets.delete_last_food_row()
            sheets.log_food(corrected["description"], corrected["calories"], corrected["protein"], corrected["carbs"], corrected["fats"], resolved_type)
            totals = sheets.get_today_totals()
            ctx.user_data["last_food"] = {"macros": corrected, "meal_type": resolved_type}
            msg = "Fixed. " + _build_food_reply(corrected, resolved_type, totals)
            await reply(msg, parse_mode="Markdown", reply_markup=_food_confirm_keyboard())
        except Exception as e:
            log.error(traceback.format_exc())
            await reply(_safe_error(e, "correction"))
        return

    # Gym results flow — waiting for the user to report back after seeing the list
    if ctx.user_data.get("awaiting_gym_results"):
        ctx.user_data.pop("awaiting_gym_results", None)
        await _log_gym_session(text, ctx, reply)
        ctx.user_data.pop("gym_exercises", None)
        ctx.user_data.pop("gym_set_name", None)
        return

    # Classify intent and act immediately
    from src.router import classify_intent
    intent = classify_intent(text)

    if intent == "meal":
        await _log_meal_text(text, reply, ctx=ctx)
    elif intent == "gym":
        await _show_gym_list(update, ctx)
    elif intent == "recovery":
        await _log_recovery(text, reply)
    elif intent == "emotions":
        await _log_emotions(text, reply)
    elif intent == "period":
        await _log_period(text, reply, bot=ctx.bot, chat_id=TELEGRAM_CHAT_ID)
    elif intent == "add_exercise":
        await handle_add_exercise(update, ctx)
    elif intent == "create_set":
        await handle_create_set(update, ctx)
    elif intent == "target_muscle":
        await handle_target_muscle(update, ctx)
    else:
        await _ask_intent(update, text)
        ctx.user_data["pending_messages"] = [text]


# ── Food confirm/fix buttons ──────────────────────────────────────────────────

def _food_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Correct", callback_data="food_correct"),
        InlineKeyboardButton("🔧 Fix this", callback_data="food_fix"),
        InlineKeyboardButton("🗑️ Delete", callback_data="food_delete"),
    ]])


def _build_food_reply(macros: dict, resolved_type: str, totals: dict) -> str:
    lines = [f"*{resolved_type.capitalize()}* · _{macros['note']}_\n"]

    # Per-item breakdown
    items = macros.get("items", [])
    if items:
        for item in items:
            cal = int(item.get("calories", 0))
            pro = float(item.get("protein", 0))
            carb = float(item.get("carbs", 0))
            fat = float(item.get("fats", 0))
            lines.append(
                f"• *{item['name']}* — {cal} cal · {pro}g P · {carb}g C · {fat}g F"
            )
        lines.append("")

    # Totals
    lines.append(
        f"Total: *{macros['calories']} cal* · {macros['protein']}g protein · "
        f"{macros['carbs']}g carbs · {macros['fats']}g fat"
    )
    lines.append(
        f"Today: {totals['calories']} / {DEFAULT_CALORIES} cal · "
        f"Protein {totals['protein']:.0f} / {DEFAULT_PROTEIN}g"
    )
    if totals["protein"] < DEFAULT_PROTEIN * 0.5 and totals["meals"] >= 2:
        lines.append("\nProtein's low, Liz. Prioritise it next meal.")
    return "\n".join(lines)


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

        # Use caption text alongside photo so bot isn't guessing blind
        caption = (update.message.caption or "").strip()
        macros = claude_ai.analyse_food_photo(image_bytes, caption_hint=caption)

        resolved_type = macros.get("meal_type") or sheets.infer_meal_type_from_time()
        sheets.log_food(macros["description"], macros["calories"], macros["protein"], macros["carbs"], macros["fats"], resolved_type)
        totals = sheets.get_today_totals()

        # Store last logged macros for correction flow
        ctx.user_data["last_food"] = {"macros": macros, "meal_type": resolved_type}

        msg = _build_food_reply(macros, resolved_type, totals)
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=_food_confirm_keyboard())
    except Exception as e:
        log.error(traceback.format_exc())
        await update.message.reply_text(_safe_error(e, "photo analysis"))


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


async def cmd_deletelast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    sheets.delete_last_food_row()
    totals = sheets.get_today_totals()
    await update.message.reply_text(
        f"Last food entry deleted.\nToday: {totals['calories']} / {DEFAULT_CALORIES} cal · Protein {totals['protein']:.0f} / {DEFAULT_PROTEIN}g"
    )


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
