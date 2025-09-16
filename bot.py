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
REQUIRED_JOINS = 2  # number of users required before advancing to next channel
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
        "channels": [{"pending": [], "counted": [], "joined": 0} for _ in CHANNELS],
        "users": {},   # track per-user progress
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
        state["channels"] = [{"pending": [], "counted": [], "joined": 0} for _ in CHANNELS]
    while len(state["channels"]) < len(CHANNELS):
        state["channels"].append({"pending": [], "counted": [], "joined": 0})
    if len(state["channels"]) > len(CHANNELS):
        state["channels"] = state["channels"][: len(CHANNELS)]

    if "users" not in state:
        state["users"] = {}

    return state


def save_state(state: Dict[str, Any]):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.exception("Failed to save state: %s", e)


# ---------- Helpers ----------
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


def advance_if_needed(channel_idx: int) -> bool:
    """Advance global active_index if enough users joined this channel."""
    state = load_state()
    ch = state["channels"][channel_idx]

    if ch["joined"] >= REQUIRED_JOINS:
        state["active_index"] = (channel_idx + 1) % len(CHANNELS)
        save_state(state)
        logger.info(f"🚀 Channel {channel_idx+1} completed. Now active: {state['active_index']+1}")
        return True
    return False


# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)

    state = load_state()
    idx = state["active_index"]

    # set user state if new
    if user_id not in state["users"]:
        state["users"][user_id] = {"current_channel": idx}
        save_state(state)

    channel = CHANNELS[idx]
    invite = channel["invite"]
    channel_id = channel["id"]

    if await is_member(context.bot, channel_id, user.id):
        await update.message.reply_text(
            f"✅ You are already a member of Channel {idx+1}. Sending files:"
        )
        await send_channel_files(update.message, idx)
        return

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
    user_id = str(user.id)
    state = load_state()

    # determine current channel
    idx = state["active_index"]
    channel = CHANNELS[idx]
    channel_id = channel["id"]

    # check membership
    if not await is_member(context.bot, channel_id, user.id):
        await query.message.reply_text("❌ I still don't see you as a member. Try again.")
        return

    # count this join
    state["channels"][idx]["joined"] += 1
    state["users"][user_id] = {"current_channel": idx}
    save_state(state)

    await query.message.reply_text(
        f"✅ Verified! You are counted for Channel {idx+1}. Sending files..."
    )
    await send_channel_files(query.message, idx)

    # check if channel can advance
    if advance_if_needed(idx):
        next_idx = load_state()["active_index"]
        await query.message.reply_text(
            f"🚀 Channel {idx+1} completed after {REQUIRED_JOINS} users. "
            f"Next active channel: {next_idx+1}."
        )


# ---------- Main ----------
tg_app = Application.builder().token(BOT_TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^verify_"))

if __name__ == "__main__":
    import asyncio
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    async def main():
        # Start Telegram bot
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling()
        logger.info("🚀 Bot started and polling...")

        # Start Flask (via Hypercorn)
        port = int(os.getenv("PORT", 5000))
        config = Config()
        config.bind = [f"0.0.0.0:{port}"]
        await serve(app, config)

    asyncio.run(main())

