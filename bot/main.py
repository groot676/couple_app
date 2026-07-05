import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from shared import db
from shared.config import BOT_TOKEN, DEFAULT_CURRENCY_CODE, DEFAULT_CURRENCY_SYMBOL
from shared.money import fmt_money
from shared.sorter import SorterError, sort_message

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
# httpx logs each request URL at INFO, which includes the bot token in the path.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("bot")


def _currency() -> tuple[str, str]:
    s = db.get_settings() or {}
    return (
        s.get("currency_code") or DEFAULT_CURRENCY_CODE,
        s.get("currency_symbol") or DEFAULT_CURRENCY_SYMBOL,
    )


def _render_confirmation(chat_id: int, tg_message_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    """One compact confirmation for everything captured from one message,
    with a one-tap lane flip per item. Re-rendered in place after overrules."""
    items = [
        i for i in db.items_for_message(chat_id, tg_message_id) if i["status"] == "active"
    ]
    if not items:
        return "✓ saved", None

    code, symbol = _currency()
    lines, buttons = [], []
    numbered = len(items) > 1
    if numbered:
        lines.append(f"✓ {len(items)} things")
    for n, item in enumerate(items, start=1):
        name = item["display_text"] or item["text"]
        parts = [name, "—", "dream" if item["lane"] == "dream" else "everyday"]
        if item["lane"] == "dream" and item["estimated_price"]:
            parts.append(f"· ~{fmt_money(item['estimated_price'], symbol, code)}")
        prefix = f"{n}. " if numbered else "✓ "
        lines.append(prefix + " ".join(parts))

        flip_to = "everyday" if item["lane"] == "dream" else "dream"
        label = f"{n} → " if numbered else ""
        buttons.append(
            [InlineKeyboardButton(f"{label}make it {'a ' if flip_to == 'dream' else ''}{flip_to}",
                                  callback_data=f"flip:{item['id']}")]
        )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.text:
        return

    user = msg.from_user
    sender_name = user.full_name if user else "unknown"
    sender_id = user.id if user else 0
    code, _ = _currency()

    try:
        outcome = await sort_message(msg.text, currency=code)
    except SorterError as exc:
        # Critical fallback: capture must NEVER fail because of the LLM.
        logger.warning("sorter failed, saving raw: %s", exc)
        db.add_item(
            msg.text, sender_name, sender_id, msg.chat_id, tg_message_id=msg.message_id
        )
        db.auto_attach_sender(sender_id)
        await msg.reply_text("✓ got it — couldn't sort this yet")
        return

    if not outcome.items:
        logger.info("chatter from %s, nothing saved", sender_name)
        return

    for it in outcome.items:
        db.add_item(
            msg.text, sender_name, sender_id, msg.chat_id,
            lane=it.lane, display_text=it.display_text,
            estimated_price=it.estimated_price, priority=it.priority,
            llm_raw=outcome.raw, tg_message_id=msg.message_id,
        )
    db.auto_attach_sender(sender_id)
    logger.info("saved %d item(s) from %s", len(outcome.items), sender_name)

    text, keyboard = _render_confirmation(msg.chat_id, msg.message_id)
    await msg.reply_text(text, reply_markup=keyboard)


async def handle_flip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    item_id = int(query.data.split(":", 1)[1])
    item = db.get_item(item_id)
    if not item or item["status"] != "active":
        await query.answer("that one's gone")
        return

    old = item["lane"]
    new = "everyday" if old == "dream" else "dream"
    db.set_lane(item_id, new)
    # Every overrule is a learning signal — original guess, correction, who, when.
    db.add_overrule(
        item_id, "lane", old, new, "telegram",
        query.from_user.id if query.from_user else None,
    )
    logger.info("overrule: item=%s %s→%s", item_id, old, new)
    await query.answer(f"now {'a dream' if new == 'dream' else 'everyday'}")

    text, keyboard = _render_confirmation(item["chat_id"], item["tg_message_id"])
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except Exception as exc:  # e.g. content identical — harmless
        logger.debug("edit skipped: %s", exc)


async def _backfill(app: Application) -> None:
    """Best-effort resort of anything still unsorted — covers pre-migration rows
    on first boot and any LLM-outage leftovers. Idempotent; never deletes."""
    unsorted = db.list_unsorted()
    if not unsorted:
        return
    logger.info("backfill: %d unsorted item(s)", len(unsorted))
    code, _ = _currency()
    for item in unsorted:
        try:
            outcome = await sort_message(item["text"], currency=code)
        except SorterError as exc:
            logger.warning("backfill: id=%s failed (%s), left unsorted", item["id"], exc)
            continue
        if not outcome.items:
            # Model judges a stored row as chatter → keep for human placement.
            logger.info("backfill: id=%s judged non-item, left unsorted", item["id"])
            continue
        first = outcome.items[0]
        db.set_sorted(
            item["id"], first.lane, first.display_text,
            first.estimated_price, first.priority, outcome.raw,
        )
        for extra in outcome.items[1:]:
            db.add_item(
                item["text"], item["sender_name"], item["sender_id"], item["chat_id"],
                lane=extra.lane, display_text=extra.display_text,
                estimated_price=extra.estimated_price, priority=extra.priority,
                llm_raw=outcome.raw, tg_message_id=item["tg_message_id"],
            )
        logger.info("backfill: id=%s sorted as %s", item["id"], first.lane)
    logger.info("backfill complete")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN not set. Copy .env.example to .env and paste your BotFather token."
        )
    db.init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(_backfill).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_flip, pattern=r"^flip:\d+$"))

    logger.info("bot starting (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
