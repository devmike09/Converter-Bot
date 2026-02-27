import os
import re
import uuid
import zipfile
import logging
import requests
import subprocess
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import BadRequest

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment Variables
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "@C0nver1_bot") # Must include @

# Dictionary to temporarily store user files for the "download chats" feature
user_sessions = {}

# Ensure temp directory exists
TEMP_DIR = "temp_downloads"
os.makedirs(TEMP_DIR, exist_ok=True)


async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the user is a member of the required channel."""
    if not REQUIRED_CHANNEL or REQUIRED_CHANNEL == "NONE":
        return True # Skip check if no channel is configured
        
    try:
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        # Valid statuses: 'member', 'administrator', 'creator', 'restricted' (if still in chat)
        if member.status in ['left', 'kicked']:
            return False
        return True
    except BadRequest as e:
        logger.error(f"Failed to check membership. Is bot an admin in {REQUIRED_CHANNEL}? Error: {e}")
        # If the bot is not an admin, it throws an error. We return False to prevent unauthorized access,
        # but you should check your logs if users complain they can't get in.
        return False
    except Exception as e:
        logger.error(f"Unexpected error checking subscription: {e}")
        return False


async def send_join_prompt(message) -> None:
    """Send a message prompting the user to join the channel."""
    channel_url = f"https://t.me/{REQUIRED_CHANNEL.replace('@', '')}"
    keyboard = [
        [InlineKeyboardButton("📢 Join Our Channel", url=channel_url)],
        [InlineKeyboardButton("✅ I have joined (Refresh)", callback_data="check_sub")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_text(
        "🛑 *Access Denied!*\n\nYou must join our official channel to use this bot.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        await send_join_prompt(update.message)
        return

    welcome_text = (
        "👋 Welcome to the Ultimate Converter & Downloader Bot!\n\n"
        "Here is what I can do:\n"
        "🔗 *Send a Link:* I will download the file and send it to you.\n"
        "🖼️ *Send an Image:* I will ask if you want it converted (PNG, JPG, WEBP, PDF).\n"
        "🎥 *Send a Video:* I can extract the audio to MP3.\n"
        "📁 *Send multiple files/pics:* I will save them in your session. Send /zip when you are done to get them all in one archive.\n"
        "🧹 *Send /clear:* To clear your current saved files session."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download a file from a direct link and send it back."""
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        await send_join_prompt(update.message)
        return

    url = update.message.text
    if not re.match(r'^https?://', url):
        return # Not a link, ignore

    message = await update.message.reply_text("⏳ Downloading file from link...")
    
    try:
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path) or f"download_{uuid.uuid4().hex[:8]}.file"
        filepath = os.path.join(TEMP_DIR, filename)

        response = requests.get(url, stream=True, timeout=10)
        response.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        file_size = os.path.getsize(filepath)
        if file_size > 49 * 1024 * 1024:
            await message.edit_text("❌ File is larger than 50MB. Telegram restricts bots from uploading files larger than 50MB.")
            os.remove(filepath)
            return

        await message.edit_text("📤 Uploading to Telegram...")
        with open(filepath, 'rb') as f:
            await update.message.reply_document(document=f)
            
        await message.delete()

    except Exception as e:
        logger.error(f"Error downloading link: {e}")
        await message.edit_text(f"❌ Failed to download or send the file. Make sure it's a direct download link.\nError: {str(e)[:50]}")
    finally:
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming images, videos, and documents."""
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        await send_join_prompt(update.message)
        return

    message = update.message
    file_obj = None
    file_type = None

    if message.photo:
        file_obj = message.photo[-1]
        file_type = "image"
    elif message.video:
        file_obj = message.video
        file_type = "video"
    elif message.document:
        file_obj = message.document
        file_type = "document"
    else:
        return

    status_msg = await message.reply_text("⏳ Processing file...")
    
    try:
        telegram_file = await context.bot.get_file(file_obj.file_id)
        
        ext = ".file"
        if getattr(file_obj, 'file_name', None):
            ext = os.path.splitext(file_obj.file_name)[1]
        elif file_type == "image":
            ext = ".jpg"
        elif file_type == "video":
            ext = ".mp4"

        filepath = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}{ext}")
        await telegram_file.download_to_drive(filepath)

        if user_id not in user_sessions:
            user_sessions[user_id] = []
        user_sessions[user_id].append(filepath)

        if file_type == "image":
            keyboard = [
                [
                    InlineKeyboardButton("Convert to PNG", callback_data=f"conv_png_{filepath}"),
                    InlineKeyboardButton("Convert to WEBP", callback_data=f"conv_webp_{filepath}")
                ],
                [InlineKeyboardButton("Convert to PDF", callback_data=f"conv_pdf_{filepath}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await status_msg.edit_text("File saved to your session! Do you want to convert it?", reply_markup=reply_markup)
            
        elif file_type == "video":
            keyboard = [[InlineKeyboardButton("Extract Audio (MP3)", callback_data=f"conv_mp3_{filepath}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await status_msg.edit_text("Video saved to your session! Do you want to extract audio?", reply_markup=reply_markup)
            
        else:
            await status_msg.edit_text(f"Document saved to your session. Total files: {len(user_sessions[user_id])}. Send /zip to pack them.")

    except Exception as e:
        logger.error(f"Error handling media: {e}")
        await status_msg.edit_text("❌ Failed to process the file.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all inline button clicks."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    await query.answer()
    data = query.data

    # Check for the "Refresh / I joined" button
    if data == "check_sub":
        if await check_subscription(user_id, context):
            await query.edit_message_text("✅ Thank you for joining! You can now use the bot. Send /start to see the menu.")
        else:
            # Send an alert popup that they still haven't joined
            await context.bot.answer_callback_query(callback_query_id=query.id, text="❌ You haven't joined the channel yet!", show_alert=True)
        return

    # If it's a conversion button, check subscription first
    if not await check_subscription(user_id, context):
        await query.edit_message_text("❌ You must join the channel to convert files.")
        return

    if data.startswith("conv_"):
        parts = data.split("_", 2)
        action = parts[1]
        filepath = parts[2]

        if not os.path.exists(filepath):
            await query.edit_message_text("❌ File expired or no longer exists on the server.")
            return

        await query.edit_message_text(f"⏳ Converting to {action.upper()}...")

        try:
            output_ext = f".{action}"
            output_path = f"{os.path.splitext(filepath)[0]}_converted{output_ext}"

            if action in ["png", "webp", "jpg", "pdf"]:
                subprocess.run(["ffmpeg", "-y", "-i", filepath, output_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif action == "mp3":
                subprocess.run(["ffmpeg", "-y", "-i", filepath, "-q:a", "0", "-map", "a", output_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if os.path.exists(output_path):
                await query.edit_message_text("📤 Uploading converted file...")
                with open(output_path, 'rb') as f:
                    await context.bot.send_document(chat_id=query.message.chat_id, document=f)
                os.remove(output_path)
                await query.message.delete()
            else:
                await query.edit_message_text("❌ Conversion failed.")

        except Exception as e:
            logger.error(f"Conversion error: {e}")
            await query.edit_message_text("❌ An error occurred during conversion.")


async def zip_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Zip all files in the user's session and send them back."""
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        await send_join_prompt(update.message)
        return
    
    if user_id not in user_sessions or not user_sessions[user_id]:
        await update.message.reply_text("⚠️ You have no files saved in your session. Send me some pics/files first!")
        return

    message = await update.message.reply_text("⏳ Zipping your files...")
    zip_filename = os.path.join(TEMP_DIR, f"Archive_{user_id}_{uuid.uuid4().hex[:6]}.zip")
    
    try:
        with zipfile.ZipFile(zip_filename, 'w') as zipf:
            for file in user_sessions[user_id]:
                if os.path.exists(file):
                    zipf.write(file, os.path.basename(file))
                    os.remove(file) 

        user_sessions[user_id] = []

        if os.path.getsize(zip_filename) > 49 * 1024 * 1024:
            await message.edit_text("❌ The resulting ZIP is over 50MB. Telegram bots cannot upload files this large.")
            return

        await message.edit_text("📤 Uploading ZIP...")
        with open(zip_filename, 'rb') as f:
            await update.message.reply_document(document=f)
            
        await message.delete()

    except Exception as e:
        logger.error(f"Zip error: {e}")
        await message.edit_text("❌ Error creating ZIP file.")
    finally:
        if os.path.exists(zip_filename):
            os.remove(zip_filename)


async def clear_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the user's current session files."""
    user_id = update.effective_user.id
    if not await check_subscription(user_id, context):
        await send_join_prompt(update.message)
        return

    if user_id in user_sessions:
        for file in user_sessions[user_id]:
            if os.path.exists(file):
                os.remove(file)
        user_sessions[user_id] = []
        await update.message.reply_text("🧹 Session cleared. All temporary files deleted.")
    else:
        await update.message.reply_text("⚠️ Your session is already empty.")


def main() -> None:
    """Start the bot."""
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("zip", zip_files))
    application.add_handler(CommandHandler("clear", clear_session))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media))
    application.add_handler(CallbackQueryHandler(button_callback))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
