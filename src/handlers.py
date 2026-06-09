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
    DEFAULT_GYM_SESSIONS_WEEK, DEFAULT_CARDIO_SESSIONS_WEEK, DEFAULT_CARDIO_MIN,
    TELEGRAM_CHAT_ID, HEIGHT_M,
)

# Transformation start for week number calculation
from datetime import date as _date_cls
_TRANSFORM_START = _date_cls(2026, 5, 18)


def _is_authorised(update: Update) -> bool:
    return update.effective_chat.id == TELEGRAM_CHAT_ID


async def _deny(update: Update):
    await update.message.reply_text("Unauthorised.")


# ── Intent disambiguation buttons ─────────────────────────────────────────────

async def _ask_intent(update: Update, text: str):
    keyboard = InlineKeyboardMarkup([
        # Log section header (fake, via leading label in first button)
        [InlineKeyboardButton("── LOG ──────────────", callback_data="noop")],
        [
            InlineKeyboardButton("🍱 Food",    callback_data="intent_meal"),
            InlineKeyboardButton("🏋️ Gym",     callback_data="intent_gym"),
        ],
        [
            InlineKeyboardButton("😴 Sleep",   callback_data="intent_recovery"),
            InlineKeyboardButton("💬 Mood",    callback_data="intent_emotions"),
        ],
        [
            InlineKeyboardButton("⚖️ Body",    callback_data="intent_body"),
            InlineKeyboardButton("🔴 Period",  callback_data="intent_period"),
        ],
        # Stats section
        [InlineKeyboardButton("── VIEW ─────────────", callback_data="noop")],
        [
            InlineKeyboardButton("📊 Today",       callback_data="menu_today"),
            InlineKeyboardButton("📅 Yesterday",   callback_data="menu_yesterday"),
        ],
        [
            InlineKeyboardButton("📈 This Week",   callback_data="menu_week"),
            InlineKeyboardButton("🎯 Quest Check", callback_data="menu_quest"),
        ],
        [
            InlineKeyboardButton("✅ Done for Day", callback_data="menu_done_day"),
        ],
    ])
    await update.message.reply_text("What's up?", reply_markup=keyboard)


# ── Callback query handler ────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── Food confirm/fix/cancel ───────────────────────────────────────────────
    if data == "confirm_food":
        pending = ctx.user_data.pop("pending_food", None)
        ctx.user_data.pop("last_meal_entry", None)
        if pending:
            m, mt = pending["macros"], pending["meal_type"]
            breakdown = _items_to_breakdown_str(m.get("items", []))
            sheets.log_food(m["description"], m["calories"], m["protein"], m["carbs"], m["fats"], mt, pending.get("log_date", ""), m.get("sugar", 0.0), breakdown)
            totals = sheets.get_today_totals()
            msg = _build_food_logged_msg(m, mt, totals)
            await query.edit_message_text(msg, parse_mode="Markdown")

    elif data == "reanalyse_food":
        # User said meals are different — run full Claude analysis on original text
        original_text = ctx.user_data.pop("pending_food_text", "")
        inferred_type = ctx.user_data.pop("pending_food_type", "")
        log_date = ctx.user_data.pop("pending_food_date", None)
        ctx.user_data.pop("last_meal_entry", None)
        if original_text:
            await query.edit_message_text("Re-analysing...")
            meal_history = sheets.get_recent_meal_descriptions(inferred_type)
            macros = claude_ai.analyse_food_text(original_text, meal_history=meal_history)
            resolved_type = inferred_type or macros.get("meal_type") or sheets.infer_meal_type_from_time()
            ctx.user_data["pending_food"] = {"macros": macros, "meal_type": resolved_type, "log_date": log_date}
            date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
            msg = _build_food_preview(macros, resolved_type) + date_note
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=_confirm_keyboard("food"))
        else:
            await query.edit_message_text("Lost the original text — try sending your meal again.")

    elif data == "confirm_food_repeat":
        # Log exact macros from the last stored meal — no Claude re-parsing
        last = ctx.user_data.pop("last_meal_entry", None)
        ctx.user_data.pop("pending_food", None)
        ctx.user_data.pop("pending_food_text", None)
        ctx.user_data.pop("pending_food_type", None)
        ctx.user_data.pop("pending_food_date", None)
        if last:
            pending_food = ctx.user_data.get("pending_food", {})
            log_date = pending_food.get("log_date", "") if pending_food else ""
            meal_desc = str(last.get("Meal", ""))
            cal = int(last.get("Calories", 0))
            pro = float(last.get("Protein", 0))
            carbs = float(last.get("Carbs", 0))
            fats = float(last.get("Fats", 0))
            sugar = sheets._get_sugar(last)
            meal_type = str(last.get("Meal Type", sheets.infer_meal_type_from_time()))
            # Reuse stored breakdown if available
            breakdown = str(last.get("Breakdown", ""))
            sheets.log_food(meal_desc, cal, pro, carbs, fats, meal_type, log_date, sugar, breakdown)
            totals = sheets.get_today_totals()
            SUGAR_TARGET = 25.0
            cal_left = DEFAULT_CALORIES - totals["calories"]
            pro_left = DEFAULT_PROTEIN - totals["protein"]
            t_sugar  = totals["sugar"]
            sugar_str = "✅" if t_sugar <= SUGAR_TARGET else f"⚠️ over by {t_sugar - SUGAR_TARGET:.0f}g"
            msg = (
                f"✅ *{meal_type.capitalize()} logged* _(same as last time)_\n"
                f"• *{meal_desc}* — {cal}cal · {pro:.0f}g P · {carbs:.0f}g C · {fats:.0f}g F\n\n"
                f"*Day total: {totals['calories']} / {DEFAULT_CALORIES} cal* "
                f"({'↓' + str(abs(int(cal_left))) + ' to go' if cal_left > 0 else '✅'})\n"
                f"Protein: {totals['protein']:.0f} / {DEFAULT_PROTEIN}g "
                f"({'↓' + str(abs(int(pro_left))) + 'g' if pro_left > 0 else '✅'})\n"
                f"Carbs: {totals['carbs']:.0f}g · Fats: {totals['fats']:.0f}g\n"
                f"Sugar: {t_sugar:.0f} / {SUGAR_TARGET:.0f}g {sugar_str}"
            )
            await query.edit_message_text(msg, parse_mode="Markdown")
        else:
            await query.edit_message_text("Couldn't find last entry — try logging again.")

    elif data == "fix_food":
        await query.edit_message_reply_markup(reply_markup=None)
        ctx.user_data["awaiting_fix"] = "food"
        ctx.user_data.pop("last_meal_entry", None)
        await query.message.reply_text("What's wrong? Tell me and I'll re-analyse.")

    elif data == "cancel_food":
        ctx.user_data.pop("pending_food", None)
        ctx.user_data.pop("last_meal_entry", None)
        ctx.user_data.pop("pending_food_text", None)
        ctx.user_data.pop("pending_food_type", None)
        ctx.user_data.pop("pending_food_date", None)
        await query.edit_message_text("Cancelled — nothing logged.")

    # ── Sleep confirm/fix/cancel ──────────────────────────────────────────────
    elif data == "confirm_sleep":
        pending = ctx.user_data.pop("pending_sleep", None)
        if pending:
            sheets.log_sleep(
                pending["hours"], pending.get("notes", ""), pending.get("log_date", ""),
                sleep_time=pending.get("sleep_time", ""),
                wake_time=pending.get("wake_time", ""),
            )
            streak = sheets.get_sleep_streak()
            buddy_reply = claude_ai.recovery_reply(pending["hours"], pending.get("notes", ""), streak)
            await query.edit_message_text(f"✅ Logged.\n{buddy_reply}")

    elif data == "fix_sleep":
        await query.edit_message_reply_markup(reply_markup=None)
        ctx.user_data["awaiting_fix"] = "sleep"
        await query.message.reply_text("What's wrong with the sleep data? Correct me.")

    elif data == "cancel_sleep":
        ctx.user_data.pop("pending_sleep", None)
        await query.edit_message_text("Cancelled — nothing logged.")

    # ── Emotions confirm/fix/cancel ───────────────────────────────────────────
    elif data == "confirm_emotions":
        pending = ctx.user_data.pop("pending_emotions", None)
        if pending:
            sheets.log_emotions(pending["mood"], pending["energy"], pending["notes"], pending.get("log_date", ""))
            cycle_day, phase = sheets.get_cycle_info()
            buddy_reply = claude_ai.emotions_reply(pending["mood"], pending["energy"], pending["notes"], cycle_day, phase)
            await query.edit_message_text(f"✅ Logged.\n{buddy_reply}")

    elif data == "fix_emotions":
        await query.edit_message_reply_markup(reply_markup=None)
        ctx.user_data["awaiting_fix"] = "emotions"
        await query.message.reply_text("What should I change about mood/energy?")

    elif data == "cancel_emotions":
        ctx.user_data.pop("pending_emotions", None)
        await query.edit_message_text("Cancelled — nothing logged.")

    # ── Body check-in confirm/fix/cancel ─────────────────────────────────────
    elif data == "confirm_body":
        pending = ctx.user_data.pop("pending_body", None)
        if pending:
            sheets.log_body(
                pending["weight_kg"], pending["body_fat_pct"],
                pending["tags"], pending["notes"],
                lean_mass_kg=pending.get("lean_mass_kg"),
                skeletal_muscle_kg=pending.get("skeletal_muscle_kg"),
                fat_mass_kg=pending.get("fat_mass_kg"),
                visceral_fat_level=pending.get("visceral_fat_level"),
            )
            buddy_reply = claude_ai.body_checkin_reply(
                pending["weight_kg"], pending["bmi"],
                pending["tags"], pending["notes"],
            )
            await query.edit_message_text(f"✅ Logged.\n{buddy_reply}")

    elif data == "fix_body":
        await query.edit_message_reply_markup(reply_markup=None)
        ctx.user_data["awaiting_fix"] = "body"
        await query.message.reply_text("What should I fix? Tell me again.")

    elif data == "cancel_body":
        ctx.user_data.pop("pending_body", None)
        await query.edit_message_text("Cancelled — nothing logged.")

    # ── Gym confirm/cancel ────────────────────────────────────────────────────
    elif data == "confirm_gym":
        pending = ctx.user_data.pop("pending_gym", None)
        if pending:
            log_date = pending.get("log_date", "")
            _dur_re = re.compile(r'(\d+)\s*min', re.IGNORECASE)
            for r in pending.get("results", []):
                if not r.get("skipped"):
                    w = r.get("weight_kg", r.get("weight", 0))
                    ex_type = r.get("type", "strength")
                    dur = int(r.get("duration_min", 0) or 0)
                    # Fallback: extract duration from notes if cardio duration wasn't parsed
                    if ex_type == "cardio" and dur == 0:
                        m = _dur_re.search(str(r.get("notes", "")))
                        if m:
                            dur = int(m.group(1))
                    dist = float(r.get("distance_km", 0) or 0)
                    sheets.log_gym(r["exercise"], r["sets"], r["reps"], w, r.get("rpe"), r.get("notes", ""), log_date, ex_type, dur, distance_km=dist)
                    if w > 0:
                        sheets.update_exercise_weight(r["exercise"], w)
            ctx.user_data.pop("gym_exercises", None)
            ctx.user_data.pop("gym_set_name", None)
            await query.edit_message_text("✅ Session logged. Mission complete, Liz.")

    elif data == "cancel_gym":
        ctx.user_data.pop("pending_gym", None)
        ctx.user_data.pop("gym_exercises", None)
        ctx.user_data.pop("awaiting_gym_results", None)
        await query.edit_message_text("Cancelled — nothing logged.")

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

    # ── Stats date confirm / pick ─────────────────────────────────────────────
    elif data == "confirm_stats_date":
        log_date = ctx.user_data.pop("pending_stats_date", None)
        await query.edit_message_reply_markup(reply_markup=None)
        if log_date:
            await _fetch_and_show_stats(log_date, query.message.reply_text)
        else:
            await query.message.reply_text("Lost the date — try again.")

    elif data == "pick_stats_date":
        ctx.user_data.pop("pending_stats_date", None)
        await query.edit_message_text("Which day? (e.g. last Monday, May 5, 3 days ago)")
        ctx.user_data["awaiting_menu_log"] = "pick_day"

    # ── Main menu callbacks ───────────────────────────────────────────────────
    elif data.startswith("menu_"):
        action = data[5:]  # strip "menu_"
        await query.edit_message_reply_markup(reply_markup=None)
        reply = query.message.reply_text

        if action == "log_food":
            ctx.user_data["awaiting_fix"] = None
            await reply("What did you eat? Describe it and I'll log the macros.")
            ctx.user_data["awaiting_menu_log"] = "food"

        elif action == "log_gym":
            await _show_gym_list(update, ctx)

        elif action == "log_sleep":
            await reply("How did you sleep? Tell me hours and anything worth noting.")
            ctx.user_data["awaiting_menu_log"] = "sleep"

        elif action == "log_mood":
            await reply("How are you feeling? Mood, energy, what's on your mind.")
            ctx.user_data["awaiting_menu_log"] = "emotions"

        elif action == "log_body":
            await reply("Weight and/or how your body feels today (e.g. 52.3kg, feeling strong, bit sore).")
            ctx.user_data["awaiting_menu_log"] = "body"

        elif action == "log_period":
            await _log_period("period started", reply, bot=ctx.bot, chat_id=TELEGRAM_CHAT_ID)

        elif action == "today":
            from datetime import date as _dt
            await _fetch_and_show_stats(_dt.today().isoformat(), reply)

        elif action == "yesterday":
            from datetime import date as _dt, timedelta
            await _fetch_and_show_stats((_dt.today() - timedelta(days=1)).isoformat(), reply)

        elif action == "week":
            await _handle_week_stats(reply)

        elif action == "pick_day":
            await reply("Which day? (e.g. last Monday, May 5, 3 days ago)")
            ctx.user_data["awaiting_menu_log"] = "pick_day"

        elif action == "quest":
            await _handle_quest_check(reply)

        elif action == "done_day":
            await _handle_done_for_day(reply)

    elif data == "noop":
        pass  # section header buttons — do nothing

    # ── Gym type selection ────────────────────────────────────────────────────
    elif data.startswith("gym_"):
        await query.edit_message_reply_markup(reply_markup=None)
        reply = query.message.reply_text

        if data == "gym_stairmaster":
            await reply("⏱ How many minutes on the stairmaster/incline?")
            ctx.user_data["awaiting_menu_log"] = "gym_cardio_time"

        elif data == "gym_revl_cardio":
            await reply("⏱ What cardio and how long? (e.g. 'treadmill 20min', 'bike 30min')")
            ctx.user_data["awaiting_menu_log"] = "gym_cardio_time"

        elif data == "gym_revl_strength":
            await _show_gym_strength_history(reply, set_name="Revl")
            ctx.user_data["awaiting_gym_results"] = True
            ctx.user_data["gym_set_name"] = "Revl"

        elif data == "gym_strength":
            await _show_gym_strength_history(reply, set_name="Gym")
            ctx.user_data["awaiting_gym_results"] = True
            ctx.user_data["gym_set_name"] = "Gym"

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
            await _log_recovery(combined, reply, ctx=ctx)
        elif intent == "emotions":
            await _log_emotions(combined, reply, ctx=ctx)
        elif intent == "body":
            await reply("Weight and/or how your body feels today (e.g. 52.3kg, feeling strong).")
            ctx.user_data["awaiting_menu_log"] = "body"
        elif intent == "period":
            await _log_period(combined, reply, bot=ctx.bot, chat_id=TELEGRAM_CHAT_ID)


