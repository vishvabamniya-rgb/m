import os
import re
import json
import tempfile
from datetime import datetime

import requests
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================
# CONFIG
# =========================================
BOT_TOKEN = "8634130308:AAGRbg2475S8YvmfZfY5QH2cw6wklfkpMdo"

# admin/local/config hide karne ho to True
HIDE_SYSTEM_DBS = False
SYSTEM_DBS = {"admin", "local", "config"}


# =========================================
# HELPERS
# =========================================
def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\\\|?*]+', "_", name).strip() or "output"


def convert_for_json(doc):
    return json.loads(json.dumps(doc, default=str, ensure_ascii=False))


def get_client(uri: str) -> MongoClient:
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    return client


def list_databases(uri: str):
    client = None
    try:
        client = get_client(uri)
        dbs = client.list_database_names()
        if HIDE_SYSTEM_DBS:
            dbs = [x for x in dbs if x not in SYSTEM_DBS]
        return sorted(dbs, key=str.lower)
    finally:
        if client:
            client.close()


def export_database_to_txt(uri: str, db_name: str):
    client = None
    try:
        client = get_client(uri)
        db = client[db_name]
        collections = db.list_collection_names()

        if not collections:
            return None, 0, 0

        fd, path = tempfile.mkstemp(prefix=f"{safe_filename(db_name)}_", suffix=".txt")
        os.close(fd)

        total_docs = 0
        total_collections = len(collections)

        with open(path, "w", encoding="utf-8") as f:
            f.write("MongoDB Export\n")
            f.write(f"Database: {db_name}\n")
            f.write(f"Export Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")

            for coll_name in collections:
                collection = db[coll_name]

                f.write("=" * 80 + "\n")
                f.write(f"COLLECTION: {coll_name}\n")
                f.write("=" * 80 + "\n\n")

                try:
                    doc_count = collection.count_documents({})
                    cursor = collection.find({})
                except Exception as e:
                    f.write(f"❌ Collection read error: {e}\n\n")
                    continue

                f.write(f"Total Documents: {doc_count}\n\n")

                if doc_count == 0:
                    f.write("(Empty Collection)\n\n")
                    continue

                for index, doc in enumerate(cursor, start=1):
                    total_docs += 1
                    clean_doc = convert_for_json(doc)

                    f.write(f"--- Document {index} ---\n")
                    f.write(json.dumps(clean_doc, indent=2, ensure_ascii=False))
                    f.write("\n\n")

        return path, total_collections, total_docs

    finally:
        if client:
            client.close()


def build_db_keyboard(db_names):
    rows = []
    row = []

    for db in db_names:
        row.append(InlineKeyboardButton(db[:60], callback_data=f"db|{db}"))
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("All", callback_data="all")])
    return InlineKeyboardMarkup(rows)


def check_bot_token_info(token: str):
    url = f"https://api.telegram.org/bot{token}/getMe"
    r = requests.get(url, timeout=15)
    data = r.json()

    if not data.get("ok"):
        return False, data.get("description", "Invalid token"), None

    result = data.get("result", {})
    return True, "OK", result


# =========================================
# COMMANDS
# =========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_uri"] = True
    context.user_data["awaiting_token"] = False
    await update.message.reply_text(
        "Mongo URI bhejo.\n\n"
        "Example:\n"
        "`mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority`\n\n"
        "Bot token check karna ho to `/token` use karo.",
        parse_mode="Markdown",
    )


async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_token"] = True
    context.user_data["awaiting_uri"] = False
    await update.message.reply_text(
        "Bot token bhejo.\n\nExample:\n`123456789:AA...`",
        parse_mode="Markdown",
    )


