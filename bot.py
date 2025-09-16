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
REQUIRED_JOINS = 2  # 🔑 number of joins needed before advancing
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
        "channels": [{"joined": 0, "counted": []} for _ in CHANNELS],
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
        state["channels"] = [{"joined": 0, "counted": []} for _ in CHANNELS]

    while len(state["channels"]) < len(CHANNELS):
        state["channels"].append({"joined": 0, "counted": []})
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

REQUIRED_JOINS = 2  # change this to any number you like

def advance_if_needed() -> bool:
    """
    Check if current channel has enough joins. If yes, reset and advance.
    Returns True if advanced, False otherwise.
    """
    state = load_state()
    idx = state["active_index"]
    ch = state["channels"][idx]

    if ch.get("joined", 0) >= REQUIRED_JOINS:
        # ✅ reset counter for current channel
        ch["joined"] = 0
        ch["counted"] = []

        # ✅ move to next channel (looping back at end)
        state["active_index"] = (idx + 1) % len(CHANNELS)
        save_state(state)

        logger.info(f"🚀 Channel {idx+1} reached {REQUIRED_JOINS} users. "
                    f"Now moving to Channel {state['active_index']+1}.")
        return True

    save_state(state)
    return False



# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = load_state()
    idx = state["active_index"]

    channel = CHANNELS[idx]
    invite = channel["invite"]
    channel_id = channel["id"]

    progress = f"📊 Progress: {state['channels'][idx]['joined']}/{REQUIRED_JOINS} users verified."

    if await is_member(context.bot, channel_id, user.id):
        await update.message.reply_text(
            f"✅ You are already a member of Channel {idx+1}. Sending files...\n\n{progress}"
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
        f"📢 Please join Channel {idx+1} to unlock the files. After joining, press Verify.\n\n{progress}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    state = load_state()
    idx = state["active_index"]
    ch = state["channels"][idx]

    # ensure fields exist
    if "joined" not in ch:
        ch["joined"] = 0
    if "counted" not in ch:
        ch["counted"] = []

    # avoid double-counting
    if user.id not in ch["counted"]:
        ch["joined"] += 1
        ch["counted"].append(user.id)
        save_state(state)
        logger.info(f"✅ User {user.id} verified in Channel {idx+1}. "
                    f"Total = {ch['joined']}")

    # check if we need to advance
    advanced = advance_if_needed()

    if advanced:
        await query.edit_message_text(
            f"🎉 Channel {idx+1} completed! Now moving to Channel {state['active_index']+1}."
        )
    else:
        await query.edit_message_text(
            f"👍 You’ve been counted in Channel {idx+1}. "
            f"Progress: {ch['joined']}/{REQUIRED_JOINS}."
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


