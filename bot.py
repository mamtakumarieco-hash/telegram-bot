#!/usr/bin/env python3
"""
Telegram rotating-channel gatekeeper bot (private channels) + Flask web service (Render).
"""

import os
import json
import logging
import pathlib
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from flask import Flask

# ----------------- CONFIG -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")  # must be set in Render environment variables
if not BOT_TOKEN:
    raise RuntimeError("❌ Please set BOT_TOKEN as an environment variable in Render.")

CHANNELS = [
    {"id": -1002866596290, "invite": "https://t.me/+vkaa61Ruo5Q5Yjk1"},
    {"id": -1002585307628, "invite": "https://t.me/+XqEETQ8WhCpiYmRl"},
    {"id": -1002821688382, "invite": "https://t.me/+hedhygcXrxAxOTA1"},
]

CHANNEL_FILES = [
    {"text": "sample1.txt", "video": "sample1.mp4"},
    {"text": "sample2.txt", "video": "sample2.mp4"},
    {"text": "sample3.txt", "video": "sample3.mp4"},
]

STATE_PATH = pathlib.Path("bot_state.json")
REQUIRED_JOINS = 1  # number of joins needed before advancing
# ------------------------------------------

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

@app.route("/")
def index():
    return "✅ Telegram bot + Flask is running on Render!"


# ---------- State helpers ----------
def default_state() -> Dict[str, Any]:
    return {
        "active_index": 0,
        "channels": [{"pending": [], "counted": []} for _ in CHANNELS],
    }


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as e:
            logger.exception("Failed to load state file, starting fresh: %s", e)
            state = default_state()
    else:
        state = default_state()

    if "active_index" not in state:
        state["active_index"] = 0
    if "channels" not in state or not isinstance(state["channels"], list):
        state["channels"] = [{"pending": [], "counted": []} for _ in CHANNELS]

    while len(state["channels"]) < len(CHANNELS):
        state["channels"].append({"pending": [], "counted": []})
    if len(state["channels"]) > len(CHANNELS):
        state["channels"] = state["channels"][: len(CHANNELS)]

    return state


def save_state(state: Dict[str, Any]):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.exception("Failed to save state: %s", e)


# ---------- Helpers ----------
async def ensure_pending(state: Dict[str, Any], channel_idx: int, user_id: int, bot):
    ch = state["channels"][channel_idx]

    try:
        res = await bot.get_chat_member(chat_id=CHANNELS[channel_idx]["id"], user_id=user_id)
        status = res.status
    except Exception as e:
        logger.warning("get_chat_member failed in ensure_pending: %s", e)
        status = None

    if status in ("left", "kicked") and user_id in ch["counted"]:
        ch["counted"].remove(user_id)

    if user_id not in ch["pending"]:
        ch["pending"].append(user_id)


def mark_counted_if_pending(state: Dict[str, Any], channel_idx: int, user_id: int) -> bool:
    ch = state["channels"][channel_idx]
    if user_id in ch["counted"]:
        return False
    if user_id in ch["pending"]:
        ch["pending"].remove(user_id)
        ch["counted"].append(user_id)
        return True
    return False


def advance_if_needed(state: Dict[str, Any]) -> bool:
    idx = state["active_index"]
    counted_len = len(state["channels"][idx]["counted"])
    if counted_len >= REQUIRED_JOINS:
        state["active_index"] = (idx + 1) % len(CHANNELS)
        logger.info("Advanced active_index to %s", state["active_index"])
        return True
    return False


async def is_member(bot, channel_id: int, user_id: int) -> bool:
    try:
        res = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return res.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("get_chat_member failed for channel_id=%s user=%s: %s", channel_id, user_id, e)
        return False


