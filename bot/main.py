import logging

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from shared.config import BOT_TOKEN
from shared.db import add_item, init_db

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx logs each request URL at INFO, which includes the bot token in the path.
# Raise it to WARNING so the token never lands in logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("bot")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.text:
        return

    user = msg.from_user
    sender_name = user.full_name if user else "unknown"
    sender_id = user.id if user else 0

    item_id = add_item(
        text=msg.text,
        sender_name=sender_name,
        sender_id=sender_id,
        chat_id=msg.chat_id,
    )
    logger.info("saved item id=%s sender=%s chat=%s", item_id, sender_name, msg.chat_id)

    await msg.reply_text("✓ got it")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN not set. Copy .env.example to .env and paste your BotFather token."
        )
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("bot starting (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