# ── Meal logging ──────────────────────────────────────────────────────────────

MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack", "supper"]


def _meal_type_from_text(text: str) -> str:
    """Check if the user explicitly named a meal type in their message."""
    lower = text.lower()
    for mt in ["breakfast", "lunch", "dinner", "supper", "snack"]:
        if mt in lower:
            return mt
    return ""


async def _log_meal_text(text: str, reply, ctx=None, meal_type: str = ""):
    try:
        log_date = claude_ai.extract_log_date(text)
        # Prefer: explicit arg > text mention > time-based inference
        inferred_type = meal_type or _meal_type_from_text(text) or sheets.infer_meal_type_from_time()
        last_entry = sheets.get_last_meal_entry(inferred_type)

        # ── "Same as last time?" — show stored meal first, skip re-analysis ──
        if last_entry and _meals_look_similar(text, str(last_entry.get("Meal", ""))):
            if ctx:
                ctx.user_data["last_meal_entry"] = last_entry
                ctx.user_data["pending_food_text"] = text
                ctx.user_data["pending_food_type"] = inferred_type
                ctx.user_data["pending_food_date"] = log_date

            meal_desc = str(last_entry.get("Meal", ""))
            cal  = last_entry.get("Calories", 0)
            pro  = last_entry.get("Protein", 0)
            carb = last_entry.get("Carbs", 0)
            fat  = last_entry.get("Fats", 0)
            sugar = sheets._get_sugar(last_entry)
            last_date = last_entry.get("Date", "")

            date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
            msg = (
                f"*Same as last time?* _(from {last_date})_\n\n"
                f"_{meal_desc}_\n\n"
                f"{cal} cal · {pro}g P · {carb}g C · {fat}g F · {sugar}g sugar"
                f"{date_note}"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, same meal", callback_data="confirm_food_repeat")],
                [InlineKeyboardButton("✏️ Different — re-analyse", callback_data="reanalyse_food")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_food")],
            ])
            await reply(msg, parse_mode="Markdown", reply_markup=keyboard)
            return

        # ── No similar recent meal — full Claude analysis ─────────────────────
        meal_history = sheets.get_recent_meal_descriptions(inferred_type)
        macros = claude_ai.analyse_food_text(text, meal_history=meal_history)

        if macros["calories"] == 0 and macros["protein"] == 0:
            await reply("What did you eat exactly? Give me weights if you have them — e.g. `100g chicken, 150g rice, side salad`")
            return

        resolved_type = meal_type or macros.get("meal_type") or sheets.infer_meal_type_from_time()

        if ctx:
            ctx.user_data["pending_food"] = {"macros": macros, "meal_type": resolved_type, "log_date": log_date}

        date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
        msg = _build_food_preview(macros, resolved_type) + date_note
        await reply(msg, parse_mode="Markdown", reply_markup=_confirm_keyboard("food"))
    except Exception as e:
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

def _gym_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏃 Revl — Cardio",         callback_data="gym_revl_cardio"),
         InlineKeyboardButton("💪 Revl — Strength",       callback_data="gym_revl_strength")],
        [InlineKeyboardButton("🪜 Gym — Stairmaster/Incline", callback_data="gym_stairmaster"),
         InlineKeyboardButton("🏋️ Gym — Strength",        callback_data="gym_strength")],
    ])


async def _show_gym_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE, set_name_hint: str = ""):
    """Show 4-option gym type menu."""
    await update.effective_message.reply_text(
        "What type of session?", reply_markup=_gym_type_keyboard()
    )