# =========================================
# TEXT HANDLER
# =========================================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # TOKEN MODE
    if context.user_data.get("awaiting_token"):
        msg = await update.message.reply_text("Checking token...")

        try:
            ok, status_msg, info = check_bot_token_info(text)

            context.user_data["awaiting_token"] = False

            if not ok:
                await msg.edit_text(
                    f"❌ Token invalid ya error.\n\n`{status_msg}`",
                    parse_mode="Markdown",
                )
                return

            full_name = info.get("first_name", "N/A")
            username = info.get("username", "N/A")
            bot_id = info.get("id", "N/A")
            can_join_groups = info.get("can_join_groups", False)
            can_read_all_group_messages = info.get("can_read_all_group_messages", False)
            supports_inline_queries = info.get("supports_inline_queries", False)
            is_bot = info.get("is_bot", False)

            text_out = (
                "✅ Token sahi hai\n\n"
                f"**Name:** {full_name}\n"
                f"**Username:** @{username if username != 'N/A' else 'N/A'}\n"
                f"**ID:** `{bot_id}`\n"
                f"**Is Bot:** `{is_bot}`\n"
                f"**Can Join Groups:** `{can_join_groups}`\n"
                f"**Can Read All Group Messages:** `{can_read_all_group_messages}`\n"
                f"**Supports Inline Queries:** `{supports_inline_queries}`"
            )
            await msg.edit_text(text_out, parse_mode="Markdown")

        except Exception as e:
            context.user_data["awaiting_token"] = False
            await msg.edit_text(
                f"❌ Token check error:\n`{str(e)[:3500]}`",
                parse_mode="Markdown",
            )
        return

    # MONGO MODE
    if context.user_data.get("awaiting_uri"):
        msg = await update.message.reply_text("Connecting MongoDB...")

        try:
            db_names = list_databases(text)

            if not db_names:
                await msg.edit_text("Koi database nahi mila.")
                return

            context.user_data["mongo_uri"] = text
            context.user_data["awaiting_uri"] = False
            context.user_data["db_names"] = db_names

            await msg.edit_text(
                "Database select karo:",
                reply_markup=build_db_keyboard(db_names)
            )

        except PyMongoError as e:
            await msg.edit_text(f"MongoDB error:\n`{str(e)[:3500]}`", parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"Error:\n`{str(e)[:3500]}`", parse_mode="Markdown")
        return

    await update.message.reply_text(
        "Use:\n"
        "/start → Mongo URI bhejne ke liye\n"
        "/token → Bot token check karne ke liye"
    )


# =========================================
# CALLBACKS
# =========================================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mongo_uri = context.user_data.get("mongo_uri")
    db_names = context.user_data.get("db_names", [])

    if not mongo_uri:
        await query.message.reply_text(
            "Session expire ho gaya. `/start` fir se karo.",
            parse_mode="Markdown"
        )
        return

    data = query.data

    if data == "all":
        await query.message.reply_text("Sab databases export ho rahe hain...")

        sent = 0
        skipped = 0

        for db_name in db_names:
            try:
                path, coll_count, docs_count = export_database_to_txt(mongo_uri, db_name)

                if not path:
                    skipped += 1
                    await query.message.reply_text(
                        f"⚠️ `{db_name}` empty hai ya export nahi hua.",
                        parse_mode="Markdown"
                    )
                    continue

                caption = (
                    f"✅ Database: `{db_name}`\n"
                    f"📚 Collections: `{coll_count}`\n"
                    f"📄 Documents: `{docs_count}`"
                )

                with open(path, "rb") as f:
                    await query.message.reply_document(
                        document=f,
                        filename=f"{safe_filename(db_name)}.txt",
                        caption=caption,
                        parse_mode="Markdown",
                    )

                sent += 1

                try:
                    os.remove(path)
                except Exception:
                    pass

            except Exception as e:
                skipped += 1
                await query.message.reply_text(
                    f"❌ `{db_name}` export error:\n`{str(e)[:3000]}`",
                    parse_mode="Markdown"
                )

        await query.message.reply_text(
            f"Done.\n✅ Exported: {sent}\n⚠️ Skipped: {skipped}"
        )
        return

    if data.startswith("db|"):
        db_name = data.split("|", 1)[1]

        await query.message.reply_text(f"Exporting `{db_name}` ...", parse_mode="Markdown")

        try:
            path, coll_count, docs_count = export_database_to_txt(mongo_uri, db_name)

            if not path:
                await query.message.reply_text(
                    f"⚠️ `{db_name}` empty hai ya export nahi hua.",
                    parse_mode="Markdown"
                )
                return

            caption = (
                f"✅ Database: `{db_name}`\n"
                f"📚 Collections: `{coll_count}`\n"
                f"📄 Documents: `{docs_count}`"
            )

            with open(path, "rb") as f:
                await query.message.reply_document(
                    document=f,
                    filename=f"{safe_filename(db_name)}.txt",
                    caption=caption,
                    parse_mode="Markdown",
                )

            try:
                os.remove(path)
            except Exception:
                pass

        except Exception as e:
            await query.message.reply_text(
                f"❌ Export error:\n`{str(e)[:3500]}`",
                parse_mode="Markdown"
            )


# =========================================
# MAIN
# =========================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("token", token_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()