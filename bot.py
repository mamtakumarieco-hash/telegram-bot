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


def advance_if_needed(user_id: str):
    state = load_state()
    user_state = state["users"].get(user_id, {"current_channel": 0})
    current_channel = user_state["current_channel"]

    # ensure channels dict exists
    if "channels" not in state:
        state["channels"] = {}
    if current_channel not in state["channels"]:
        state["channels"][current_channel] = {"joined": 0}

    # threshold: require 2 joins before advancing
    REQUIRED_JOINS = 2  

    if state["channels"][current_channel]["joined"] >= REQUIRED_JOINS:
        user_state["current_channel"] += 1
        logger.info(f"➡️ Channel {current_channel} reached {REQUIRED_JOINS} users. "
                    f"User {user_id} moved to channel {user_state['current_channel']}.")

    # save back
    state["users"][user_id] = user_state
    save_state(state)



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


async def verify_callback(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    state = load_state()

    # get current channel for this user
    user_state = state["users"].get(user_id, {"current_channel": 0})
    current_channel = user_state["current_channel"]

    # make sure channel counter exists
    if "channels" not in state:
        state["channels"] = {}
    if current_channel not in state["channels"]:
        state["channels"][current_channel] = {"joined": 0}

    # increment channel join count
    state["channels"][current_channel]["joined"] += 1
    logger.info(f"✅ User {user_id} joined channel {current_channel}. "
                f"Total joins = {state['channels'][current_channel]['joined']}")

    # save
    state["users"][user_id] = user_state
    save_state(state)

    # now check if user can advance
    advance_if_needed(user_id)

    await update.message.reply_text("👍 Verified your join!")



# ---------- Main ----------
tg_app = Application.builder().token(BOT_TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^verify_"))

if __name__ == "__main__":
    import asyncio

    async def main():
        # Start the Telegram bot
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling()
        logger.info("🚀 Bot started and polling...")

        # Run Flask inside the same event loop
        port = int(os.getenv("PORT", 5000))
        from hypercorn.asyncio import serve
        from hypercorn.config import Config

        config = Config()
        config.bind = [f"0.0.0.0:{port}"]

        # Run Flask server (Hypercorn) together with bot
        await serve(app, config)

    asyncio.run(main())