async def _show_gym_strength_history(reply, set_name: str = ""):
    """Show past 3-week strength exercises grouped by muscle, prefilled 3 sets."""
    try:
        exercises = sheets.get_strength_exercises_past_weeks(weeks=3)
        if not exercises:
            await reply(
                "No strength sessions in the past 3 weeks yet.\n"
                "Log your session: e.g. `Cable Row 35kg, Deadlift 52kg`",
                parse_mode="Markdown",
            )
            return

        # Group by muscle group
        from collections import defaultdict
        by_muscle: dict[str, list] = defaultdict(list)
        for ex in exercises:
            muscle = ex["muscle_group"] or "Other"
            by_muscle[muscle].append(ex)

        lines = ["*Past 3 weeks — copy/edit and send:*\n"]
        for muscle, exs in sorted(by_muscle.items()):
            lines.append(f"_{muscle}_")
            for ex in exs:
                w = ex["last_weight"]
                w_str = f"{w}kg" if w else "bodyweight"
                lines.append(f"• {ex['exercise']} — {w_str} × 3 sets")
            lines.append("")
        lines.append("_Edit weights/sets as needed, then send_")
        await reply("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(traceback.format_exc())
        await reply("Couldn't load exercise history. Log your session manually.")


async def _log_gym_session(text: str, ctx, reply):
    """Parse gym results, store pending, show preview for confirmation."""
    try:
        log_date = claude_ai.extract_log_date(text)
        exercise_list = ctx.user_data.get("gym_exercises", [])
        has_pr = False
        lines = []
        pending_results = []

        if exercise_list:
            ex_names = [
                e.get("Exercise Name", e) if isinstance(e, dict) else e
                for e in exercise_list
            ]
            results = claude_ai.parse_session_results(ex_names, text)
            for r in results:
                pending_results.append(r)
                if r.get("skipped"):
                    continue  # don't show skipped exercises in summary
                ex_type = str(r.get("type", "strength")).lower()
                if ex_type == "cardio":
                    dur  = int(r.get("duration_min", 0) or 0)
                    dist = float(r.get("distance_km", 0) or 0)
                    entry = f"• {r['exercise']}"
                    if dist:
                        entry += f" — {dist}km"
                    if dur:
                        entry += f" · {dur}min"
                    if r.get("notes") and not dist and not dur:
                        entry += f" _{r['notes']}_"
                    lines.append(entry)
                else:
                    w = r["weight_kg"]
                    last = sheets.get_last_session(r["exercise"])
                    sets = r.get("sets", 3) or 3
                    reps = r.get("reps", 0)
                    entry = f"• {r['exercise']} — {w}kg {sets}x{reps}"
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
            pending_results.append({**parsed, "weight_kg": parsed["weight"], "skipped": False})
            last = sheets.get_last_session(parsed["exercise"])
            pb = sheets.get_pb(parsed["exercise"])
            entry = f"{parsed['exercise']} — {parsed['weight']}kg {parsed['sets']}x{parsed['reps']}"
            if parsed["rpe"]:
                entry += f" RPE {parsed['rpe']}"
            if pb and parsed["weight"] > float(pb.get("Weight", 0)):
                entry += " 🔺 PR!"
                has_pr = True
            lines.append(entry)

        ctx.user_data["pending_gym"] = {"results": pending_results, "log_date": log_date}
        buddy_reply = claude_ai.gym_session_reply(lines, has_pr)
        session_summary = "\n".join(lines)
        date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
        await reply(
            f"{session_summary}\n\n{buddy_reply}{date_note}",
            parse_mode="Markdown",
            reply_markup=_gym_confirm_keyboard(),
        )
    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "gym logging"))


# ── Recovery logging ──────────────────────────────────────────────────────────

async def _log_recovery(text: str, reply, ctx=None):
    try:
        log_date = claude_ai.extract_log_date(text)
        parsed = claude_ai.parse_sleep(text)
        hours       = parsed["hours"]
        notes       = parsed.get("notes", "")
        sleep_time  = parsed.get("sleep_time", "")
        wake_time   = parsed.get("wake_time", "")
        if ctx:
            ctx.user_data["pending_sleep"] = {
                "hours": hours, "notes": notes, "log_date": log_date,
                "sleep_time": sleep_time, "wake_time": wake_time,
            }
        date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
        time_str = ""
        if sleep_time and wake_time:
            time_str = f"\n🕐 {sleep_time} → {wake_time}"
        elif sleep_time:
            time_str = f"\n🕐 Slept {sleep_time}"
        elif wake_time:
            time_str = f"\n⏰ Woke {wake_time}"
        msg = f"*Sleep:* {hours}h{time_str}"
        if notes:
            msg += f"\n_{notes}_"
        msg += f"{date_note}\n\nCorrect?"
        await reply(msg, parse_mode="Markdown", reply_markup=_confirm_keyboard("sleep"))
    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "logging"))


# ── Emotions logging ──────────────────────────────────────────────────────────

async def _log_emotions(text: str, reply, ctx=None):
    try:
        log_date = claude_ai.extract_log_date(text)
        parsed = claude_ai.parse_emotions(text)
        if ctx:
            ctx.user_data["pending_emotions"] = {**parsed, "log_date": log_date}
        date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
        msg = (
            f"*Mood:* {parsed['mood']}/10 · *Energy:* {parsed['energy']}/10\n"
            f"_{parsed['notes']}_{date_note}\n\nCorrect?"
        )
        await reply(msg, parse_mode="Markdown", reply_markup=_confirm_keyboard("emotions"))
    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "logging"))


# ── Body check-in ─────────────────────────────────────────────────────────────

async def _log_body_checkin(text: str, reply, ctx=None):
    try:
        parsed = claude_ai.parse_body_checkin(text)
        weight = parsed["weight_kg"]
        bf_pct = parsed["body_fat_pct"]
        lean_mass = parsed["lean_mass_kg"]
        skeletal_muscle = parsed["skeletal_muscle_kg"]
        fat_mass = parsed["fat_mass_kg"]
        visceral_fat = parsed["visceral_fat_level"]
        tags = parsed["tags"]
        notes = parsed["notes"]

        bmi = round(weight / (HEIGHT_M ** 2), 1) if weight else None
        # Derive lean mass if not given directly
        if not lean_mass and weight and bf_pct:
            lean_mass = round(weight * (1 - bf_pct / 100), 1)

        if ctx:
            ctx.user_data["pending_body"] = {
                "weight_kg": weight, "body_fat_pct": bf_pct,
                "lean_mass_kg": lean_mass, "skeletal_muscle_kg": skeletal_muscle,
                "fat_mass_kg": fat_mass, "visceral_fat_level": visceral_fat,
                "tags": tags, "notes": notes, "bmi": bmi,
            }

        # Build preview
        lines = ["*Body Check-in*\n"]
        if weight:
            lines.append(f"⚖️ Weight: *{weight} kg*")
            lines.append(f"📊 BMI: *{bmi}* ({_bmi_category(bmi)})")
        if bf_pct:
            lines.append(f"🔬 Body fat: *{bf_pct}%*")
        if fat_mass:
            lines.append(f"🫀 Fat mass: *{fat_mass} kg*")
        if lean_mass:
            lines.append(f"💪 Lean mass: *{lean_mass} kg*")
        if skeletal_muscle:
            lines.append(f"🦾 Skeletal muscle: *{skeletal_muscle} kg*")
        if visceral_fat:
            lines.append(f"📉 Visceral fat level: *{visceral_fat}*")
        if tags:
            lines.append(f"🏷 Feel: {' · '.join(tags)}")
        if notes:
            lines.append(f"📝 *Notes:* _{notes}_")
        if not weight and not tags:
            await reply(
                "What did you want to log? Tell me your weight (e.g. 52.3kg) and/or how you feel.\n"
                "_You can also add notes — e.g. '52.3kg, shouldn't have skipped protein yesterday'_",
                parse_mode="Markdown",
            )
            return

        lines.append("\nCorrect?")
        await reply("\n".join(lines), parse_mode="Markdown", reply_markup=_confirm_keyboard("body"))
    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "body check-in"))