async def send_channel_files(target, channel_idx: int):
    files = CHANNEL_FILES[channel_idx]

    text_path = files.get("text")
    if text_path:
        text_path = pathlib.Path(text_path)
        if text_path.exists():
            try:
                with open(text_path, "rb") as f:
                    await target.reply_document(f)
            except Exception as e:
                logger.exception("Failed to send text/document: %s", e)

    video_path = files.get("video")
    if video_path:
        video_path = pathlib.Path(video_path)
        if video_path.exists():
            try:
                with open(video_path, "rb") as v:
                    await target.reply_video(video=v)
            except Exception as e:
                logger.exception("Failed to send video: %s", e)


# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = load_state()
    idx = state["active_index"]

    channel = CHANNELS[idx]
    invite = channel["invite"]
    channel_id = channel["id"]

    if await is_member(context.bot, channel_id, user.id):
        newly_counted = mark_counted_if_pending(state, idx, user.id)
        if newly_counted:
            save_state(state)
            advanced = advance_if_needed(state)
            save_state(state)
            if advanced:
                await update.message.reply_text(
                    f"✅ You joined Channel {idx+1} — counted! Channel advanced."
                )
            else:
                await update.message.reply_text(
                    f"✅ You are a member of Channel {idx+1}. Here are your files:"
                )
        else:
            await update.message.reply_text(
                f"✅ You are already a member of Channel {idx+1}. Sending files:"
            )

        await send_channel_files(update.message, idx)
        return

    await ensure_pending(state, idx, user.id, context.bot)
    save_state(state)

    keyboard = [
        [
            InlineKeyboardButton("👉 Join Channel", url=invite),
            InlineKeyboardButton("✅ Verify", callback_data=f"verify_{idx}"),
        ]
    ]
    await update.message.reply_text(
        f"📢 Please join Channel {idx+1} to unlock the files. After joining, press Verify.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data or ""
    parts = data.split("_")
    if len(parts) != 2 or not parts[1].isdigit():
        await query.message.reply_text("⚠️ Invalid verification request.")
        return

    target_idx = int(parts[1])
    state = load_state()

    if target_idx < 0 or target_idx >= len(CHANNELS):
        await query.message.reply_text("⚠️ That channel is no longer available.")
        return

    channel = CHANNELS[target_idx]
    channel_id = channel["id"]

    member = await is_member(context.bot, channel_id, user.id)
    if not member:
        await query.message.reply_text(
            "❌ I still don't see you as a member of that channel. Join and try again."
        )
        return

    newly_counted = mark_counted_if_pending(state, target_idx, user.id)
    if newly_counted:
        save_state(state)
        if state["active_index"] == target_idx:
            advanced = advance_if_needed(state)
            save_state(state)
            await query.message.reply_text(
                f"✅ Verified & counted for Channel {target_idx+1}. Here are your files:"
            )
            await send_channel_files(query.message, target_idx)
            if advanced:
                next_idx = state["active_index"]
                await query.message.reply_text(
                    f"🚀 Channel {target_idx+1} completed. Next active channel: {next_idx+1}."
                )
        else:
            save_state(state)
            await query.message.reply_text(
                f"✅ Verified & counted for Channel {target_idx+1}. Here are your files:"
            )
            await send_channel_files(query.message, target_idx)
    else:
        ch = state["channels"][target_idx]
        if user.id in ch.get("counted", []):
            await query.message.reply_text(
                f"ℹ️ You were already counted for Channel {target_idx+1}. Sending files again:"
            )
            await send_channel_files(query.message, target_idx)
        else:
            await query.message.reply_text(
                f"✅ You are a member of Channel {target_idx+1}, but not in pending list. Not counted, but here are your files:"
            )
            await send_channel_files(query.message, target_idx)


# ---------- Main ----------
tg_app = Application.builder().token(BOT_TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^verify_"))

if __name__ == "__main__":
    import asyncio

    async def run_bot():
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling()
        logger.info("🚀 Bot started and polling...")

    loop = asyncio.get_event_loop()
    loop.create_task(run_bot())

    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
