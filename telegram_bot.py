"""
Telegram-бот для генерации PDF-отчётов.

Запуск: python telegram_bot.py
Требуется TELEGRAM_BOT_TOKEN в .env
"""
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from utils.ai_processor import process_dialog_with_ai
from utils.design_processor import generate_design_image, process_design_dialog
from utils.pdf_generator import (
    generate_design_report_pdf,
    generate_product_card_pdf,
    generate_report_pdf,
)
from utils.product_processor import generate_product_image, process_product_card

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tg_bot")

CHOOSE_TYPE, TRANSCRIPTION, PRODUCT_NAME, PRODUCT_PRICE = range(4)


def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1. Клиентский отчёт", callback_data="client"),
            InlineKeyboardButton("2. Дизайн-отчёт", callback_data="design"),
        ],
        [InlineKeyboardButton("3. Карточка товара", callback_data="product")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "👋 *AI Report Generator*\n\nВыберите тип отчёта:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )
    return CHOOSE_TYPE


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["report_type"] = query.data

    if query.data == "product":
        await query.edit_message_text("📦 *Карточка товара*\n\nВведите название товара:")
        return PRODUCT_NAME
    await query.edit_message_text(
        "📝 *Отправьте транскрибацию диалога:*\n"
        "— текстом в сообщении\n"
        "— или прикрепите .txt файл",
        parse_mode="Markdown",
    )
    return TRANSCRIPTION


async def product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["product_name"] = update.message.text.strip()
    await update.message.reply_text("💰 Введите стоимость товара (например: 599 ₽):")
    return PRODUCT_PRICE


async def product_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["product_price"] = update.message.text.strip()
    name = context.user_data.get("product_name", "")
    price = context.user_data.get("product_price", "")
    transcription = f"{name} | {price}"
    context.user_data["transcription"] = transcription
    await update.message.reply_text("⏳ Генерирую карточку товара...")
    return await run_product_report(update, context)


async def transcription_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    if text and text.strip():
        context.user_data["transcription"] = text.strip()
        await update.message.reply_text("⏳ Обрабатываю...")
        return await run_report(update, context)
    await update.message.reply_text("Текст пуст. Отправьте транскрибацию:")
    return TRANSCRIPTION


async def document_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("Отправьте файл .txt с транскрибацией.")
        return TRANSCRIPTION

    file = await context.bot.get_file(doc.file_id)
    tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        await file.download_to_drive(tmp_path)
        try:
            transcription = Path(tmp_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            transcription = Path(tmp_path).read_text(encoding="cp1251")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not transcription.strip():
        await update.message.reply_text("Файл пуст. Отправьте другой файл или текст.")
        return TRANSCRIPTION

    context.user_data["transcription"] = transcription.strip()
    await update.message.reply_text("⏳ Обрабатываю...")
    return await run_report(update, context)


async def run_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    report_type = context.user_data.get("report_type", "client")
    transcription = context.user_data.get("transcription", "")

    try:
        if report_type == "design":
            data = process_design_dialog(transcription)
            prompt = data.get("image_prompt", "")
            image_path = None
            if prompt:
                try:
                    image_path = generate_design_image(prompt)
                except Exception as e:
                    logger.warning("Ошибка генерации изображения: %s", e)
            pdf_path = generate_design_report_pdf(data, image_path)
        else:
            data = process_dialog_with_ai(transcription)
            pdf_path = generate_report_pdf(data)

        with open(pdf_path, "rb") as f:
            await update.message.reply_document(document=f, filename=Path(pdf_path).name)
        await update.message.reply_text("✅ Готово!", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.exception("Ошибка генерации отчёта: %s", e)
        await update.message.reply_text(
            f"❌ Ошибка: {str(e)}\n\nПопробуйте снова:",
            reply_markup=get_main_keyboard(),
        )
    return CHOOSE_TYPE


async def run_product_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    transcription = context.user_data.get("transcription", "")

    try:
        data = process_product_card(transcription)
        prompt = data.get("image_prompt", "")
        if not prompt:
            raise ValueError("Не получен промпт для изображения")
        image_path = generate_product_image(prompt)
        pdf_path = generate_product_card_pdf(data, image_path)

        with open(pdf_path, "rb") as f:
            await update.message.reply_document(document=f, filename=Path(pdf_path).name)
        await update.message.reply_text("✅ Карточка создана!", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.exception("Ошибка генерации карточки: %s", e)
        await update.message.reply_text(
            f"❌ Ошибка: {str(e)}\n\nПопробуйте снова:",
            reply_markup=get_main_keyboard(),
        )
    return CHOOSE_TYPE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=get_main_keyboard())
    return CHOOSE_TYPE


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Добавьте TELEGRAM_BOT_TOKEN в .env")
        return

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_TYPE: [
                CallbackQueryHandler(choose_type, pattern="^(client|design|product)$"),
            ],
            TRANSCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, transcription_input),
                MessageHandler(filters.Document.ALL, document_input),
            ],
            PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_name)],
            PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)

    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