def _bmi_category(bmi: float) -> str:
    if bmi < 18.5:
        return "underweight"
    elif bmi < 25.0:
        return "healthy"
    elif bmi < 30.0:
        return "overweight"
    else:
        return "obese"


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

    # Fix flow — user is correcting a pending entry before it's logged
    if ctx.user_data.get("awaiting_fix"):
        fix_type = ctx.user_data.pop("awaiting_fix")
        try:
            if fix_type == "food":
                original = ctx.user_data.get("pending_food", {})
                original_desc = original.get("macros", {}).get("description", "")
                original_mt = original.get("meal_type", "")
                # Carry existing date, or detect one from the correction text
                log_date = original.get("log_date") or claude_ai.extract_log_date(text)
                corrected = claude_ai.analyse_food_text(
                    f"Original meal: {original_desc}. Correction: {text}"
                )
                resolved_type = corrected.get("meal_type") or original_mt or sheets.infer_meal_type_from_time()
                ctx.user_data["pending_food"] = {"macros": corrected, "meal_type": resolved_type, "log_date": log_date}
                date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
                msg = _build_food_preview(corrected, resolved_type) + date_note
                await reply(msg, parse_mode="Markdown", reply_markup=_confirm_keyboard("food"))

            elif fix_type == "sleep":
                log_date = ctx.user_data.get("pending_sleep", {}).get("log_date") or claude_ai.extract_log_date(text)
                parsed = claude_ai.parse_sleep(text)
                hours, notes = parsed["hours"], parsed.get("notes", "")
                ctx.user_data["pending_sleep"] = {"hours": hours, "notes": notes, "log_date": log_date}
                date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
                msg = f"*Updated:* {hours}h\n_{notes}_{date_note}\nCorrect?" if notes else f"*Updated:* {hours}h{date_note}\nCorrect?"
                await reply(msg, parse_mode="Markdown", reply_markup=_confirm_keyboard("sleep"))

            elif fix_type == "emotions":
                log_date = ctx.user_data.get("pending_emotions", {}).get("log_date") or claude_ai.extract_log_date(text)
                parsed = claude_ai.parse_emotions(text)
                ctx.user_data["pending_emotions"] = {**parsed, "log_date": log_date}
                date_note = f"\n📅 *Logging for {log_date}*" if log_date else ""
                msg = f"*Updated:* Mood {parsed['mood']}/10 · Energy {parsed['energy']}/10\n_{parsed['notes']}_{date_note}\nCorrect?"
                await reply(msg, parse_mode="Markdown", reply_markup=_confirm_keyboard("emotions"))

            elif fix_type == "body":
                await _log_body_checkin(text, reply, ctx=ctx)
        except Exception as e:
            log.error(traceback.format_exc())
            await reply(_safe_error(e, "correction"))
        return

    # "menu" or "help" shortcut
    if text.lower().strip() in {"menu", "help", "hi", "hey"}:
        await reply("What are we doing?", reply_markup=_main_menu_keyboard())
        return

    # Menu-triggered log flow
    if ctx.user_data.get("awaiting_menu_log"):
        mode = ctx.user_data.pop("awaiting_menu_log")
        if mode == "food":
            await _log_meal_text(text, reply, ctx=ctx)
        elif mode == "sleep":
            await _log_recovery(text, reply, ctx=ctx)
        elif mode == "emotions":
            await _log_emotions(text, reply, ctx=ctx)
        elif mode == "body":
            await _log_body_checkin(text, reply, ctx=ctx)
        elif mode == "pick_day":
            await _handle_stats_query(text, reply)
        elif mode == "gym_cardio_time":
            # User replied with duration (e.g. "20", "20min", "stairmaster 20min")
            await _log_gym_session(text, ctx, reply)
        return

    # Done-for-day always wins — even if gym state is pending
    from src.router import _DONE_RE, _NO_RE
    if _DONE_RE.search(text):
        ctx.user_data.pop("awaiting_gym_results", None)
        ctx.user_data.pop("gym_exercises", None)
        ctx.user_data.pop("gym_set_name", None)
        await _handle_done_for_day(reply)
        return

    # "no" / "nope" / "never mind" while any state is pending → clear + show menu
    if _NO_RE.match(text.strip()):
        had_state = any([
            ctx.user_data.pop("awaiting_gym_results", None),
            ctx.user_data.pop("awaiting_menu_log", None),
            ctx.user_data.pop("awaiting_fix", None),
        ])
        ctx.user_data.pop("gym_exercises", None)
        ctx.user_data.pop("gym_set_name", None)
        if had_state:
            await reply("No worries. What's up?", reply_markup=_main_menu_keyboard())
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
        _HAS_EXERCISE_DATA = re.compile(r'\d+\s*(kg|x\d|min\b|lbs|reps?)', re.IGNORECASE)
        _IS_CARDIO = re.compile(r'\b(stairmaster|treadmill|incline|cardio|cycling|bike|rowing|elliptical|running)\b', re.IGNORECASE)
        if _HAS_EXERCISE_DATA.search(text):
            # Has data inline → log directly
            await _log_gym_session(text, ctx, reply)
        elif _IS_CARDIO.search(text):
            # Explicitly cardio → show cardio options
            await update.effective_message.reply_text(
                "What cardio?", reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🪜 Stairmaster/Incline", callback_data="gym_stairmaster"),
                     InlineKeyboardButton("🏃 Other cardio", callback_data="gym_revl_cardio")],
                ])
            )
        else:
            # Default: straight to strength history, no menu
            await _show_gym_strength_history(reply)
            ctx.user_data["awaiting_gym_results"] = True
    elif intent == "recovery":
        await _log_recovery(text, reply, ctx=ctx)
    elif intent == "emotions":
        await _log_emotions(text, reply, ctx=ctx)
    elif intent == "period":
        await _log_period(text, reply, bot=ctx.bot, chat_id=TELEGRAM_CHAT_ID)
    elif intent == "food_query":
        await _handle_food_query(text, reply)
    elif intent == "stats_query":
        await _handle_stats_query(text, reply)
    elif intent == "done_for_day":
        await _handle_done_for_day(reply)
    elif intent == "content":
        await _log_content(text, reply)
    elif intent == "body_check":
        await _log_body_checkin(text, reply, ctx=ctx)
    elif intent == "add_exercise":
        await handle_add_exercise(update, ctx)
    elif intent == "create_set":
        await handle_create_set(update, ctx)
    elif intent == "target_muscle":
        await handle_target_muscle(update, ctx)
    else:
        await _ask_intent(update, text)
        ctx.user_data["pending_messages"] = [text]


# ── Confirm keyboard (pre-log) ────────────────────────────────────────────────

def _confirm_keyboard(entry_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Log it", callback_data=f"confirm_{entry_type}"),
        InlineKeyboardButton("🔧 Fix this", callback_data=f"fix_{entry_type}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{entry_type}"),
    ]])


def _confirm_keyboard_with_repeat(entry_type: str, last_entry: dict) -> InlineKeyboardMarkup:
    """Keyboard with an extra 'Same as last time' button using exact stored macros."""
    cal = last_entry.get("Calories", "?")
    pro = last_entry.get("Protein", "?")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Log it", callback_data=f"confirm_{entry_type}"),
            InlineKeyboardButton("🔧 Fix this", callback_data=f"fix_{entry_type}"),
        ],
        [
            InlineKeyboardButton(f"🔄 Same as last ({cal} cal · {pro}g P)", callback_data="confirm_food_repeat"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{entry_type}"),
        ],
    ])


def _normalise_meal_text(s: str) -> str:
    """Expand shorthand so similarity matching works."""
    s = s.lower()
    s = re.sub(r"\bpbb\b", "peanut butter", s)
    s = re.sub(r"\bpb\b", "peanut butter", s)
    s = re.sub(r"\bsandwich\b", "bread", s)
    s = re.sub(r"\btoast\b", "bread", s)
    s = re.sub(r"\bcoffee\b", "coffee", s)
    return s


def _meals_look_similar(new_text: str, last_description: str) -> bool:
    """Check if new meal text likely refers to the same meal as last logged one."""
    _STOPWORDS = {"and", "the", "with", "some", "had", "have", "ate", "eat", "for",
                  "breakfast", "lunch", "dinner", "snack", "supper", "today", "my",
                  "just", "bit", "half", "boiled", "slice", "tablespoon", "tbsp"}

    def keywords(s: str) -> set:
        s = _normalise_meal_text(s)
        return {w for w in re.split(r"\W+", s) if len(w) > 2 and w not in _STOPWORDS}

    new_kw  = keywords(new_text)
    last_kw = keywords(last_description)
    overlap = new_kw & last_kw

    # Extra bridges: egg variants
    if any("egg" in w for w in new_kw) and any("egg" in w for w in last_kw):
        overlap.add("egg")

    return len(overlap) >= 2


def _gym_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Log it", callback_data="confirm_gym"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_gym"),
    ]])


def _items_to_breakdown_str(items: list) -> str:
    """Compact single-line string for storing in the Breakdown sheet column."""
    parts = []
    for item in items:
        name = item.get("name", "")
        cal  = int(item.get("calories", 0))
        pro  = float(item.get("protein", 0))
        parts.append(f"{name}: {cal}cal {pro:.0f}gP")
    return " | ".join(parts)


def _build_food_logged_msg(macros: dict, meal_type: str, totals: dict) -> str:
    """Confirmation message shown after a meal is logged — full macros per item + running day totals."""
    SUGAR_TARGET = 25.0
    items = macros.get("items", [])
    lines = [f"✅ *{meal_type.capitalize()} logged*\n"]

    # Per-item breakdown
    if items:
        for item in items:
            cal   = int(item.get("calories", 0))
            pro   = float(item.get("protein", 0))
            carb  = float(item.get("carbs", 0))
            fat   = float(item.get("fats", 0))
            sugar = float(item.get("sugar", 0))
            macro_str = f"{cal}cal · {pro:.0f}g P · {carb:.0f}g C · {fat:.0f}g F"
            if sugar > 0.5:
                macro_str += f" · {sugar:.0f}g sugar"
            lines.append(f"• *{item['name']}* — {macro_str}")
        lines.append("")

    # This meal's totals
    m_cal  = macros.get("calories", 0)
    m_pro  = macros.get("protein", 0)
    m_carb = macros.get("carbs", 0)
    m_fat  = macros.get("fats", 0)
    m_sug  = macros.get("sugar", 0)
    lines.append(f"_This meal: {m_cal}cal · {m_pro:.0f}g P · {m_carb:.0f}g C · {m_fat:.0f}g F_\n")

    # Running day totals
    cal_left = DEFAULT_CALORIES - totals["calories"]
    pro_left = DEFAULT_PROTEIN - totals["protein"]
    sugar    = totals.get("sugar", 0)
    lines.append(
        f"*Day total: {totals['calories']} / {DEFAULT_CALORIES} cal* "
        f"({'↓' + str(abs(int(cal_left))) + ' to go' if cal_left > 0 else '✅ over by ' + str(abs(int(cal_left)))})"
    )
    lines.append(
        f"Protein: {totals['protein']:.0f} / {DEFAULT_PROTEIN}g "
        f"({'↓' + str(abs(int(pro_left))) + 'g' if pro_left > 0 else '✅'})"
    )
    lines.append(
        f"Carbs: {totals.get('carbs', 0):.0f}g · Fats: {totals.get('fats', 0):.0f}g"
    )
    sugar_str = "✅" if sugar <= SUGAR_TARGET else f"⚠️ over by {sugar - SUGAR_TARGET:.0f}g"
    lines.append(f"Sugar: {sugar:.0f} / {SUGAR_TARGET:.0f}g {sugar_str}")

    if totals["protein"] < DEFAULT_PROTEIN * 0.5 and totals["meals"] >= 2:
        lines.append("\nProtein's low, Liz. Prioritise it next meal.")
    return "\n".join(lines)


def _build_food_preview(macros: dict, resolved_type: str) -> str:
    """Show meal breakdown before logging — no today totals yet."""
    lines = [f"*{resolved_type.capitalize()}* · _{macros.get('note', '')}_\n"]
    items = macros.get("items", [])
    if items:
        for item in items:
            cal = int(item.get("calories", 0))
            pro = float(item.get("protein", 0))
            carb = float(item.get("carbs", 0))
            fat = float(item.get("fats", 0))
            sugar = float(item.get("sugar", 0))
            line = f"• *{item['name']}* — {cal} cal · {pro}g P · {carb}g C · {fat}g F"
            if sugar > 0:
                line += f" · {sugar}g sugar"
            lines.append(line)
        lines.append("")
    sugar_total = macros.get("sugar", 0)
    lines.append(
        f"Total: *{macros['calories']} cal* · {macros['protein']}g protein · "
        f"{macros['carbs']}g carbs · {macros['fats']}g fat · {sugar_total}g sugar"
    )
    lines.append("\nCorrect?")
    return "\n".join(lines)


# ── Food query (past days) ────────────────────────────────────────────────────

async def _handle_food_query(text: str, reply):
    try:
        from datetime import date, timedelta
        log_date = claude_ai.extract_log_date(text)
        if not log_date:
            log_date = (date.today() - timedelta(days=1)).isoformat()

        rows = sheets.get_food_by_date(log_date)
        if not rows:
            await reply(f"Nothing logged for {log_date}.")
            return

        total_cal = sum(int(r.get("Calories", 0)) for r in rows)
        total_pro = sum(float(r.get("Protein", 0)) for r in rows)
        total_carbs = sum(float(r.get("Carbs", 0)) for r in rows)
        total_fats = sum(float(r.get("Fats", 0)) for r in rows)
        total_sugar = sum(sheets._get_sugar(r) for r in rows)

        # Meal breakdown grouped by type
        lines = [f"*{log_date} — what you ate:*\n"]
        current_meal = None
        for r in rows:
            meal = str(r.get("Meal Type", "")).capitalize()
            if meal != current_meal:
                current_meal = meal
                lines.append(f"\n_{meal}_")
            lines.append(f"• {r.get('Meal', '')} — {r.get('Calories', 0)} cal · {r.get('Protein', 0)}g P")

        lines.append(f"\n*Total: {total_cal} cal · {total_pro:.0f}g protein · {total_carbs:.0f}g carbs · {total_fats:.0f}g fat · {total_sugar:.0f}g sugar*")

        # Gap vs targets
        SUGAR_TARGET = 25.0
        cal_gap = DEFAULT_CALORIES - total_cal
        pro_gap = DEFAULT_PROTEIN - total_pro
        sugar_gap = SUGAR_TARGET - total_sugar
        lines.append("\n*vs targets:*")
        lines.append(f"Calories: {total_cal} / {DEFAULT_CALORIES} ({'under by ' + str(abs(int(cal_gap))) if cal_gap > 0 else 'over by ' + str(abs(int(cal_gap)))})")
        lines.append(f"Protein: {total_pro:.0f}g / {DEFAULT_PROTEIN}g ({'under by ' + str(abs(int(pro_gap))) + 'g' if pro_gap > 0 else 'over by ' + str(abs(int(pro_gap))) + 'g'})")
        lines.append(f"Sugar: {total_sugar:.0f}g / {SUGAR_TARGET:.0f}g ({'under ✅' if sugar_gap >= 0 else 'over by ' + str(abs(int(sugar_gap))) + 'g ⚠️'})")

        # Weekly gym progress
        today = date.today()
        days_left = 7 - today.weekday()  # Mon=7 … Sun=1, always includes today
        gym_done = sheets.get_week_gym_days()
        gym_needed = max(0, DEFAULT_GYM_SESSIONS_WEEK - gym_done)
        lines.append(f"\n*Gym this week:* {gym_done} / {DEFAULT_GYM_SESSIONS_WEEK} sessions")
        if gym_needed == 0:
            lines.append("Weekly gym target hit ✅")
        else:
            lines.append(f"{gym_needed} more session{'s' if gym_needed > 1 else ''} needed · {days_left} day{'s' if days_left != 1 else ''} left this week")

        await reply("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "food query"))


# ── Stats / summary query ─────────────────────────────────────────────────────

async def _handle_stats_query(text: str, reply, ctx=None, skip_confirm: bool = False):
    """Full daily or weekly summary — food + gym + sleep + body."""
    try:
        from datetime import date, timedelta
        import re as _re

        lower = text.lower()
        is_weekly = bool(_re.search(r"\b(this\s+week|weekly|week)\b", lower))

        if is_weekly:
            await _handle_week_stats(reply)
            return

        # Determine target date — flexible yesterday matching
        log_date = claude_ai.extract_log_date(text)
        if not log_date:
            if _re.search(r"\byesterday", lower) or _re.search(r"\byest\b", lower):
                log_date = (date.today() - timedelta(days=1)).isoformat()
            else:
                log_date = date.today().isoformat()

        # ── Confirm date before fetching ─────────────────────────────────────
        if not skip_confirm:
            d_obj = date.fromisoformat(log_date)
            date_fmt = d_obj.strftime("%a, %d %b")
            if ctx:
                ctx.user_data["pending_stats_date"] = log_date
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"✅ {date_fmt}", callback_data="confirm_stats_date"),
                InlineKeyboardButton("📅 Different day", callback_data="pick_stats_date"),
            ]])
            await reply(f"Pulling stats for *{date_fmt}* — right?",
                        parse_mode="Markdown", reply_markup=keyboard)
            return

        await _fetch_and_show_stats(log_date, reply)

    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "stats query"))


async def _fetch_and_show_stats(log_date: str, reply):
    """Fetch and display the full daily summary for a given date."""
    try:
        from datetime import date, timedelta
        food_rows = sheets.get_food_by_date(log_date)
        gym_rows  = sheets.get_gym_by_date(log_date)
        sleep_row = sheets.get_sleep_by_date(log_date)
        body_row  = sheets.get_body_by_date(log_date)

        d_obj = date.fromisoformat(log_date)
        date_fmt = d_obj.strftime("%a, %d %b")
        if log_date == date.today().isoformat():
            label = f"Today — {date_fmt}"
        elif log_date == (date.today() - timedelta(days=1)).isoformat():
            label = f"Yesterday — {date_fmt}"
        else:
            label = date_fmt

        lines = [f"*{label}*\n"]

        # ── Food ──────────────────────────────────────────────────────────────
        if food_rows:
            total_cal   = sum(int(r.get("Calories", 0)) for r in food_rows)
            total_pro   = sum(float(r.get("Protein", 0)) for r in food_rows)
            total_carbs = sum(float(r.get("Carbs", 0)) for r in food_rows)
            total_fats  = sum(float(r.get("Fats", 0)) for r in food_rows)
            total_sugar = sum(sheets._get_sugar(r) for r in food_rows)

            cal_gap = DEFAULT_CALORIES - total_cal
            pro_gap = DEFAULT_PROTEIN - total_pro
            SUGAR_TARGET = 25.0

            lines.append("🍱 *Food*")
            # Compact per-meal breakdown
            current_meal = None
            for r in food_rows:
                meal = str(r.get("Meal Type", "")).capitalize()
                meal_name = str(r.get("Meal", ""))
                cal = int(r.get("Calories", 0))
                pro = float(r.get("Protein", 0))
                if meal != current_meal:
                    current_meal = meal
                    lines.append(f"_{meal}_")
                lines.append(f"  • {meal_name} — {cal}cal · {pro:.0f}g P")
            lines.append(
                f"*Total: {total_cal} / {DEFAULT_CALORIES} cal "
                f"({'↓' + str(abs(int(cal_gap))) if cal_gap > 0 else '↑' + str(abs(int(cal_gap)))})*"
            )
            lines.append(
                f"Protein: {total_pro:.0f}g / {DEFAULT_PROTEIN}g "
                f"({'↓' + str(abs(int(pro_gap))) + 'g' if pro_gap > 0 else '✅'})"
            )
            lines.append(f"Carbs: {total_carbs:.0f}g · Fats: {total_fats:.0f}g")
            sugar_status = "✅" if total_sugar <= SUGAR_TARGET else f"⚠️ over by {total_sugar - SUGAR_TARGET:.0f}g"
            lines.append(f"Sugar: {total_sugar:.0f}g / {SUGAR_TARGET:.0f}g {sugar_status}")
        else:
            lines.append("🍱 *Food* — nothing logged")

        lines.append("")

        # ── Gym ───────────────────────────────────────────────────────────────
        if gym_rows:
            exercises = list({str(r.get("Exercise", "")) for r in gym_rows if r.get("Exercise")})
            lines.append(f"🏋️ *Gym* — {len(gym_rows)} sets · {len(exercises)} exercise(s)")
            for ex in exercises:
                ex_sets = [r for r in gym_rows if str(r.get("Exercise", "")) == ex]
                w = ex_sets[-1].get("Weight", "")
                reps = ex_sets[-1].get("Reps", "")
                lines.append(f"  • {ex} — {w}kg {len(ex_sets)}x{reps}")
        else:
            lines.append("🏋️ *Gym* — rest day")

        lines.append("")

        # ── Sleep ─────────────────────────────────────────────────────────────
        if sleep_row:
            h = float(sleep_row.get("Hours", 0))
            notes = (sleep_row.get("Notes") or sleep_row.get("Quality") or "").strip()
            lines.append(f"😴 *Sleep* — {h}h" + (f" · _{notes}_" if notes else ""))
        else:
            lines.append("😴 *Sleep* — not logged")

        lines.append("")

        # ── Body check-in ─────────────────────────────────────────────────────
        if body_row and (body_row.get("Weight (kg)") or body_row.get("Body Feel")):
            w = body_row.get("Weight (kg)", "")
            bf = body_row.get("Body Fat (%)", "")
            tags = body_row.get("Body Feel", "")
            parts = []
            if w:
                parts.append(f"{w}kg")
            if bf:
                parts.append(f"BF {bf}%")
            if tags:
                parts.append(f"_{tags}_")
            lines.append("⚖️ *Body* — " + " · ".join(parts))
            lines.append("")

        # ── Gym week progress ─────────────────────────────────────────────────
        today = date.today()
        days_left = 7 - today.weekday()  # Mon=7 … Sun=1, always includes today
        gym_done = sheets.get_week_gym_days()
        gym_needed = max(0, DEFAULT_GYM_SESSIONS_WEEK - gym_done)
        lines.append(f"📅 *Gym this week:* {gym_done} / {DEFAULT_GYM_SESSIONS_WEEK}")
        if gym_needed == 0:
            lines.append("Weekly target hit ✅")
        else:
            lines.append(f"{gym_needed} more needed · {days_left} day{'s' if days_left != 1 else ''} left")

        # Show "Done for Day" button only on today's stats
        kb = None
        if log_date == date.today().isoformat():
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Done for Day", callback_data="menu_done_day")
            ]])

        await reply("\n".join(lines), parse_mode="Markdown", reply_markup=kb)

    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "stats fetch"))


async def _handle_week_stats(reply):
    """Weekly averages summary."""
    try:
        from datetime import date
        food = sheets.get_week_food()
        gym  = sheets.get_week_gym()

        lines = ["*This Week's Summary*\n"]

        if food:
            days_food = len({r.get("Date") for r in food})
            avg_cal  = sum(int(r.get("Calories", 0)) for r in food) / max(days_food, 1)
            avg_pro  = sum(float(r.get("Protein", 0)) for r in food) / max(days_food, 1)
            avg_carb = sum(float(r.get("Carbs", 0)) for r in food) / max(days_food, 1)
            avg_fat  = sum(float(r.get("Fats", 0)) for r in food) / max(days_food, 1)
            avg_sugar = sum(sheets._get_sugar(r) for r in food) / max(days_food, 1)
            lines.append(f"🍱 *Food* (avg over {days_food} day{'s' if days_food != 1 else ''})")
            lines.append(f"Calories: {avg_cal:.0f} / {DEFAULT_CALORIES}")
            lines.append(f"Protein: {avg_pro:.0f}g / {DEFAULT_PROTEIN}g")
            lines.append(f"Carbs: {avg_carb:.0f}g · Fats: {avg_fat:.0f}g · Sugar: {avg_sugar:.0f}g")
        else:
            lines.append("🍱 *Food* — nothing logged this week")

        lines.append("")

        gym_days = len({r.get("Date") for r in gym})
        today = date.today()
        days_left = 7 - today.weekday()  # Mon=7 … Sun=1, includes today
        gym_needed = max(0, DEFAULT_GYM_SESSIONS_WEEK - gym_days)
        lines.append(f"🏋️ *Gym:* {gym_days} / {DEFAULT_GYM_SESSIONS_WEEK} sessions")
        if gym_needed == 0:
            lines.append("Weekly target hit ✅")
        else:
            lines.append(f"{gym_needed} more needed · {days_left} day{'s' if days_left != 1 else ''} left")

        await reply("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "week stats"))


# ── Done for the day ─────────────────────────────────────────────────────────

async def _handle_done_for_day(reply):
    """Compact end-of-day wrap."""
    try:
        from datetime import date, timedelta
        today = date.today()
        today_str = today.isoformat()
        yesterday_str = (today - timedelta(days=1)).isoformat()
        days_left = 7 - today.weekday()
        SUGAR_TARGET = 25.0

        food_rows  = sheets.get_today_food()
        gym_rows   = sheets.get_today_gym()
        sleep_row  = sheets.get_today_sleep()
        yest_food  = sheets.get_food_by_date(yesterday_str)
        yest_sleep = sheets.get_sleep_by_date(yesterday_str)
        yest_gym   = sheets.get_gym_by_date(yesterday_str)

        date_fmt = today.strftime("%a, %d %b")
        lines = [f"*End of Day — {date_fmt}*\n"]

        # ── Food: creative story + 2 stat lines ───────────────────────────────
        total_cal   = sum(int(r.get("Calories", 0)) for r in food_rows)
        total_pro   = sum(float(r.get("Protein", 0)) for r in food_rows)
        total_carbs = sum(float(r.get("Carbs", 0)) for r in food_rows)
        total_fats  = sum(float(r.get("Fats", 0)) for r in food_rows)
        total_sugar = sum(sheets._get_sugar(r) for r in food_rows)
        cal_gap  = DEFAULT_CALORIES - total_cal
        pro_gap  = DEFAULT_PROTEIN - total_pro

        lines.append("🍱 *Food*")
        if food_rows:
            all_meals = ", ".join(str(r.get("Meal", "")) for r in food_rows if r.get("Meal"))
            try:
                story = claude_ai.generate_food_day_story(all_meals, total_cal, DEFAULT_CALORIES, total_pro, DEFAULT_PROTEIN)
            except Exception:
                story = "A full day of fuel in the books."
            lines.append(f"_{story}_")
            cal_str = f"{total_cal} / {DEFAULT_CALORIES} cal · {'✅' if cal_gap >= 0 else f'↑{abs(int(cal_gap))} over'}"
            pro_str = f"{total_pro:.0f}g P · {'✅' if pro_gap <= 0 else f'↓{abs(int(pro_gap))}g short'}"
            macro_str = f"C {total_carbs:.0f}g · F {total_fats:.0f}g · Sugar {total_sugar:.0f}g {'✅' if total_sugar <= SUGAR_TARGET else f'⚠️ +{total_sugar - SUGAR_TARGET:.0f}g'}"
            lines.append(f"{cal_str} · {pro_str}")
            lines.append(macro_str)
        else:
            lines.append("_Nothing logged today._")
        lines.append("")

        # ── Gym: one line ─────────────────────────────────────────────────────
        lines.append("🏋️ *Gym*")
        if gym_rows:
            strength_rows = [r for r in gym_rows if str(r.get("Type", "strength")).lower() != "cardio"]
            cardio_rows   = [r for r in gym_rows if str(r.get("Type", "")).lower() == "cardio"]
            parts = []
            if strength_rows: parts.append(f"Strength training ✅")
            if cardio_rows:
                mins = sum(int(r.get("Duration (min)", 0) or 0) for r in cardio_rows)
                parts.append(f"{mins}min cardio ✅")
            lines.append(" · ".join(parts))
        else:
            lines.append("Rest day")
        lines.append("")

        # ── Sleep: one line ───────────────────────────────────────────────────
        lines.append("😴 *Sleep*")
        if sleep_row:
            h = float(sleep_row.get("Hours", 0))
            label = "High 🟢" if h >= 7 else "Medium 🟡" if h >= 6 else "Low 🔴"
            lines.append(f"{h}h · {label}")
        else:
            lines.append("Not logged yet")
        lines.append("")

        # ── vs Yesterday: delta + one-liner ──────────────────────────────────
        yest_cal = sum(int(r.get("Calories", 0)) for r in yest_food)
        yest_pro = sum(float(r.get("Protein", 0)) for r in yest_food)
        yest_sug = sum(sheets._get_sugar(r) for r in yest_food)
        yest_h   = float(yest_sleep.get("Hours", 0)) if yest_sleep else None

        if yest_food or yest_sleep:
            parts = []
            if yest_cal: parts.append(f"Cal {'↑' if total_cal>yest_cal else '↓'}{abs(total_cal-yest_cal)}")
            if yest_pro: parts.append(f"P {'↑' if total_pro>yest_pro else '↓'}{abs(total_pro-yest_pro):.0f}g")
            if yest_sug or total_sugar: parts.append(f"Sugar {'↑' if total_sugar>yest_sug else '↓'}{abs(total_sugar-yest_sug):.0f}g")
            if yest_h and sleep_row: parts.append(f"Sleep {'↑' if float(sleep_row.get('Hours',0))>yest_h else '↓'}{abs(float(sleep_row.get('Hours',0))-yest_h):.1f}h")
            if not yest_gym and gym_rows: parts.append("Gym: rest→active 💪")
            elif yest_gym and not gym_rows: parts.append("Gym: active→rest")
            lines.append(f"📊 *vs Yesterday* — {' · '.join(parts)}" if parts else "📊 *vs Yesterday* — no comparable data")
        lines.append("")

        # ── This week: all 5 targets ──────────────────────────────────────────
        gym_done    = sheets.get_week_gym_days()
        cardio_done = sheets.get_week_cardio_sessions(DEFAULT_CARDIO_MIN)
        food_week   = sheets.get_week_food()
        sleep_week  = sheets.get_week_sleep()

        # Days hitting calorie target (≤ DEFAULT_CALORIES)
        food_by_day: dict = {}
        for r in food_week:
            d = r.get("Date", "")
            if d not in food_by_day:
                food_by_day[d] = {"cal": 0, "pro": 0}
            food_by_day[d]["cal"] += int(r.get("Calories", 0))
            food_by_day[d]["pro"] += float(r.get("Protein", 0))
        days_logged = len(food_by_day) or 1
        days_cal_hit = sum(1 for v in food_by_day.values() if v["cal"] <= DEFAULT_CALORIES)
        days_pro_hit = sum(1 for v in food_by_day.values() if v["pro"] >= DEFAULT_PROTEIN)

        # Days hitting sleep target (≥ 7h)
        days_sleep_hit = sum(1 for r in sleep_week if float(r.get("Hours", 0)) >= 7)
        days_sleep_logged = len(sleep_week)

        gym_tick     = "✅" if gym_done    >= DEFAULT_GYM_SESSIONS_WEEK    else f"({max(0, DEFAULT_GYM_SESSIONS_WEEK - gym_done)} more · {days_left}d left)"
        cardio_tick  = "✅" if cardio_done >= DEFAULT_CARDIO_SESSIONS_WEEK else f"({max(0, DEFAULT_CARDIO_SESSIONS_WEEK - cardio_done)} more · {days_left}d left)"
        cal_tick     = "✅" if days_cal_hit >= days_logged                  else f"({days_logged - days_cal_hit} day{'s' if days_logged - days_cal_hit != 1 else ''} over)"
        pro_tick     = "✅" if days_pro_hit >= days_logged                  else f"({days_logged - days_pro_hit} day{'s' if days_logged - days_pro_hit != 1 else ''} short)"
        sleep_tick   = "✅" if days_sleep_hit > 0 and days_sleep_hit >= days_sleep_logged else f"({days_sleep_logged - days_sleep_hit}/{days_sleep_logged} days under 7h)"

        lines.append(f"*This week so far*")
        lines.append(f"  💪 Strength: {gym_done}/{DEFAULT_GYM_SESSIONS_WEEK} sessions {gym_tick}")
        lines.append(f"  🏃 Cardio: {cardio_done}/{DEFAULT_CARDIO_SESSIONS_WEEK} sessions {cardio_tick}")
        lines.append(f"  🍱 Calories: {days_cal_hit}/{days_logged} days on target {cal_tick}")
        lines.append(f"  🥩 Protein: {days_pro_hit}/{days_logged} days on target {pro_tick}")
        lines.append(f"  😴 Sleep 7h+: {days_sleep_hit}/{days_sleep_logged} days {sleep_tick}")
        lines.append("")

        # ── Coach push ────────────────────────────────────────────────────────
        day_str  = f"Cal {total_cal}/{DEFAULT_CALORIES}, P {total_pro:.0f}g/{DEFAULT_PROTEIN}g, Sugar {total_sugar:.0f}g, Gym: {'yes' if gym_rows else 'rest'}, Sleep: {sleep_row.get('Hours','?') if sleep_row else 'not logged'}"
        week_str = (f"Gym {gym_done}/{DEFAULT_GYM_SESSIONS_WEEK}, Cardio {cardio_done}/{DEFAULT_CARDIO_SESSIONS_WEEK}, "
                    f"Cal on-target {days_cal_hit}/{days_logged}d, Protein on-target {days_pro_hit}/{days_logged}d, "
                    f"Sleep 7h+ {days_sleep_hit}/{days_sleep_logged}d, {days_left}d left this week")
        yest_str = f"Cal {yest_cal}, P {yest_pro:.0f}g, Sugar {yest_sug:.0f}g, Gym: {'yes' if yest_gym else 'rest'}" if (yest_food or yest_gym) else ""
        try:
            note = claude_ai.generate_end_of_day_coaching(day_str, week_str, yest_str)
        except Exception:
            note = "Day's done. Rest up and go again tomorrow."

        lines.append(f"_{note}_")
        await reply("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "end of day"))


# ── Quest check ───────────────────────────────────────────────────────────────

async def _handle_quest_check(reply):
    """Show what's left to hit this week's targets."""
    try:
        from datetime import date, timedelta
        today = date.today()
        days_elapsed = today.weekday() + 1      # Mon=1 … Sun=7
        days_left = 7 - today.weekday()         # Mon=7 … Sun=1, includes today

        food_week  = sheets.get_week_food()
        gym_week   = sheets.get_week_gym()

        # ── Gym + Cardio ───────────────────────────────────────────────────────
        gym_days  = sheets.get_week_gym_days()
        gym_needed = max(0, DEFAULT_GYM_SESSIONS_WEEK - gym_days)
        cardio_done = sheets.get_week_cardio_sessions(DEFAULT_CARDIO_MIN)
        cardio_needed = max(0, DEFAULT_CARDIO_SESSIONS_WEEK - cardio_done)
        gym_line = (
            f"🏋️ Gym: {gym_days} / {DEFAULT_GYM_SESSIONS_WEEK} sessions — "
            + ("✅" if gym_needed == 0 else f"{gym_needed} more to go · {days_left}d left")
        )
        cardio_line = (
            f"🏃 Cardio (≥{DEFAULT_CARDIO_MIN}min): {cardio_done} / {DEFAULT_CARDIO_SESSIONS_WEEK} — "
            + ("✅" if cardio_needed == 0 else f"{cardio_needed} more to go · {days_left}d left")
        )

        # ── Nutrition (daily averages so far this week) ───────────────────────
        food_days = len({r.get("Date") for r in food_week}) or 1
        avg_cal  = sum(int(r.get("Calories", 0)) for r in food_week) / food_days
        avg_pro  = sum(float(r.get("Protein", 0)) for r in food_week) / food_days
        avg_sugar = sum(sheets._get_sugar(r) for r in food_week) / food_days
        SUGAR_TARGET = 25.0

        cal_status = "✅" if avg_cal <= DEFAULT_CALORIES else f"⚠️ avg {avg_cal:.0f} (over by {avg_cal - DEFAULT_CALORIES:.0f})"
        pro_status = "✅" if avg_pro >= DEFAULT_PROTEIN else f"❌ avg {avg_pro:.0f}g (need +{DEFAULT_PROTEIN - avg_pro:.0f}g/day)"
        sugar_status = "✅" if avg_sugar <= SUGAR_TARGET else f"⚠️ avg {avg_sugar:.0f}g (over by {avg_sugar - SUGAR_TARGET:.0f}g)"

        # ── Sleep ─────────────────────────────────────────────────────────────
        from datetime import timedelta
        week_start = today - timedelta(days=today.weekday())
        sleep_days_hit = 0
        for i in range(days_elapsed):
            d = (week_start + timedelta(days=i)).isoformat()
            row = sheets.get_sleep_by_date(d)
            if row and float(row.get("Hours", 0)) >= 7:
                sleep_days_hit += 1
        sleep_line = (
            f"😴 Sleep 7h+: {sleep_days_hit} / {days_elapsed} days logged"
            + (" ✅" if sleep_days_hit == days_elapsed else f" · {days_elapsed - sleep_days_hit} night(s) under 7h")
        )

        # ── Build message ─────────────────────────────────────────────────────
        lines = [f"*🎯 Quest Check — Week {today.strftime('%b %d')}*\n"]
        lines.append(gym_line)
        lines.append(cardio_line)
        lines.append(f"🍱 Calories: {cal_status}")
        lines.append(f"💪 Protein: {pro_status}")
        lines.append(f"🍬 Sugar: {sugar_status}")
        lines.append(sleep_line)

        # Summary verdict
        quests_done = sum([
            gym_needed == 0,
            cardio_needed == 0,
            avg_pro >= DEFAULT_PROTEIN,
            avg_cal <= DEFAULT_CALORIES,
            avg_sugar <= SUGAR_TARGET,
        ])
        lines.append(f"\n*{quests_done} / 5 quests on track.*")
        if days_left > 0:
            lines.append(f"{days_left} day{'s' if days_left != 1 else ''} left to close the gap.")
        else:
            lines.append("Week's done. Quest continues Monday.")

        await reply("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        log.error(traceback.format_exc()); await reply(_safe_error(e, "quest check"))


# ── Photo handler ─────────────────────────────────────────────────────────────

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    caption = (update.message.caption or "").strip()
    reply = update.message.reply_text

    if caption:
        # Route by caption intent — photo could be food, gym, body, etc.
        from src.router import classify_intent
        intent = classify_intent(caption)
        if intent == "meal":
            await _log_meal_text(caption, reply, ctx=ctx)
        elif intent == "gym":
            _HAS_EXERCISE_DATA = re.compile(r'\d+\s*(kg|x\d|min\b|lbs|reps?)', re.IGNORECASE)
            if _HAS_EXERCISE_DATA.search(caption):
                await _log_gym_session(caption, ctx, reply)
            else:
                await _show_gym_list(update, ctx, set_name_hint=caption)
        elif intent == "body_check":
            await _log_body_checkin(caption, reply, ctx=ctx)
        elif intent == "recovery":
            await _log_recovery(caption, reply, ctx=ctx)
        else:
            # Unknown — show menu so user can pick what to log
            await reply("What's this for?", reply_markup=_main_menu_keyboard())
    else:
        # No caption — ask what this is about
        await reply("What's this? Let me know what to log 👇", reply_markup=_main_menu_keyboard())


# ── Commands ──────────────────────────────────────────────────────────────────

def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🍱 Log Food",       callback_data="menu_log_food"),
            InlineKeyboardButton("🏋️ Log Gym",        callback_data="menu_log_gym"),
        ],
        [
            InlineKeyboardButton("😴 Log Sleep",      callback_data="menu_log_sleep"),
            InlineKeyboardButton("💬 Log Mood",       callback_data="menu_log_mood"),
        ],
        [
            InlineKeyboardButton("⚖️ Log Body",       callback_data="menu_log_body"),
            InlineKeyboardButton("🔴 Log Period",     callback_data="menu_log_period"),
        ],
        [
            InlineKeyboardButton("📊 Today",          callback_data="menu_today"),
            InlineKeyboardButton("📅 Yesterday",      callback_data="menu_yesterday"),
        ],
        [
            InlineKeyboardButton("📈 This Week",      callback_data="menu_week"),
            InlineKeyboardButton("🗓 Pick a Day",     callback_data="menu_pick_day"),
        ],
        [
            InlineKeyboardButton("🎯 Quest Check",    callback_data="menu_quest"),
        ],
    ])


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    await update.message.reply_text(
        "What are we doing?", reply_markup=_main_menu_keyboard()
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    await update.message.reply_text(
        "Buff Buddy online. Game time.\n\n"
        "Talk to me naturally, or tap /menu for quick options.\n\n"
        "Commands: /menu /summary /week /weight /goals /recovery /streak /pb",
        parse_mode="Markdown",
    )


async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    totals = sheets.get_today_totals()
    sleep = sheets.get_today_sleep()
    gym = sheets.get_today_gym()
    cycle_day, phase = sheets.get_cycle_info()

    from datetime import date as _date
    sleep_notes = (sleep.get("Notes") or sleep.get("Quality") or "").strip() if sleep else ""
    sleep_str = f"{sleep['Hours']}h · {sleep_notes}" if sleep and sleep_notes else (f"{sleep['Hours']}h" if sleep else "Not logged")
    gym_str = f"{len(gym)} sets today" if gym else "Rest day"
    cycle_str = f"Day {cycle_day} · {phase}" if cycle_day else "—"
    gym_week = sheets.get_week_gym_days()
    gym_needed = max(0, DEFAULT_GYM_SESSIONS_WEEK - gym_week)
    days_left = 7 - _date.today().weekday()  # Mon=7 … Sun=1, includes today
    gym_week_str = f"{gym_week} / {DEFAULT_GYM_SESSIONS_WEEK} sessions this week"
    if gym_needed > 0:
        gym_week_str += f" · {gym_needed} more needed · {days_left}d left"
    else:
        gym_week_str += " ✅"

    msg = (
        "*The Scoreboard*\n"
        f"Calories: {totals['calories']} / {DEFAULT_CALORIES}\n"
        f"Protein: {totals['protein']:.0f}g / {DEFAULT_PROTEIN}g\n"
        f"Carbs: {totals['carbs']:.0f}g · Fats: {totals['fats']:.0f}g\n"
        f"Sleep: {sleep_str}\n"
        f"Training: {gym_str}\n"
        f"Gym week: {gym_week_str}\n"
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


async def _log_content(text: str, reply):
    """Log a content idea/thought — auto-classify pillar, angle, week, suggest angle."""
    try:
        from datetime import date
        days_in = (date.today() - _TRANSFORM_START).days
        week_num = max(1, min(8, days_in // 7 + 1))

        # Auto-classify via Claude
        parsed = claude_ai.parse_content_idea(text, week_num)
        pillar          = parsed.get("pillar", "?")
        angle           = parsed.get("angle", "?")
        suggested_angle = parsed.get("suggested_angle", "")

        # Confirm week if before or after the 8-week window
        week_str = f"Week {week_num}" if 1 <= week_num <= 8 else f"Week ? (day {days_in})"

        # Log — raw note stored verbatim
        sheets.log_content(
            raw_note=text,
            week_num=week_str,
            pillar=pillar,
            angle=angle,
            suggested_angle=suggested_angle,
        )

        await reply(
            f"📝 *Logged* — {week_str} · {pillar}\n"
            f"_{angle}_\n\n"
            f"💡 _{suggested_angle}_",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(traceback.format_exc())
        await reply(_safe_error(e, "content log"))


async def cmd_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show content log — optionally filter by week or pillar.
    Usage: /content  /content week 3  /content training  /content transformation
    """
    if not _is_authorised(update):
        return await _deny(update)
    try:
        args = " ".join(ctx.args).strip().lower() if ctx.args else ""
        rows = sheets.get_content_log(limit=100)

        # Filter
        if args:
            import re as _re
            week_match = _re.search(r'week\s*(\d+)', args)
            if week_match:
                wk = f"Week {week_match.group(1)}"
                rows = [r for r in rows if wk.lower() in str(r.get("Week #", "")).lower()]
            else:
                # Filter by pillar keyword
                rows = [r for r in rows if args in str(r.get("Pillar", "")).lower()]

        if not rows:
            await update.message.reply_text("No content ideas found for that filter.")
            return

        # Group by week
        from collections import defaultdict
        by_week: dict = defaultdict(list)
        for r in rows:
            by_week[r.get("Week #", "?")].append(r)

        lines = ["*Content Log*" + (f" — {args}" if args else "") + "\n"]
        for wk in sorted(by_week.keys()):
            lines.append(f"*{wk}*")
            for r in by_week[wk]:
                d = str(r.get("Date", ""))[:10]
                pillar = str(r.get("Pillar", ""))
                angle  = str(r.get("Angle", ""))
                note   = str(r.get("Raw Note", ""))[:80] + ("…" if len(str(r.get("Raw Note", ""))) > 80 else "")
                suggestion = str(r.get("Suggested Angle", ""))
                lines.append(f"  📅 {d} · _{pillar}_")
                lines.append(f"  {angle}")
                lines.append(f"  \"{note}\"")
                if suggestion:
                    lines.append(f"  💡 {suggestion}")
                lines.append("")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(traceback.format_exc())
        await update.message.reply_text(_safe_error(e, "content view"))


async def cmd_weeklysummary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manually trigger the weekly report for the current/previous week."""
    if not _is_authorised(update):
        return await _deny(update)
    await update.message.reply_text("Generating weekly report… ⏳")
    try:
        from src.scheduler import _weekly_report
        await _weekly_report(ctx.bot)
    except Exception as e:
        log.error(traceback.format_exc())
        await update.message.reply_text(_safe_error(e, "weekly report"))


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
    h = float(sleep["Hours"])
    notes = (sleep.get("Notes") or sleep.get("Quality") or "").strip()
    recovery = "High" if h >= 7 else "Medium" if h >= 6 else "Low"
    msg = f"Recovery: *{recovery}* — {h}h"
    if notes:
        msg += f"\n_{notes}_"
    await update.message.reply_text(msg, parse_mode="Markdown")


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


async def cmd_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update):
        return await _deny(update)
    rows = sheets.get_body_trend(days=7)
    weight_rows = [r for r in rows if r.get("Weight (kg)")]
    if not weight_rows:
        await update.message.reply_text("No weight logged in the last 7 days.")
        return

    weights = [float(r["Weight (kg)"]) for r in weight_rows]
    bmis = [float(r["BMI"]) for r in weight_rows if r.get("BMI")]
    avg_w = round(sum(weights) / len(weights), 1)
    min_w = min(weights)
    max_w = max(weights)
    avg_bmi = round(sum(bmis) / len(bmis), 1) if bmis else None

    # Last body composition entry
    bf_rows = [r for r in rows if r.get("Body Fat (%)")]
    bf_line = ""
    if bf_rows:
        last = bf_rows[-1]
        last_bf = float(last["Body Fat (%)"])
        last_lm = last.get("Lean Mass (kg)", "")
        last_sm = last.get("Skeletal Muscle (kg)", "")
        last_fm = last.get("Fat Mass (kg)", "")
        last_vf = last.get("Visceral Fat Level", "")
        parts = [f"Body fat: {last_bf}%"]
        if last_fm:
            parts.append(f"Fat mass: {last_fm} kg")
        if last_lm:
            parts.append(f"Lean: {last_lm} kg")
        if last_sm:
            parts.append(f"Muscle: {last_sm} kg")
        if last_vf:
            parts.append(f"Visceral: {last_vf}")
        bf_line = "\n" + " · ".join(parts)

    # Feel tag frequency this week
    all_tags: list[str] = []
    for r in rows:
        tags_str = str(r.get("Body Feel", "")).strip()
        if tags_str:
            all_tags.extend([t.strip() for t in tags_str.split(",")])
    tag_counts: dict[str, int] = {}
    for t in all_tags:
        if t:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:3]
    tags_line = "\nTop feel tags: " + " · ".join(f"{t} ({c}x)" for t, c in top_tags) if top_tags else ""

    trend_lines = []
    for r in weight_rows[-7:]:
        w = r.get("Weight (kg)", "")
        d = r.get("Date", "")
        tags = str(r.get("Body Feel", "")).strip()
        line = f"  {d}: {w} kg"
        if tags:
            line += f" — {tags}"
        trend_lines.append(line)

    msg = (
        f"*7-day Weight Trend*\n"
        f"Avg: {avg_w} kg · Min: {min_w} kg · Max: {max_w} kg\n"
        f"BMI: {avg_bmi} ({_bmi_category(avg_bmi)})" if avg_bmi else f"*7-day Weight Trend*\n"
        f"Avg: {avg_w} kg · Min: {min_w} kg · Max: {max_w} kg"
    )
    if avg_bmi:
        msg = (
            f"*7-day Weight Trend*\n"
            f"Avg: {avg_w} kg · Min: {min_w} · Max: {max_w}\n"
            f"BMI: avg {avg_bmi} ({_bmi_category(avg_bmi)})"
            f"{bf_line}{tags_line}\n\n"
            + "\n".join(trend_lines)
        )
    else:
        msg = (
            f"*7-day Weight Trend*\n"
            f"Avg: {avg_w} kg · Min: {min_w} · Max: {max_w}"
            f"{bf_line}{tags_line}\n\n"
            + "\n".join(trend_lines)
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


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
