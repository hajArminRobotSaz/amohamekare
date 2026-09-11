import subprocess
import shutil
#!/usr/bin/env python3
"""ToolBot — Telegram utility bot.

Features:
  • Video → MP3 with ffmpeg
  • Minecraft mod search/download via Modrinth
  • File conversion
  • Unicode-safe square QR code generator
  • V2Ray subscription/config manager backed by GitHub
"""

import asyncio
import base64
import io
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

FFMPEG_BIN = "ffmpeg"
WORK_DIR = Path(tempfile.mkdtemp(prefix="toolbot_"))

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Armiin8472/yt-bot-uploads")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"

TELEGRAM_MOD_LIMIT = 50 * 1024 * 1024
GITHUB_CONTENTS_LIMIT = 100 * 1024 * 1024
TELEGRAM_CONVERT_LIMIT = 20 * 1024 * 1024

logger = logging.getLogger("toolbot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Menu helpers
# ---------------------------------------------------------------------------

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    # چیدمان اصلی: ماد / تبدیل / بارکد / استیم / اینستا / ویتوری
    return ReplyKeyboardMarkup([
        [KeyboardButton("🎮 دانلود ماد ماینکرفت"), KeyboardButton("🔄 تبدیل فایل")],
        [KeyboardButton("📊 ساخت بارکد"), KeyboardButton("💰 قیمت بازی استیم")],
        [KeyboardButton("📥 دانلود از اینستاگرام"), KeyboardButton("🔗 ساب لینک V2Ray")],
        [KeyboardButton("🔊 متن به صدا")],
    ], resize_keyboard=True, is_persistent=True)


def v2ray_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ اضافه کردن کانفینگ جدید")],
        [KeyboardButton("📋 ساب لینک‌های من")],
        [KeyboardButton("🗑 حذف همه کانفینگ‌ها")],
        [KeyboardButton("◀️ بازگشت به منوی اصلی")],
    ], resize_keyboard=True, is_persistent=True)


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton("◀️ بازگشت به منوی اصلی")]], resize_keyboard=True, is_persistent=True)


def reply_keyboard(buttons: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton(text) for text in row] for row in buttons], resize_keyboard=True, is_persistent=True)


def back_button(callback_data: str = "menu_main", text: str = "◀️ بازگشت به منوی اصلی") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=callback_data)]])


WELCOME_TEXT = "👋 به ربات ابزار خوش آمدید!\n\nاز منوی زیر ابزار مورد نظر خود را انتخاب کنید:"
HELP_TEXT = (
    "❓ راهنمای ربات\n\n"
    "🔄 تبدیل فایل: فایل بفرستید (PDF, DOCX, TXT, PNG, JPG, MP4, MP3) یا ویدیو برای استخراج صدا یا متن برای تبدیل به فایل.\n"
    "🖼️ تبدیل عکس به PDF: عکس‌ها را به ترتیب بفرستید و در پایان «پایان» را بزنید تا همان ترتیب داخل PDF حفظ شود.\n"
    "🔊 متن به صدا: متن را بدهید و از بین صداهای سرویس text-to-speech.online انتخاب کنید.\n"
    "🎮 دانلود ماد ماینکرفت: نام ماد را جستجو کنید و نسخه واقعی موجود را انتخاب کنید.\n"
    "📊 ساخت بارکد: متن یا عدد را به QR کد مربعی تبدیل کنید.\n"
    "💰 قیمت بازی استیم: نام بازی را جستجو کنید و قیمت ریجن‌های مختلف را ببینید.\n"
    "📥 دانلود از اینستاگرام: لینک پست، ریلز یا استوری را ارسال کنید.\n"
    "🔗 ساب لینک V2Ray: کانفینگ‌های خود را مدیریت کنید.\n\n"
    "برای لغو عملیات جاری /cancel را بزنید."
)

# ---------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, reply_markup=main_menu_keyboard())


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text("✅ عملیات لغو شد.", reply_markup=main_menu_keyboard())

# ---------------------------------------------------------------------------
# Main/V2Ray menus
# ---------------------------------------------------------------------------

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    # Kept for compatibility with old inline-keyboard messages.
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("ℹ️ این منو قدیمی است؛ از دکمه‌های پایین صفحه استفاده کنید.", reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# Feature 1 — Video to Audio
# ---------------------------------------------------------------------------

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    file_obj = msg.video or (msg.document if msg.document and (msg.document.mime_type or "").startswith("video/") else None)
    if file_obj is None:
        return

    if file_obj.file_size and file_obj.file_size > 50 * 1024 * 1024:
        await msg.reply_text("❌ حجم فایل بیش از ۵۰ مگابایت است.", reply_markup=main_menu_keyboard())
        return

    status_msg = await msg.reply_text("⏳ در حال دانلود ویدیو...")
    input_path = None
    output_path = None
    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
        ext = Path(getattr(file_obj, "file_name", None) or "video.mp4").suffix or ".mp4"
        input_path = WORK_DIR / f"{file_obj.file_id}{ext}"
        output_path = WORK_DIR / f"{file_obj.file_id}.mp3"
        await tg_file.download_to_drive(str(input_path))
        await status_msg.edit_text("⏳ در حال استخراج صدا (ffmpeg)...")

        proc = await asyncio.create_subprocess_exec(
            FFMPEG_BIN, "-y", "-i", str(input_path), "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(output_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            await status_msg.edit_text(f"❌ خطا در تبدیل فایل:\n`{stderr.decode(errors='replace')[:500]}`", parse_mode="Markdown")
            return

        await status_msg.edit_text("✅ تبدیل انجام شد! در حال ارسال فایل...")
        with open(output_path, "rb") as f:
            await msg.reply_audio(audio=f, filename=output_path.name, caption="🎵 فایل صوتی استخراج شده", reply_markup=main_menu_keyboard())
        await status_msg.delete()
    except Exception as exc:
        logger.exception("Video2Audio error")
        await status_msg.edit_text(f"❌ خطای غیرمنتظره:\n`{str(exc)[:500]}`", parse_mode="Markdown")
    finally:
        for p in (input_path, output_path):
            if p:
                try:
                    p.unlink()
                except OSError:
                    pass

# ---------------------------------------------------------------------------
# Feature 2 — Minecraft Mod Downloader
# ---------------------------------------------------------------------------

MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_HEADERS = {"User-Agent": "ToolBot/2.0 (Telegram Bot)"}


def _version_key(version: str) -> tuple:
    nums = tuple(int(x) for x in re.findall(r"\d+", version))
    return nums + (-1,) * (4 - len(nums))


def _mc_family(version: str) -> str | None:
    match = re.match(r"^(\d+\.\d+)(?:\.|$)", version)
    return match.group(1) if match else None


async def _search_mods(query: str, limit: int = 6) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(headers=MODRINTH_HEADERS, timeout=20) as client:
        resp = await client.get(
            f"{MODRINTH_API}/search",
            params={"query": query, "limit": limit, "facets": '[["project_type:mod"]]'},
        )
        resp.raise_for_status()
        return resp.json().get("hits", [])


async def _get_all_versions(project_id: str) -> list[dict[str, Any]]:
    """Fetch all actual releases for a Modrinth project (all loaders/MC versions)."""
    async with httpx.AsyncClient(headers=MODRINTH_HEADERS, timeout=25) as client:
        resp = await client.get(
            f"{MODRINTH_API}/project/{project_id}/version",
            params={"include_changelog": "false"},
        )
        resp.raise_for_status()
        return resp.json()


async def _download_mod_file(url: str, filename: str) -> Path:
    path = WORK_DIR / filename
    async with httpx.AsyncClient(headers=MODRINTH_HEADERS, timeout=120, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(path, "wb") as f:
                async for chunk in resp.aiter_bytes(1024 * 1024):
                    f.write(chunk)
    return path


async def handle_mod_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    menu_texts = {
        "🎮 دانلود ماد ماینکرفت", "🎮 ماد", "🔄 تبدیل فایل", "🔄 تبدیل",
        "📊 ساخت بارکد", "📊 بارکد", "💰 قیمت بازی استیم", "💰 استیم",
        "📥 دانلود از اینستاگرام", "📥 اینستا", "🔊 متن به صدا",
        "🔗 ساب لینک V2Ray", "🔗 ویتوری", "➕ اضافه کردن کانفینگ جدید", "📋 ساب لینک‌های من",
        "🗑 حذف همه کانفینگ‌ها", "◀️ بازگشت به منوی اصلی",
    }
    if text in menu_texts:
        await handle_menu_text(update, context)
        return
    state = context.user_data.get("state")
    if text == "✅ پایان" and state == "image_to_pdf":
        await _finish_images_to_pdf(update, context)
        return
    if state == "mod_query":
        await _do_mod_search(update, context)
    elif state == "mod_pick":
        await _do_mod_pick_text(update, context)
    elif state == "mod_family":
        await _do_mod_family_text(update, context)
    elif state == "mod_exact":
        await _do_mod_exact_text(update, context)
    elif state == "barcode_text":
        await _do_barcode(update, context)
    elif state == "convert_choice":
        await _do_convert_choice(update, context)
    elif state == "convert_or_text":
        await _do_convert_or_text(update, context)
    elif state == "text_convert_choice":
        await _do_text_convert_choice(update, context)
    elif state == "text_to_txt":
        await _do_text_to_txt(update, context)
    elif state == "v2ray_add":
        await handle_v2ray_config(update, context)
    elif state == "v2ray_delete_confirm":
        await _handle_v2ray_delete_confirm(update, context)
    elif state == "steam_search":
        await _do_steam_search(update, context)
    elif state == "steam_region":
        await _do_steam_region(update, context)
    elif state == "insta_url":
        await _do_insta_download(update, context)
    elif state == "tts_text":
        await _do_tts_text(update, context)
    elif state == "tts_voice":
        await _do_tts_voice(update, context)
    else:
        await handle_menu_text(update, context)


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == "🎬 ویدیو به صدا":
        await update.message.reply_text("🎬 یک فایل ویدیویی برای من بفرستید تا صدای آن را استخراج کنم.\nفرمت خروجی: MP3\n\nبرای لغو: /cancel", reply_markup=back_keyboard())
        return
    if text in ("🎮 دانلود ماد ماینکرفت", "🎮 ماد"):
        context.user_data["state"] = "mod_query"
        await update.message.reply_text("🎮 نام ماد ماینکرفت را ارسال کنید.\nنسخه‌ها فقط از Modrinth و به‌صورت واقعی تشخیص داده می‌شوند.\n\nبرای لغو: /cancel", reply_markup=back_keyboard())
        return
    if text in ("🔄 تبدیل فایل", "🔄 تبدیل"):
        context.user_data["state"] = "convert_or_text"
        await update.message.reply_text(
            "🔄 تبدیل فایل\n\n"
            "فایل بفرستید (PDF, DOCX, TXT, PNG, JPG, MP4, MP3)\n"
            "🔸 ویدیو بفرستید تا صدا استخراج شود.\n"
            "🔸 یا متنی تایپ کنید تا به فایل متنی تبدیل شود.\n\n"
            "🖼️ تبدیل عکس به PDF: عکس‌ها را به ترتیب بفرستید و در پایان «پایان» را بزنید؛ عکس‌ها دقیقاً با همان ترتیب داخل PDF قرار می‌گیرند.",
            reply_markup=back_keyboard(),
        )
        return
    if text in ("📊 ساخت بارکد", "📊 بارکد"):
        context.user_data["state"] = "barcode_text"
        await update.message.reply_text("📊 متن، عدد یا متن فارسی را بفرستید تا QR کد ساخته شود.\n\nبرای لغو: /cancel", reply_markup=back_keyboard())
        return

    if text == "🔊 متن به صدا":
        context.user_data["state"] = "tts_text"
        await update.message.reply_text(
            "🔊 متن به صدا\n\n"
            "متنت را بفرست تا صدا بسازیم.\n"
            "بعد از دریافت متن، لیست صداها را برای انتخاب نمایش می‌دهم.\n\n"
            "برای لغو: /cancel",
            reply_markup=back_keyboard(),
        )
        return
    if text in ("🔗 ساب لینک V2Ray", "🔗 ویتوری"):
        context.user_data["state"] = None
        await update.message.reply_text("🔗 ساب لینک V2Ray\n\nیکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=v2ray_menu_keyboard())
        return
    if text in ("💰 قیمت بازی استیم", "💰 استیم"):
        context.user_data["state"] = "steam_search"
        await update.message.reply_text("💰 نام بازی مورد نظر را تایپ کنید:\n\nبرای لغو: /cancel", reply_markup=back_keyboard())
        return
    if text in ("📥 دانلود از اینستاگرام", "📥 اینستا"):
        context.user_data["state"] = "insta_url"
        await update.message.reply_text("📥 لینک پست، ریلز یا استوری اینستاگرام را ارسال کنید:\n\nبرای لغو: /cancel", reply_markup=back_keyboard())
        return
    if text == "➕ اضافه کردن کانفینگ جدید":
        try:
            existing_configs = await _load_v2ray_configs(update.effective_user.id)
        except Exception as exc:
            logger.exception("V2Ray existing configs lookup error")
            await update.message.reply_text(f"❌ خطا در خواندن کانفینگ‌های قبلی از GitHub:\n`{str(exc)[:250]}`", reply_markup=v2ray_menu_keyboard(), parse_mode="Markdown")
            return
        context.user_data["state"] = "v2ray_add"
        context.user_data["v2ray_configs"] = existing_configs
        await update.message.reply_text("➕ کانفینگ V2Ray خود را ارسال کنید.\nبرای پایان: /done", reply_markup=back_keyboard())
        return
    if text == "📋 ساب لینک‌های من":
        uid = update.effective_user.id
        link = f"{GITHUB_RAW_BASE}/configs/{uid}.txt"
        await update.message.reply_text(f"📋 ساب لینک شما:\n`{link}`", reply_markup=v2ray_menu_keyboard(), parse_mode="Markdown")
        return
    if text == "🗑 حذف همه کانفینگ‌ها":
        context.user_data["state"] = "v2ray_delete_confirm"
        await update.message.reply_text("⚠️ مطمئنی می‌خواهی همه کانفینگ‌های خودت را حذف کنی؟\n\nاین کار قابل برگشت نیست.", reply_markup=reply_keyboard([["✅ بله، حذف کن", "❌ لغو"]]))
        return
    if text == "◀️ بازگشت به منوی اصلی":
        context.user_data.clear()
        await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
        return


async def _do_mod_pick_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == "◀️ بازگشت به منوی اصلی":
        await handle_menu_text(update, context); return
    labels = context.user_data.get("mod_pick_labels", {})
    index = next((i for i, label in labels.items() if label == text), None)
    if index is None:
        await update.message.reply_text("❌ یکی از مادهای نمایش‌داده‌شده را انتخاب کنید.")
        return
    context.user_data["state"] = None
    await _mod_pick_project(update.message, context, index)


async def _mod_pick_project(message, context, index: str) -> None:
    project_id = context.user_data.get("mod_search_results", {}).get(index)
    mod_name = context.user_data.get("mod_search_names", {}).get(index, "ماد")
    if not project_id:
        await message.reply_text("❌ نتیجه جستجو منقضی شده است. دوباره ماد را جستجو کنید.", reply_markup=main_menu_keyboard())
        return
    await message.reply_text("⏳ در حال بررسی نسخه‌های واقعی ماد...")
    try:
        versions = await _get_all_versions(project_id)
    except Exception as exc:
        await message.reply_text(f"❌ خطا در دریافت نسخه‌ها:\n`{str(exc)[:400]}`", parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return
    options = {}
    for release in versions:
        if not release.get("files"):
            continue
        loaders = release.get("loaders") or ["unknown"]
        for game_version in release.get("game_versions") or []:
            if not _mc_family(game_version):
                continue
            for loader in loaders:
                key = (game_version, loader.lower())
                if key not in options:
                    options[key] = release
    families = sorted({_mc_family(gv) for gv, _ in options if _mc_family(gv)}, key=_version_key, reverse=True)
    if not families:
        await message.reply_text("❌ هیچ نسخه سازگاری برای این ماد پیدا نشد.", reply_markup=main_menu_keyboard())
        return
    context.user_data["mod_project_id"] = project_id
    context.user_data["mod_name"] = mod_name
    context.user_data["mod_versions"] = options
    context.user_data["mod_families"] = families
    buttons = [[f"📦 {family}"] for family in families] + [["◀️ بازگشت به منوی اصلی"]]
    context.user_data["state"] = "mod_family"
    context.user_data["mod_family_labels"] = {str(i): families[i] for i in range(len(families))}
    await message.reply_text("🎮 نسخه اصلی Minecraft را انتخاب کنید:", reply_markup=reply_keyboard(buttons))


async def _do_mod_family_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == "◀️ بازگشت به منوی اصلی":
        await handle_menu_text(update, context); return
    labels = context.user_data.get("mod_family_labels", {})
    family = next((v for v in labels.values() if f"📦 {v}" == text), None)
    if not family:
        await update.message.reply_text("❌ یکی از نسخه‌های اصلی نمایش‌داده‌شده را انتخاب کنید.")
        return
    exact = [(gv, loader, release) for (gv, loader), release in context.user_data.get("mod_versions", {}).items() if gv == family or gv.startswith(family + ".")]
    exact.sort(key=lambda x: (_version_key(x[0]), x[1]), reverse=True)
    context.user_data["mod_exact_options"] = exact
    context.user_data["mod_family"] = family
    await _send_mod_exact_page(update.message, context, 0)


async def _send_mod_exact_page(message, context, page: int) -> None:
    exact = context.user_data.get("mod_exact_options", [])
    family = context.user_data.get("mod_family", "")
    page_size = 24
    total_pages = max(1, (len(exact) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    chunk = exact[start:start + page_size]
    buttons = []
    labels = {}
    for i, (game_version, loader, _release) in enumerate(chunk):
        absolute = start + i
        loader_label = {"neoforge":"NeoForge","forge":"Forge","fabric":"Fabric","quilt":"Quilt","liteloader":"LiteLoader"}.get(loader.lower(), loader.title())
        label = f"{game_version} {loader_label}"
        buttons.append([label]); labels[label] = absolute
    nav = []
    if page > 0: nav.append("⬅️ قبلی")
    if page < total_pages - 1: nav.append("➡️ بعدی")
    if nav: buttons.append(nav)
    buttons.append(["◀️ نسخه‌های اصلی"])
    context.user_data["mod_exact_labels"] = labels
    context.user_data["mod_exact_nav"] = {"⬅️ قبلی": page - 1, "➡️ بعدی": page + 1}
    context.user_data["state"] = "mod_exact"
    await message.reply_text(f"🎮 نسخه‌های دقیق Minecraft برای {family}:\nصفحه {page+1} از {total_pages}", reply_markup=reply_keyboard(buttons))


async def _do_mod_exact_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == "◀️ نسخه‌های اصلی":
        await update.message.reply_text("🎮 نسخه اصلی Minecraft را انتخاب کنید:", reply_markup=reply_keyboard([[f"📦 {f}"] for f in context.user_data.get("mod_families", [])] + [["◀️ بازگشت به منوی اصلی"]]))
        context.user_data["state"] = "mod_family"
        return
    if text in ("⬅️ قبلی", "➡️ بعدی"):
        await _send_mod_exact_page(update.message, context, context.user_data.get("mod_exact_nav", {}).get(text, 0)); return
    index = context.user_data.get("mod_exact_labels", {}).get(text)
    if index is None:
        await update.message.reply_text("❌ یکی از نسخه‌های نمایش‌داده‌شده را انتخاب کنید.")
        return
    gv, loader, release = context.user_data["mod_exact_options"][index]
    await _download_selected_mod_message(update.message, context, gv, loader, release)


async def _do_mod_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query_text = update.message.text.strip()
    if not query_text:
        await update.message.reply_text("❌ نام ماد خالی است.")
        return

    status_msg = await update.message.reply_text("🔍 در حال جستجو...")
    try:
        results = await _search_mods(query_text)
    except Exception as exc:
        logger.exception("Modrinth search error")
        await status_msg.edit_text(f"❌ خطا در جستجو:\n`{str(exc)[:400]}`", parse_mode="Markdown")
        return

    if not results:
        await status_msg.edit_text("❌ مادی با این نام پیدا نشد.")
        await update.message.reply_text("از منوی پایین استفاده کنید یا دوباره نام ماد را بفرستید.", reply_markup=main_menu_keyboard())
        return

    context.user_data["state"] = None
    context.user_data["mod_search_results"] = {str(i): mod.get("project_id", "") for i, mod in enumerate(results)}
    context.user_data["mod_search_names"] = {str(i): mod.get("title", "ماد") for i, mod in enumerate(results)}

    buttons = []
    summaries = []
    for i, mod in enumerate(results):
        name = mod.get("title", "Unknown")
        dl = mod.get("downloads", 0)
        label = f"{i + 1}️⃣ {name}"
        buttons.append([label[:64]])
        summaries.append(f"• **{name}**" + (f" — ⬇️ {dl:,}" if dl else ""))
    buttons.append(["◀️ بازگشت به منوی اصلی"])
    context.user_data["state"] = "mod_pick"
    context.user_data["mod_pick_labels"] = {str(i): buttons[i][0] for i in range(len(results))}
    await status_msg.delete()
    await update.message.reply_text("🔍 نتایج جستجو:\n\n" + "\n".join(summaries), reply_markup=reply_keyboard(buttons), parse_mode="Markdown")


async def mod_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        index = query.data.split(":", 1)[1]
        project_id = context.user_data.get("mod_search_results", {}).get(index)
        mod_name = context.user_data.get("mod_search_names", {}).get(index, "ماد")
    except Exception:
        project_id = None
    if not project_id:
        await query.edit_message_text("❌ نتیجه جستجو منقضی شده است. دوباره ماد را جستجو کنید.", reply_markup=back_button())
        return

    await query.edit_message_text("⏳ در حال بررسی نسخه‌های واقعی ماد...")
    try:
        versions = await _get_all_versions(project_id)
    except Exception as exc:
        logger.exception("Modrinth versions error")
        await query.edit_message_text(f"❌ خطا در دریافت نسخه‌ها:\n`{str(exc)[:400]}`", parse_mode="Markdown")
        return

    # Build exact (game version + loader) entries, keeping the newest release for each pair.
    options: dict[tuple[str, str], dict[str, Any]] = {}
    for release in versions:
        if not release.get("files"):
            continue
        loaders = release.get("loaders") or ["unknown"]
        for game_version in release.get("game_versions") or []:
            if not _mc_family(game_version):
                continue
            for loader in loaders:
                key = (game_version, loader.lower())
                if key not in options:
                    options[key] = release

    families = sorted({_mc_family(game_version) for game_version, _ in options if _mc_family(game_version)}, key=_version_key, reverse=True)
    if not families:
        await query.edit_message_text("❌ هیچ نسخه سازگاری برای این ماد پیدا نشد.", reply_markup=back_button())
        return

    context.user_data["mod_project_id"] = project_id
    context.user_data["mod_name"] = mod_name
    context.user_data["mod_versions"] = options
    context.user_data["mod_families"] = families
    context.user_data["state"] = None

    buttons = [[f"📦 {family}"] for family in families]
    buttons.append(["◀️ بازگشت به منوی اصلی"])
    context.user_data["state"] = "mod_family"
    context.user_data["mod_family_labels"] = {str(i): families[i] for i in range(len(families))}
    await query.edit_message_text(
        "🎮 نسخه ماینکرفت را انتخاب کنید:\n\n"
        "فقط نسخه‌هایی که واقعاً برای همین ماد در Modrinth وجود دارند نمایش داده می‌شوند."
    )
    await query.message.reply_text("نسخه اصلی Minecraft را انتخاب کنید:", reply_markup=reply_keyboard(buttons))


async def mod_minor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        family_index = int(query.data.split(":", 1)[1])
        family = context.user_data["mod_families"][family_index]
        options = context.user_data["mod_versions"]
    except Exception:
        await query.edit_message_text("❌ اطلاعات نسخه منقضی شده است. دوباره ماد را انتخاب کنید.", reply_markup=back_button())
        return

    exact = [(gv, loader, release) for (gv, loader), release in options.items() if gv == family or gv.startswith(family + ".")]
    exact.sort(key=lambda x: (_version_key(x[0]), x[1]), reverse=True)
    context.user_data["mod_exact_options"] = exact
    context.user_data["mod_family"] = family
    await _show_mod_exact_page(query, context, 0)


async def _show_mod_exact_page(query_or_message, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    exact = context.user_data.get("mod_exact_options", [])
    family = context.user_data.get("mod_family", "")
    page_size = 24
    total_pages = max(1, (len(exact) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    context.user_data["mod_exact_page"] = page

    start = page * page_size
    chunk = exact[start:start + page_size]
    buttons = []
    labels = {}
    for i, (game_version, loader, _release) in enumerate(chunk):
        absolute = start + i
        loader_label = {
            "neoforge": "NeoForge",
            "forge": "Forge",
            "fabric": "Fabric",
            "quilt": "Quilt",
            "liteloader": "LiteLoader",
        }.get(loader.lower(), loader.title())
        label = f"{game_version} {loader_label}"
        buttons.append([label])
        labels[label] = absolute

    nav = []
    if page > 0:
        nav.append("⬅️ قبلی")
    if page < total_pages - 1:
        nav.append("➡️ بعدی")
    if nav:
        buttons.append(nav)
    buttons.append(["◀️ نسخه‌های اصلی"])
    context.user_data["mod_exact_labels"] = labels
    context.user_data["mod_exact_nav"] = {"⬅️ قبلی": page - 1, "➡️ بعدی": page + 1}
    context.user_data["state"] = "mod_exact"

    text = f"🎮 نسخه‌های دقیق Minecraft برای **{family}**:\n\n"
    text += "هر گزینه شامل نسخه دقیق + لودر واقعی ماد است.\n"
    text += f"صفحه {page + 1} از {total_pages}"
    inline_buttons = []
    for row in buttons:
        inline_row = []
        for label in row:
            if label in labels:
                inline_row.append(InlineKeyboardButton(label, callback_data=f"modver:{labels[label]}"))
            elif label == "⬅️ قبلی":
                inline_row.append(InlineKeyboardButton(label, callback_data=f"modpage:{page-1}"))
            elif label == "➡️ بعدی":
                inline_row.append(InlineKeyboardButton(label, callback_data=f"modpage:{page+1}"))
            elif label == "◀️ نسخه‌های اصلی":
                inline_row.append(InlineKeyboardButton(label, callback_data="modfamilies"))
        if inline_row:
            inline_buttons.append(inline_row)
    await query_or_message.edit_message_text(text, reply_markup=InlineKeyboardMarkup(inline_buttons), parse_mode="Markdown")


async def mod_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        page = int(query.data.split(":", 1)[1])
        await _show_mod_exact_page(query, context, page)
    except Exception:
        await query.edit_message_text("❌ اطلاعات نسخه منقضی شده است.", reply_markup=back_button())


async def mod_families_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    families = context.user_data.get("mod_families", [])
    if not families:
        await query.edit_message_text("❌ اطلاعات نسخه منقضی شده است.", reply_markup=back_button())
        return
    buttons = [[InlineKeyboardButton(f"📦 {family}", callback_data=f"modminor:{i}")] for i, family in enumerate(families)]
    buttons.append([InlineKeyboardButton("◀️ بازگشت", callback_data="menu_mod")])
    await query.edit_message_text("🎮 نسخه اصلی Minecraft را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(buttons))


async def mod_ver_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.split(":", 1)[1])
        exact = context.user_data["mod_exact_options"][index]
        game_version, loader, release = exact
    except Exception:
        await query.edit_message_text("❌ این نسخه منقضی شده است. دوباره انتخاب کنید.", reply_markup=back_button())
        return
    await _download_selected_mod(query, context, game_version, loader, release)


async def _download_selected_mod(query, context: ContextTypes.DEFAULT_TYPE, game_version: str, loader: str, release: dict[str, Any]) -> None:
    await _download_selected_mod_common(query.message, context, game_version, loader, release, status_editor=query)


async def _download_selected_mod_message(message, context: ContextTypes.DEFAULT_TYPE, game_version: str, loader: str, release: dict[str, Any]) -> None:
    """Download a mod selected from the reply-keyboard flow."""
    await _download_selected_mod_common(message, context, game_version, loader, release, status_editor=None)


async def _download_selected_mod_common(message, context: ContextTypes.DEFAULT_TYPE, game_version: str, loader: str, release: dict[str, Any], status_editor=None) -> None:
    files = release.get("files") or []
    if not files:
        await message.reply_text("❌ فایلی برای این نسخه پیدا نشد.", reply_markup=main_menu_keyboard())
        return

    file_info = next((f for f in files if f.get("primary")), None) or next((f for f in files if str(f.get("filename", "")).lower().endswith(".jar")), files[0])
    file_url = file_info.get("url")
    filename = file_info.get("filename", "mod.jar")
    if not file_url:
        await message.reply_text("❌ لینک فایل این نسخه موجود نیست.", reply_markup=main_menu_keyboard())
        return

    if status_editor is not None:
        await status_editor.edit_message_text(f"⏳ در حال دانلود **{game_version} {loader}**...", parse_mode="Markdown")
    else:
        await message.reply_text(f"⏳ در حال دانلود **{game_version} {loader}**...", parse_mode="Markdown")
    local_path = None
    try:
        local_path = await _download_mod_file(file_url, filename)
        size = local_path.stat().st_size
        mod_name = context.user_data.get("mod_name", "ماد")

        if size > TELEGRAM_MOD_LIMIT:
            text = (
                f"⚠️ فایل بیشتر از ۵۰ مگابایت است ({_format_size(size)})\n\n"
                f"🎮 نسخه: `{game_version}`\n"
                f"🔧 لودر: `{loader}`\n\n"
                f"🔗 لینک مستقیم دانلود از Modrinth:\n{file_url}"
            )
            if status_editor is not None:
                await status_editor.edit_message_text(text, parse_mode="Markdown")
                await message.reply_text("از منوی پایین استفاده کنید.", reply_markup=main_menu_keyboard())
            else:
                await message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
            return

        with open(local_path, "rb") as f:
            await message.reply_document(
                document=f, filename=filename,
                caption=(f"🎮 {mod_name}\n📦 نسخه: {game_version}\n🔧 لودر: {loader}\n💾 حجم: {_format_size(size)}"),
                reply_markup=main_menu_keyboard(),
            )
        if status_editor is not None:
            await status_editor.edit_message_text("✅ دانلود با موفقیت انجام شد!")
            await message.reply_text("از منوی پایین استفاده کنید.", reply_markup=main_menu_keyboard())
    except Exception as exc:
        logger.exception("Mod download/upload error")
        if status_editor is not None:
            await status_editor.edit_message_text(f"❌ خطا در دانلود ماد:\n{str(exc)[:500]}")
            await message.reply_text("از منوی پایین استفاده کنید.", reply_markup=main_menu_keyboard())
        else:
            await message.reply_text(f"❌ خطا در دانلود ماد:\n{str(exc)[:500]}", reply_markup=main_menu_keyboard())
    finally:
        if local_path:
            try:
                local_path.unlink()
            except OSError:
                pass

def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / 1024:.0f} KB"

# ---------------------------------------------------------------------------
# Feature 3 — File Converter
# ---------------------------------------------------------------------------

CONVERT_MAP = {
    ".pdf": [(".txt", "PDF → TXT")],
    ".docx": [(".pdf", "DOCX → PDF")],
    ".txt": [(".pdf", "TXT → PDF")],
    ".png": [(".jpg", "PNG → JPG")],
    ".jpg": [(".png", "JPG → PNG")],
    ".jpeg": [(".png", "JPEG → PNG")],
}


def _convert_pdf_to_txt(src: Path, dst: Path) -> None:
    from PyPDF2 import PdfReader
    reader = PdfReader(str(src))
    text_parts = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text_parts.append(t)
    dst.write_text("\n\n".join(text_parts), encoding="utf-8")


def _convert_docx_to_txt(src: Path, dst: Path) -> None:
    from docx import Document
    doc = Document(str(src))
    dst.write_text("\n".join(p.text for p in doc.paragraphs), encoding="utf-8")


def _convert_txt_to_pdf(src: Path, dst: Path) -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm

    text = src.read_text(encoding="utf-8-sig")

    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    ]
    font_path = next((p for p in font_candidates if Path(p).exists()), None)
    if not font_path:
        raise RuntimeError("فونت Unicode برای ساخت PDF روی سرور پیدا نشد")

    font_name = "ToolBotDejaVu"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, font_path))

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        def shape_line(line: str) -> str:
            return get_display(arabic_reshaper.reshape(line))
    except ImportError:
        def shape_line(line: str) -> str:
            return line

    c = canvas.Canvas(str(dst), pagesize=A4)
    width, height = A4
    margin = 2 * cm
    max_width = width - 2 * margin
    y = height - margin
    line_height = 16
    font_size = 11
    c.setFont(font_name, font_size)

    for raw_line in text.splitlines() or [""]:
        line = shape_line(raw_line)
        if not line:
            chunks = [""]
        else:
            chunks = []
            current = ""
            for ch in line:
                candidate = current + ch
                if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
                    chunks.append(current)
                    current = ch
                else:
                    current = candidate
            chunks.append(current)

        for chunk in chunks:
            if y < margin:
                c.showPage()
                c.setFont(font_name, font_size)
                y = height - margin
            c.drawString(margin, y, chunk)
            y -= line_height

    c.save()


def _convert_docx_to_pdf_sync(src: Path, dst: Path) -> None:
    """Convert DOCX to PDF using LibreOffice."""
    binary = shutil.which("libreoffice") or shutil.which("soffice")
    if not binary:
        raise RuntimeError("LibreOffice روی سرور نصب نیست")

    workdir = tempfile.mkdtemp(prefix="docx2pdf_")
    try:
        # LibreOffice writes the PDF beside the input/output directory.
        input_path = Path(workdir) / src.name
        shutil.copy2(src, input_path)
        proc = subprocess.run(
            [binary, "--headless", "--convert-to", "pdf",
             "--outdir", workdir, str(input_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
        generated = Path(workdir) / (input_path.stem + ".pdf")
        if proc.returncode != 0 or not generated.exists():
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"تبدیل Word به PDF انجام نشد{': ' + detail[:300] if detail else ''}")
        shutil.copy2(generated, dst)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _convert_png_to_jpg(src: Path, dst: Path) -> None:
    img = Image.open(src)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(dst, "JPEG", quality=95)


def _convert_jpg_to_png(src: Path, dst: Path) -> None:
    Image.open(src).save(dst, "PNG")


CONVERT_FUNCS = {
    (".pdf", ".txt"): _convert_pdf_to_txt,
    (".docx", ".pdf"): _convert_docx_to_pdf_sync,
    (".txt", ".pdf"): _convert_txt_to_pdf,
    (".png", ".jpg"): _convert_png_to_jpg,
    (".jpg", ".png"): _convert_jpg_to_png,
    (".jpeg", ".png"): _convert_jpg_to_png,
}





def _convert_images_to_pdf(paths: list[Path], dst: Path) -> None:
    """Convert downloaded images to a single PDF while preserving their order."""
    if not paths:
        raise ValueError("هیچ عکسی برای ساخت PDF وجود ندارد")

    images = []
    try:
        from PIL import Image, ImageOps

        for path in paths:
            with Image.open(path) as img:
                # Apply EXIF orientation before converting, so phone photos are upright.
                img = ImageOps.exif_transpose(img)
                if img.mode != "RGB":
                    if img.mode in ("RGBA", "LA"):
                        background = Image.new("RGB", img.size, "white")
                        alpha = img.getchannel("A")
                        background.paste(img.convert("RGB"), mask=alpha)
                        img = background
                    else:
                        img = img.convert("RGB")
                else:
                    img = img.copy()
                images.append(img)

        first, rest = images[0], images[1:]
        first.save(
            str(dst),
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=rest,
        )
    finally:
        for img in images:
            try:
                img.close()
            except Exception:
                pass


async def _finish_images_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    image_ids = context.user_data.get("image_pdf_ids", [])
    if not image_ids:
        await update.message.reply_text("❌ هنوز عکسی دریافت نشده. اول عکس‌ها را به ترتیب بفرست.", reply_markup=back_keyboard())
        return

    status = await update.message.reply_text("⏳ در حال ساخت PDF با همان ترتیب عکس‌ها...")
    paths: list[Path] = []
    out_path = WORK_DIR / f"images_{update.effective_user.id}_{update.message.message_id}.pdf"
    try:
        for i, file_id in enumerate(image_ids, start=1):
            tg_file = await context.bot.get_file(file_id)
            path = WORK_DIR / f"imagepdf_{update.effective_user.id}_{i}_{file_id}.jpg"
            await tg_file.download_to_drive(str(path))
            paths.append(path)

        await asyncio.get_running_loop().run_in_executor(
            None, _convert_images_to_pdf, paths, out_path
        )
        with open(out_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="images.pdf",
                caption=f"✅ PDF آماده شد.\n🖼️ تعداد عکس‌ها: {len(paths)}\n📌 ترتیب عکس‌ها دقیقاً حفظ شد.",
                reply_markup=main_menu_keyboard(),
            )
        await status.delete()
    except Exception as exc:
        logger.exception("Images to PDF error")
        await status.edit_text(f"❌ خطا در ساخت PDF:\n{str(exc)[:500]}", reply_markup=main_menu_keyboard())
    finally:
        for path in paths + [out_path]:
            try:
                path.unlink()
            except OSError:
                pass
        context.user_data.pop("image_pdf_ids", None)
        context.user_data.pop("state", None)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not msg.photo:
        return

    state = context.user_data.get("state")

    # عکس‌های ارسالی در بخش تبدیل، به‌صورت خودکار وارد حالت عکس → PDF می‌شوند.
    if state == "convert_or_text":
        context.user_data["state"] = "image_to_pdf"
        context.user_data["image_pdf_ids"] = []

    if context.user_data.get("state") == "image_to_pdf":
        # Telegram photo sizes are ordered small → large; keep the largest one.
        file_id = msg.photo[-1].file_id
        context.user_data.setdefault("image_pdf_ids", []).append(file_id)
        count = len(context.user_data["image_pdf_ids"])
        await msg.reply_text(
            f"✅ عکس {count} دریافت شد و در جایگاه {count} قرار گرفت.\n"
            "عکس بعدی را بفرست یا وقتی تمام شد «پایان» را بزن.",
            reply_markup=reply_keyboard([["✅ پایان"], ["◀️ بازگشت به منوی اصلی"]]),
        )
        return

    await msg.reply_text(
        "❌ برای تبدیل عکس به PDF، از منوی «🔄 تبدیل» وارد شوید و عکس‌ها را به ترتیب بفرستید.",
        reply_markup=main_menu_keyboard(),
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("state")

    msg = update.message
    doc = msg.document
    if not doc or not doc.file_name:
        return
    if context.user_data.get("state") in ("mod_query", "barcode_text", "v2ray_add"):
        return

    # If in convert_or_text state and user sends a file, switch to file conversion
    if context.user_data.get("state") == "convert_or_text":
        context.user_data["state"] = None

    ext = Path(doc.file_name).suffix.lower()
    if ext not in CONVERT_MAP:
        await msg.reply_text(f"❌ فرمت `{ext}` پشتیبانی نمی‌شود.\nفرمت‌های مجاز: PDF, DOCX, TXT, PNG, JPG", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        return
    if doc.file_size and doc.file_size > TELEGRAM_CONVERT_LIMIT:
        await msg.reply_text("❌ حجم فایل بیش از ۲۰ مگابایت است.", reply_markup=main_menu_keyboard())
        return

    buttons = [[desc] for dst_ext, desc in CONVERT_MAP[ext]]
    buttons.append(["◀️ بازگشت به منوی اصلی"])
    context.user_data["convert_choices"] = {desc: dst_ext for dst_ext, desc in CONVERT_MAP[ext]}
    context.user_data["state"] = "convert_choice"
    context.user_data["convert_file_id"] = doc.file_id
    context.user_data["convert_file_name"] = doc.file_name
    context.user_data["convert_src_ext"] = ext
    await msg.reply_text(f"📄 فایل: {doc.file_name}\n\nچه تبدیلی انجام دهید؟", reply_markup=reply_keyboard(buttons))


async def _do_convert_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == "◀️ بازگشت به منوی اصلی":
        await handle_menu_text(update, context)
        return
    dst_ext = context.user_data.get("convert_choices", {}).get(text)
    if not dst_ext:
        await update.message.reply_text("❌ یکی از تبدیل‌های نمایش‌داده‌شده را انتخاب کنید.")
        return
    src_ext = context.user_data.get("convert_src_ext", "")
    file_id = context.user_data.get("convert_file_id")
    file_name = context.user_data.get("convert_file_name", "file")
    convert_func = CONVERT_FUNCS.get((src_ext, dst_ext))
    if not file_id or not convert_func:
        await update.message.reply_text("❌ اطلاعات فایل منقضی شده است. دوباره فایل را ارسال کنید.", reply_markup=main_menu_keyboard())
        return
    await update.message.reply_text("⏳ در حال تبدیل فایل...")
    src_path = dst_path = None
    try:
        tg_file = await context.bot.get_file(file_id)
        src_path = WORK_DIR / f"conv_{file_id}{src_ext}"
        dst_path = WORK_DIR / f"conv_{file_id}{dst_ext}"
        await tg_file.download_to_drive(str(src_path))
        await asyncio.get_running_loop().run_in_executor(None, convert_func, src_path, dst_path)
        if not dst_path.exists() or dst_path.stat().st_size == 0:
            raise RuntimeError("فایل خروجی ساخته نشد")
        if dst_path.stat().st_size > TELEGRAM_MOD_LIMIT:
            raise RuntimeError("حجم فایل خروجی بیشتر از ۵۰ مگابایت است و تلگرام اجازه ارسال آن را نمی‌دهد")
        with open(dst_path, "rb") as f:
            await update.message.reply_document(document=f, filename=Path(file_name).stem + dst_ext, caption=f"✅ تبدیل {src_ext} → {dst_ext} انجام شد!", reply_markup=main_menu_keyboard())
    except Exception as exc:
        logger.exception("Convert error")
        await update.message.reply_text(f"❌ خطا در تبدیل:\n{str(exc)[:500]}", reply_markup=main_menu_keyboard())
    finally:
        for pth in (src_path, dst_path):
            if pth:
                try: pth.unlink()
                except OSError: pass
    context.user_data.pop("state", None)
    context.user_data.pop("convert_choices", None)


async def convert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    dst_ext = query.data.split(":", 1)[1]
    src_ext = context.user_data.get("convert_src_ext", "")
    file_id = context.user_data.get("convert_file_id")
    file_name = context.user_data.get("convert_file_name", "file")
    convert_func = CONVERT_FUNCS.get((src_ext, dst_ext))
    if not file_id or not convert_func:
        await query.edit_message_text("❌ اطلاعات فایل منقضی شده است. دوباره فایل را ارسال کنید.", reply_markup=back_button())
        return

    await query.edit_message_text("⏳ در حال تبدیل فایل...")
    src_path = dst_path = None
    try:
        tg_file = await context.bot.get_file(file_id)
        src_path = WORK_DIR / f"conv_{file_id}{src_ext}"
        dst_path = WORK_DIR / f"conv_{file_id}{dst_ext}"
        await tg_file.download_to_drive(str(src_path))
        await asyncio.get_running_loop().run_in_executor(None, convert_func, src_path, dst_path)
        with open(dst_path, "rb") as f:
            await query.message.reply_document(document=f, filename=Path(file_name).stem + dst_ext, caption=f"✅ تبدیل {src_ext} → {dst_ext} انجام شد!", reply_markup=back_button())
        await query.edit_message_text("✅ فایل با موفقیت تبدیل شد.", reply_markup=back_button())
    except Exception as exc:
        logger.exception("Convert error")
        await query.edit_message_text(f"❌ خطا در تبدیل:\n{str(exc)[:500]}", reply_markup=back_button())
    finally:
        for p in (src_path, dst_path):
            if p:
                try: p.unlink()
                except OSError: pass

async def _do_text_to_txt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    if text.strip() == "◀️ بازگشت به منوی اصلی":
        await handle_menu_text(update, context)
        return
    if not text:
        await update.message.reply_text("❌ متن نمی‌تواند خالی باشد.")
        return

    try:
        data = io.BytesIO(text.encode("utf-8"))
        data.name = "text.txt"
        data.seek(0)
        await update.message.reply_document(
            document=data,
            filename="text.txt",
            caption="📝 فایل TXT آماده شد.",
            reply_markup=main_menu_keyboard(),
        )
        context.user_data.pop("state", None)
    except Exception as exc:
        logger.exception("Text to TXT error")
        await update.message.reply_text(
            f"❌ خطا در ساخت فایل TXT:\n`{str(exc)[:500]}`",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )


async def _do_convert_or_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input in the convert state — offer format choices."""
    text = (update.message.text or "").strip()
    if text == "◀️ بازگشت به منوی اصلی":
        context.user_data.clear()
        await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
        return

    # Store the text and show format options
    context.user_data["convert_text"] = text
    context.user_data["state"] = "text_convert_choice"
    buttons = [
        ["PDF 📄"],
        ["Word (DOCX) 📝"],
        ["TXT 📃"],
        ["◀️ بازگشت به منوی اصلی"],
    ]
    await update.message.reply_text(
        f"📝 متن دریافت شد ({len(text)} کاراکتر)\n\nفرمت خروجی را انتخاب کنید:",
        reply_markup=reply_keyboard(buttons),
    )


async def _do_text_convert_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text conversion format selection."""
    text = (update.message.text or "").strip()
    if text == "◀️ بازگشت به منوی اصلی":
        context.user_data.clear()
        await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
        return

    content = context.user_data.get("convert_text", "")
    if not content:
        await update.message.reply_text("❌ متن منقضی شده است. دوباره تایپ کنید.", reply_markup=back_keyboard())
        context.user_data["state"] = "convert_or_text"
        return

    context.user_data["state"] = None
    context.user_data.pop("convert_text", None)

    status_msg = await update.message.reply_text("⏳ در حال ساخت فایل...")
    out_path = None
    try:
        if "PDF" in text:
            out_path = WORK_DIR / "converted.pdf"
            await asyncio.get_running_loop().run_in_executor(None, _convert_text_to_pdf_sync, content, out_path)
            filename = "converted.pdf"

        elif "Word" in text or "DOCX" in text:
            out_path = WORK_DIR / "converted.docx"
            await asyncio.get_running_loop().run_in_executor(None, _convert_text_to_docx, content, out_path)
            filename = "converted.docx"

        elif "TXT" in text:
            out_path = WORK_DIR / "converted.txt"
            out_path.write_text(content, encoding="utf-8")
            filename = "converted.txt"

        else:
            await status_msg.edit_text("❌ فرمت نامعتبر.", reply_markup=back_keyboard())
            return

        with open(out_path, "rb") as f:
            await update.message.reply_document(
                document=f, filename=filename,
                caption=f"✅ فایل {text.split()[0]} ساخته شد!",
                reply_markup=main_menu_keyboard(),
            )
        await status_msg.delete()

    except Exception as exc:
        logger.exception("Text convert error")
        await status_msg.edit_text(f"❌ خطا در ساخت فایل:\n{str(exc)[:500]}", reply_markup=main_menu_keyboard())
    finally:
        if out_path:
            try:
                out_path.unlink()
            except OSError:
                pass


def _convert_text_to_pdf_sync(text: str, dst: Path) -> None:
    """Convert plain text to PDF (sync)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import cm

    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    ]
    font_path = next((p for p in font_candidates if Path(p).exists()), None)
    if not font_path:
        raise RuntimeError("فونت Unicode برای ساخت PDF روی سرور پیدا نشد")

    font_name = "ToolBotDejaVu"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, font_path))

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        def shape_line(line: str) -> str:
            return get_display(arabic_reshaper.reshape(line))
    except ImportError:
        def shape_line(line: str) -> str:
            return line

    c = canvas.Canvas(str(dst), pagesize=A4)
    width, height = A4
    margin = 2 * cm
    max_width = width - 2 * margin
    y = height - margin
    line_height = 16
    font_size = 11
    c.setFont(font_name, font_size)

    for raw_line in text.splitlines() or [""]:
        line = shape_line(raw_line)
        if not line:
            chunks = [""]
        else:
            chunks = []
            current = ""
            for ch in line:
                candidate = current + ch
                if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
                    chunks.append(current)
                    current = ch
                else:
                    current = candidate
            chunks.append(current)

        for chunk in chunks:
            if y < margin:
                c.showPage()
                c.setFont(font_name, font_size)
                y = height - margin
            c.drawString(margin, y, chunk)
            y -= line_height

    c.save()


def _convert_text_to_docx(text: str, dst: Path) -> None:
    """Convert text to DOCX with table and formatting support."""
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # Set default font
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(11)

    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # Detect markdown-style tables (| col1 | col2 |)
        if '|' in line and line.strip().startswith('|'):
            table_lines = []
            while i < len(lines) and '|' in lines[i] and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                # Skip separator rows (|---|---|)
                if all(set(c.strip()) <= set('-: ') for c in cells):
                    i += 1
                    continue
                table_lines.append(cells)
                i += 1

            if table_lines:
                max_cols = max(len(row) for row in table_lines)
                table = doc.add_table(rows=len(table_lines), cols=max_cols, style='Table Grid')
                for ri, row_data in enumerate(table_lines):
                    for ci, cell_text in enumerate(row_data):
                        if ci < max_cols:
                            cell = table.cell(ri, ci)
                            cell.text = cell_text
                            # Bold first row (header)
                            if ri == 0:
                                for paragraph in cell.paragraphs:
                                    for run in paragraph.runs:
                                        run.bold = True
                doc.add_paragraph('')  # spacer
            continue

        # Empty line = paragraph break
        if not line.strip():
            doc.add_paragraph('')
            i += 1
            continue

        # Heading detection (# heading)
        if line.startswith('# '):
            doc.add_heading(line[2:], level=1)
        elif line.startswith('## '):
            doc.add_heading(line[3:], level=2)
        elif line.startswith('### '):
            doc.add_heading(line[4:], level=3)
        else:
            # Bold text **text**
            para = doc.add_paragraph()
            parts = re.split(r'(\*\*.*?\*\*)', line)
            for part in parts:
                if part.startswith('**') and part.endswith('**'):
                    run = para.add_run(part[2:-2])
                    run.bold = True
                else:
                    para.add_run(part)

        i += 1

    doc.save(str(dst))


# ---------------------------------------------------------------------------
# Feature 4 — QR code (replaces CODE128 so Persian/Unicode works)
# ---------------------------------------------------------------------------

async def _do_barcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["state"] = None
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ متن نمی‌تواند خالی باشد.")
        return

    status_msg = await update.message.reply_text("⏳ در حال ساخت QR کد...")
    try:
        import qrcode
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image()
        out = io.BytesIO()
        img.save(out, format="PNG")
        out.seek(0)
        await update.message.reply_photo(photo=out, caption="📊 QR کد با موفقیت ساخته شد.", reply_markup=main_menu_keyboard())
        await status_msg.delete()
    except Exception as exc:
        logger.exception("QR error")
        await status_msg.edit_text(f"❌ خطا در ساخت QR:\n`{str(exc)[:400]}`", parse_mode="Markdown")

# ---------------------------------------------------------------------------
# Feature 5 — V2Ray config manager
# ---------------------------------------------------------------------------

V2RAY_VALID_PREFIXES = ("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hysteria://", "tuic://")


def _normalise_config_text(text: str) -> list[str]:
    """Accept real newlines AND escaped \\n sequences sent as plain text."""
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line for line in lines if any(line.lower().startswith(prefix) for prefix in V2RAY_VALID_PREFIXES)]


async def handle_v2ray_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if text.lower() == "/done":
        configs = context.user_data.get("v2ray_configs", [])
        if not configs:
            await update.message.reply_text("❌ هیچ کانفینگی اضافه نشد.", reply_markup=main_menu_keyboard())
            context.user_data.pop("state", None)
            return
        uid = update.effective_user.id
        await update.message.reply_text("⏳ در حال ذخیره در GitHub...")
        try:
            await _save_to_github(uid, "\n".join(configs))
            link = f"{GITHUB_RAW_BASE}/configs/{uid}.txt"
            await update.message.reply_text(
                f"✅ {len(configs)} کانفینگ ذخیره شد!\n\n🔗 ساب لینک شما:\n`{link}`\n\nاین لینک را در اپلیکیشن V2Ray خود کپی کنید.",
                reply_markup=main_menu_keyboard(), parse_mode="Markdown",
            )
        except Exception as exc:
            logger.exception("V2Ray save error")
            await update.message.reply_text(f"❌ خطا در ذخیره:\n`{str(exc)[:300]}`", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        context.user_data.pop("state", None)
        context.user_data.pop("v2ray_configs", None)
        return

    valid_lines = _normalise_config_text(text)
    if not valid_lines:
        await update.message.reply_text(
            "❌ کانفینگ پیدا نشد.\n"
            "هر خط باید با یکی از این‌ها شروع شود: vmess:// ، vless:// ، trojan:// ، ss:// ، ssr:// ، hysteria:// ، tuic://\n\n"
            "حتی اگر بین کانفینگ‌ها `\\n` نوشته شده باشد هم قبول می‌کنم.\nبرای پایان /done را بزنید."
        )
        return

    configs = context.user_data.setdefault("v2ray_configs", [])
    added = 0
    for line in valid_lines:
        if line not in configs:
            configs.append(line)
            added += 1
    await update.message.reply_text(f"✅ {added} کانفینگ اضافه شد (مجموع: {len(configs)})\nکانفینگ بعدی را بفرستید یا /done بزنید.")


async def _handle_v2ray_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        context.user_data["state"] = None
        await update.message.reply_text("❌ حذف لغو شد.", reply_markup=v2ray_menu_keyboard())
        return
    if text != "✅ بله، حذف کن":
        await update.message.reply_text("لطفاً یکی از گزینه‌های تأیید یا لغو را انتخاب کنید.")
        return
    uid = update.effective_user.id
    try:
        await _delete_github_file(f"configs/{uid}.txt", f"delete: {uid}.txt")
        context.user_data.clear()
        await update.message.reply_text("✅ همه کانفینگ‌ها حذف شدند.", reply_markup=v2ray_menu_keyboard())
    except FileNotFoundError:
        context.user_data["state"] = None
        await update.message.reply_text("ℹ️ کانفینگی برای حذف وجود ندارد.", reply_markup=v2ray_menu_keyboard())
    except Exception as exc:
        logger.exception("V2Ray delete error")
        context.user_data["state"] = None
        await update.message.reply_text(f"❌ خطا در حذف: {str(exc)[:150]}", reply_markup=v2ray_menu_keyboard())


async def _github_headers() -> dict[str, str]:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN روی Railway تنظیم نشده است")
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


async def _upload_release_asset(local_path: Path, filename: str, message: str) -> str:
    """Upload a large binary as a GitHub Release asset and return its direct download URL."""
    headers = await _github_headers()
    api_base = f"https://api.github.com/repos/{GITHUB_REPO}"
    async with httpx.AsyncClient(timeout=300) as client:
        releases = await client.get(f"{api_base}/releases", headers=headers, params={"per_page": 100})
        if releases.status_code != 200:
            raise RuntimeError(f"GitHub releases lookup error: {releases.status_code} {releases.text[:300]}")
        release = next((r for r in releases.json() if r.get("tag_name") == "toolbot-mods"), None)
        if release is None:
            create_body = {
                "tag_name": "toolbot-mods",
                "name": "ToolBot Minecraft Mods",
                "body": "Minecraft mods uploaded by ToolBot.",
                "target_commitish": GITHUB_BRANCH,
                "draft": False,
                "prerelease": False,
            }
            created = await client.post(f"{api_base}/releases", headers=headers, json=create_body)
            if created.status_code not in (201,):
                raise RuntimeError(f"GitHub release create error: {created.status_code} {created.text[:300]}")
            release = created.json()

        release_id = release["id"]
        assets = await client.get(f"{api_base}/releases/{release_id}/assets", headers=headers, params={"per_page": 100})
        if assets.status_code != 200:
            raise RuntimeError(f"GitHub assets lookup error: {assets.status_code} {assets.text[:300]}")
        existing = next((a for a in assets.json() if a.get("name") == filename), None)
        if existing:
            deleted = await client.delete(f"{api_base}/releases/assets/{existing['id']}", headers=headers)
            if deleted.status_code != 204:
                raise RuntimeError(f"GitHub old asset delete error: {deleted.status_code} {deleted.text[:200]}")

        upload_url = release.get("upload_url", "").split("{", 1)[0]
        if not upload_url:
            upload_url = f"https://uploads.github.com/repos/{GITHUB_REPO}/releases/{release_id}/assets"
        content_type = "application/java-archive" if filename.lower().endswith(".jar") else "application/octet-stream"
        upload_headers = dict(headers)
        upload_headers["Content-Type"] = content_type
        data = local_path.read_bytes()
        response = await client.post(upload_url, headers=upload_headers, params={"name": filename}, content=data)
        if response.status_code != 201:
            raise RuntimeError(f"GitHub release asset upload error: {response.status_code} {response.text[:300]}")
        result = response.json()
        return result.get("browser_download_url") or result.get("url")


async def _upload_binary_to_github(repo_path: str, local_path: Path, message: str) -> str:
    """Upload a binary through GitHub Contents API and return a raw direct URL."""
    data = local_path.read_bytes()
    if len(data) > GITHUB_CONTENTS_LIMIT:
        raise RuntimeError("فایل بزرگ‌تر از ۱۰۰ مگابایت است و با GitHub Contents API قابل آپلود نیست")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{repo_path}"
    headers = await _github_headers()
    encoded = base64.b64encode(data).decode("ascii")
    async with httpx.AsyncClient(timeout=180) as client:
        existing = await client.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
        body = {"message": message, "content": encoded, "branch": GITHUB_BRANCH}
        if existing.status_code == 200:
            body["sha"] = existing.json().get("sha")
        elif existing.status_code != 404:
            raise RuntimeError(f"GitHub lookup error: {existing.status_code} {existing.text[:200]}")
        resp = await client.put(url, headers=headers, json=body)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"GitHub upload error: {resp.status_code} {resp.text[:300]}")
    return f"{GITHUB_RAW_BASE}/{repo_path}"


async def _load_v2ray_configs(user_id: int) -> list[str]:
    path = f"configs/{user_id}.txt"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = await _github_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub config lookup error: {resp.status_code} {resp.text[:200]}")
        encoded = resp.json().get("content", "").replace("\n", "")
        try:
            content = base64.b64decode(encoded).decode("utf-8")
        except Exception:
            return []
        return _normalise_config_text(content)


async def _save_to_github(user_id: int, content: str) -> None:
    path = f"configs/{user_id}.txt"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = await _github_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        existing = await client.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
        body = {
            "message": f"update: user {user_id} configs",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": GITHUB_BRANCH,
        }
        if existing.status_code == 200:
            body["sha"] = existing.json().get("sha")
        elif existing.status_code != 404:
            raise RuntimeError(f"GitHub lookup error: {existing.status_code} {existing.text[:200]}")
        resp = await client.put(url, headers=headers, json=body)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"GitHub API error: {resp.status_code} {resp.text[:300]}")


async def _delete_github_file(path: str, message: str) -> None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = await _github_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        existing = await client.get(url, headers=headers, params={"ref": GITHUB_BRANCH})
        if existing.status_code == 404:
            raise FileNotFoundError(path)
        existing.raise_for_status()
        sha = existing.json().get("sha")
        resp = await client.request("DELETE", url, headers=headers, json={"message": message, "sha": sha, "branch": GITHUB_BRANCH})
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"GitHub delete error: {resp.status_code} {resp.text[:200]}")

# ---------------------------------------------------------------------------
# Feature 6 — Steam Price Checker
# ---------------------------------------------------------------------------

STEAM_REGIONS = [
    ("🇺🇸 US", "us"),
    ("🇮🇳 IN", "in"),
    ("🇧🇷 BR", "br"),
    ("🇨🇳 CN", "cn"),
]

STEAM_REGION_NAMES = {code: name for name, code in STEAM_REGIONS}

# Currency information used only for displaying the Steam result.
STEAM_CURRENCY_INFO = {
    "USD": ("$", "دلار"),
    "EUR": ("€", "یورو"),
    "TRY": ("₺", "لیر ترکیه"),
    "ARS": ("ARS ", "پزوی آرژانتین"),
    "INR": ("₹", "روپیه"),
    "BRL": ("R$", "رئال برزیل"),
    "CNY": ("¥", "یوان"),
}


async def _steam_search(query: str) -> list[dict]:
    """Search Steam store for games."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": query, "l": "english", "cc": "us"},
        )
        resp.raise_for_status()

        result = resp.json()
        if isinstance(result, dict):
            items = result.get("items", [])
            return items if isinstance(items, list) else []

        return []


async def _steam_get_price(app_id: int, cc: str) -> dict | None:
    """Get price info for a Steam app in a specific region."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": app_id, "cc": cc},
        )
        resp.raise_for_status()

        data = resp.json()
        if not isinstance(data, dict):
            return None

        app_data = data.get(str(app_id))
        if not isinstance(app_data, dict):
            return None

        if not app_data.get("success"):
            return None

        info = app_data.get("data")
        if not isinstance(info, dict):
            return None

        # Free game — no price_overview
        if info.get("is_free"):
            return {"free": True, "currency": "", "initial": 0, "final": 0, "discount_percent": 0}

        price_overview = info.get("price_overview")
        if not isinstance(price_overview, dict):
            return None

        return price_overview


async def _convert_to_toman(amount: float, currency: str) -> int | None:
    """Convert a foreign-currency amount to approximately toman using open.er-api.com."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            resp.raise_for_status()
            rates = resp.json().get("rates", {})
            irr = rates.get("IRR")
            foreign = rates.get(currency)
            if not irr or not foreign:
                return None
            # amount in foreign currency → IRR → Toman
            toman = amount * irr / foreign / 10
            return int(round(toman / 1000) * 1000)
    except Exception:
        return None


async def _do_steam_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle game name input and search Steam."""
    context.user_data["state"] = None
    query = update.message.text.strip()
    if not query:
        await update.message.reply_text("❌ نام بازی نمی‌تواند خالی باشد.")
        return

    status_msg = await update.message.reply_text("🔍 در حال جستجو در Steam...")
    try:
        results = await _steam_search(query)
    except Exception as exc:
        logger.exception("Steam search error")
        await status_msg.edit_text(f"❌ خطا در جستجو:\n`{str(exc)[:300]}`", parse_mode="Markdown")
        return

    if not results:
        await status_msg.edit_text("❌ بازی با این نام پیدا نشد.", reply_markup=back_keyboard())
        return

    # Build numbered reply keyboard
    buttons = []
    labels = {}
    for i, item in enumerate(results[:8]):
        name = item.get("name", "Unknown")
        label = f"{i+1}️⃣ {name}"
        buttons.append([label])
        labels[str(i)] = {"app_id": item["id"], "name": name}

    buttons.append(["◀️ بازگشت به منوی اصلی"])
    context.user_data["state"] = "steam_region"
    context.user_data["steam_results"] = labels

    await status_msg.delete()
    await update.message.reply_text("🔍 نتایج جستجو:", reply_markup=reply_keyboard(buttons))

async def _do_steam_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle game selection or region selection."""
    text = update.message.text.strip()
    if text == "◀️ بازگشت به منوی اصلی":
        context.user_data.clear()
        await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
        return

    # Handle "🔄 تغییر ریجن" — show region list again
    if text == "🔄 تغییر ریجن":
        game_name = context.user_data.get("steam_game_name", "")
        app_id = context.user_data.get("steam_app_id")
        if not app_id:
            await update.message.reply_text("❌ ابتدا یک بازی انتخاب کنید.", reply_markup=back_keyboard())
            return
        region_buttons = [[f"{name}"] for name, code in STEAM_REGIONS]
        region_buttons.append(["◀️ بازگشت به منوی اصلی"])
        await update.message.reply_text(
            f"🎮 **{game_name}**\n\n🌐 ریجن جدید را انتخاب کنید:",
            reply_markup=reply_keyboard(region_buttons),
            parse_mode="Markdown",
        )
        return

    # Check if user is picking a game (numbered list)
    if text.startswith("1️⃣") or text.startswith("2️⃣") or text.startswith("3️⃣") or text.startswith("4️⃣") or \
       text.startswith("5️⃣") or text.startswith("6️⃣") or text.startswith("7️⃣") or text.startswith("8️⃣"):
        results = context.user_data.get("steam_results", {})
        # Extract the number from emoji
        for key, val in results.items():
            expected_prefix = f"{['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣'][int(key)]} "
            if text.startswith(expected_prefix.strip()):
                context.user_data["steam_app_id"] = val["app_id"]
                context.user_data["steam_game_name"] = val["name"]
                # Show region selection
                region_buttons = [[f"{name}"] for name, code in STEAM_REGIONS]
                region_buttons.append(["◀️ بازگشت به منوی اصلی"])
                context.user_data["state"] = "steam_region"
                await update.message.reply_text(
                    f"🎮 **{val['name']}**\n\n🌐 ریجن مورد نظر را انتخاب کنید:",
                    reply_markup=reply_keyboard(region_buttons),
                    parse_mode="Markdown",
                )
                return

        # If not a numbered option, maybe it's a region name
        pass

    # Check if user is picking a region
    app_id = context.user_data.get("steam_app_id")
    game_name = context.user_data.get("steam_game_name", "")

    if not app_id:
        await update.message.reply_text("❌ ابتدا یک بازی انتخاب کنید.", reply_markup=back_keyboard())
        return

    # Find region code
    region_code = None
    for name, code in STEAM_REGIONS:
        if text == name:
            region_code = code
            break

    if not region_code:
        await update.message.reply_text("❌ یکی از ریجن‌های نمایش داده شده را انتخاب کنید.")
        return

    status_msg = await update.message.reply_text("⏳ در حال دریافت قیمت...")
    try:
        price = await _steam_get_price(app_id, region_code)
    except Exception as exc:
        logger.exception("Steam price error")
        await status_msg.edit_text(f"❌ خطا در دریافت قیمت:\n`{str(exc)[:300]}`", parse_mode="Markdown")
        return

    if not price:
        await status_msg.edit_text(
            f"🎮 **{game_name}**\n\n❌ قیمتی برای این ریجن یافت نشد.\nممکن است بازی رایگان یا ناموجود باشد.",
            reply_markup=back_keyboard(),
            parse_mode="Markdown",
        )
        return

    # Free game
    try:
        is_free = price.get("free")
    except Exception:
        is_free = False

    if is_free:
        text = (
            f"🎮 {game_name}\n\n"
            f"🆓 Free: رایگان\n\n"
            f"🌐 Region: {STEAM_REGION_NAMES.get(region_code, region_code)}"
        )

        change_region_buttons = [
            ["🔄 تغییر ریجن"],
            ["◀️ بازگشت به منوی اصلی"],
        ]

        await status_msg.edit_text(text, parse_mode="Markdown")
        await update.message.reply_text(
            "ریجن عوض کنید یا برگردید:",
            reply_markup=reply_keyboard(change_region_buttons),
        )

        context.user_data["state"] = "steam_region"
        return

    initial = price.get("initial", 0) or 0
    final = price.get("final", 0) or 0
    discount = price.get("discount_percent", 0) or 0
    currency = str(price.get("currency", "") or "").upper()

    initial_value = initial / 100
    final_value = final / 100

    currency_info = STEAM_CURRENCY_INFO.get(
        currency,
        (currency + " ", currency)
    )

    currency_symbol, currency_name = currency_info

    initial_fmt = f"{initial_value:,.2f}"
    final_fmt = f"{final_value:,.2f}"

    # Get fresh TGJU rates every time the user requests a price.
    try:
        initial_toman = await _convert_to_toman(initial_value, currency)
    except Exception:
        initial_toman = None
    try:
        final_toman = await _convert_to_toman(final_value, currency)
    except Exception:
        final_toman = None

    initial_toman_text = (
        f"{initial_toman:,} تومان"
        if initial_toman is not None
        else "قابل محاسبه نیست"
    )

    final_toman_text = (
        f"{final_toman:,} تومان"
        if final_toman is not None
        else "قابل محاسبه نیست"
    )

    region_name = STEAM_REGION_NAMES.get(region_code, region_code)

    text = (
        f"💰 {game_name}\n\n"
        f"💵 Original: {currency_symbol}{initial_fmt} ({currency_name})\n"
        f"   🇮🇷 حدود {initial_toman_text}\n\n"
        f"🔥 Current: {currency_symbol}{final_fmt} ({currency_name})\n"
        f"   🇮🇷 حدود {final_toman_text}\n\n"
        f"📉 Discount: {discount}%\n\n"
        f"🌐 Region: {region_name}"
    )

    change_region_buttons = [["🔄 تغییر ریجن"], ["◀️ بازگشت به منوی اصلی"]]

    await status_msg.edit_text(text, parse_mode="Markdown")
    await update.message.reply_text("ریجن عوض کنید یا برگردید:", reply_markup=reply_keyboard(change_region_buttons))

    # Now set state to handle "تغییر ریجن" button
    context.user_data["state"] = "steam_region"

# ---------------------------------------------------------------------------
# Feature 7 — Instagram Downloader
# ---------------------------------------------------------------------------

INSTAGRAM_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:instagram\.com|instagr\.am)"
    r"/(?:p|reel|reels|stories|tv)/([A-Za-z0-9_-]+)"
)


def _extract_instagram_shortcode(text: str) -> str | None:
    """Extract Instagram shortcode from a URL (direct or share.google)."""
    # Direct Instagram link
    m = INSTAGRAM_URL_RE.search(text)
    if m:
        return m.group(1)
    # share.google with instagram URL as parameter (URL-encoded or not)
    m = re.search(r"instagram\.com(?:%2F|/)p(?:%2F|/)([A-Za-z0-9_-]+)", text)
    if m:
        return m.group(1)
    return None


async def _resolve_share_google(url: str) -> str | None:
    """Follow share.google redirect to get the real Instagram URL."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            final_url = str(resp.url)
            m = INSTAGRAM_URL_RE.search(final_url)
            if m:
                return m.group(1)
            return None
    except Exception:
        return None


async def _do_insta_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download Instagram post/reel/story by URL."""
    text = (update.message.text or "").strip()
    if text == "◀️ بازگشت به منوی اصلی":
        context.user_data["state"] = None
        await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
        return

    # Try to extract shortcode directly (handles share.google parameters too)
    shortcode = _extract_instagram_shortcode(text)

    # If not found, try following share.google redirect
    if not shortcode and "share.google" in text.lower():
        status_msg = await update.message.reply_text("⏳ در حال پیگیری لینک...")
        shortcode = await _resolve_share_google(text)

    if not shortcode:
        await update.message.reply_text(
            "❌ لینک اینستاگرام معتبر نیست.\n\n"
            "لینک پست، ریلز یا استوری را ارسال کنید:\n"
            "`https://www.instagram.com/p/ABC123/`\n"
            "یا لینک share.google",
            reply_markup=reply_keyboard([["◀️ بازگشت به منوی اصلی"]]),
            parse_mode="Markdown",
        )
        return

    status_msg = await update.message.reply_text("⏳ در حال دانلود از اینستاگرام...")

    try:
        import instaloader
        L = instaloader.Instaloader(
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
        )

        post = instaloader.Post.from_shortcode(L.context, shortcode)

        downloaded_files = []

        if post.typename == "GraphSidecar":
            # Carousel post — multiple images/videos
            for i, node in enumerate(post.get_sidecar_nodes()):
                if node.is_video:
                    url = node.video_url
                    ext = ".mp4"
                else:
                    url = node.display_url
                    ext = ".jpg"
                path = WORK_DIR / f"insta_{shortcode}_{i}{ext}"
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    resp.raise_for_status()
                    path.write_bytes(resp.content)
                downloaded_files.append((path, ext, node.is_video))

        elif post.is_video:
            url = post.video_url
            ext = ".mp4"
            path = WORK_DIR / f"insta_{shortcode}{ext}"
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                path.write_bytes(resp.content)
            downloaded_files.append((path, ext, True))

        else:
            url = post.display_url
            ext = ".jpg"
            path = WORK_DIR / f"insta_{shortcode}{ext}"
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                path.write_bytes(resp.content)
            downloaded_files.append((path, ext, False))

        if not downloaded_files:
            await status_msg.edit_text("❌ فایلی برای دانلود پیدا نشد.", reply_markup=back_keyboard())
            return

        caption = f"📥 اینستاگرام | @{post.owner_username}" if post.owner_username else "📥 اینستاگرام"

        for i, (fpath, fext, is_video) in enumerate(downloaded_files):
            if fpath.stat().st_size > 50 * 1024 * 1024:
                await update.message.reply_text(f"⚠️ فایل {i+1} بیشتر از ۵۰ مگابایت است و قابل ارسال نیست.")
                continue
            with open(fpath, "rb") as f:
                if is_video:
                    await update.message.reply_video(video=f, caption=caption if i == 0 else None)
                else:
                    await update.message.reply_photo(photo=f, caption=caption if i == 0 else None)

        await status_msg.delete()
        # Stay in insta_url state — user can send more links
        context.user_data["state"] = "insta_url"
        await update.message.reply_text(
            "✅ دانلود انجام شد!\n\nلینک بعدی را بفرستید یا برگردید:",
            reply_markup=reply_keyboard([["◀️ بازگشت به منوی اصلی"]]),
        )

    except instaloader.exceptions.InstaloaderException as exc:
        logger.exception("Instaloader error")
        await status_msg.edit_text(f"❌ خطا در دانلود:\n`{str(exc)[:400]}`", parse_mode="Markdown",
                                   reply_markup=reply_keyboard([["◀️ بازگشت به منوی اصلی"]]))
    except Exception as exc:
        logger.exception("Instagram download error")
        await status_msg.edit_text(f"❌ خطای غیرمنتظره:\n`{str(exc)[:400]}`", parse_mode="Markdown",
                                   reply_markup=reply_keyboard([["◀️ بازگشت به منوی اصلی"]]))
    finally:
        for fpath, _, _ in downloaded_files:
            try:
                fpath.unlink()
            except OSError:
                pass



# ---------------------------------------------------------------------------
# Feature 6 — Text to Speech (same Microsoft Edge TTS engine used by
# text-to-speech.online)
# ---------------------------------------------------------------------------

async def _get_tts_voices(locale_prefix=None):
    """Return Edge TTS voices filtered by the detected language."""
    try:
        import edge_tts
    except ImportError:
        return []

    voices = await edge_tts.list_voices()

    if locale_prefix:
        prefix = locale_prefix.lower()
        filtered = [v for v in voices
                    if v.get("Locale", "").lower().startswith(prefix)]
        if filtered:
            voices = filtered

    # Andrew - US is the first English option.
    def voice_key(v):
        short = v.get("ShortName", "")
        locale = v.get("Locale", "").lower()
        andrew_us = (
            short in ("en-US-AndrewNeural", "en-US-AndrewMultilingualNeural")
            or (locale == "en-us" and "andrew" in short.lower())
        )
        return (0 if andrew_us else 1, locale, short)

    return sorted(voices, key=voice_key)


def _detect_tts_locale(text: str) -> str | None:
    """Detect the script/language family for selecting relevant TTS voices."""
    if re.search(r"[\u0600-\u06FF]", text):
        return "fa"
    if re.search(r"[A-Za-z]", text):
        return "en"
    if re.search(r"[\u3040-\u30FF]", text):
        return "ja"
    if re.search(r"[\u4E00-\u9FFF]", text):
        return "zh"
    if re.search(r"[\uAC00-\uD7AF]", text):
        return "ko"
    if re.search(r"[\u0400-\u04FF]", text):
        return "ru"
    if re.search(r"[\u0370-\u03FF]", text):
        return "el"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"
    return None


async def _do_tts_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == "◀️ بازگشت به منوی اصلی":
        await handle_menu_text(update, context)
        return
    if not text:
        await update.message.reply_text("❌ متن نمی‌تواند خالی باشد.")
        return
    if len(text) > 10000:
        await update.message.reply_text("❌ متن خیلی طولانی است. لطفاً متن را تا ۱۰هزار کاراکتر بفرست.")
        return

    try:
        detected_locale = _detect_tts_locale(text)

        context.user_data["tts_locale"] = detected_locale

        voices = await _get_tts_voices(detected_locale)
    except ImportError:
        await update.message.reply_text(
            "❌ قابلیت متن به صدا نصب نشده است. پکیج `edge-tts` را نصب کنید.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        context.user_data.pop("state", None)
        return
    except Exception as exc:
        logger.exception("TTS voice list error")
        await update.message.reply_text(f"❌ دریافت لیست صداها ناموفق بود:\n{str(exc)[:400]}", reply_markup=main_menu_keyboard())
        context.user_data.pop("state", None)
        return

    context.user_data["tts_text"] = text
    context.user_data["tts_voices"] = voices
    context.user_data["tts_voice_page"] = 0
    context.user_data["state"] = "tts_voice"
    await _send_tts_voice_page(update.message, context, 0)


async def _send_tts_voice_page(message, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    voices = context.user_data.get("tts_voices", [])
    page_size = 20
    total_pages = max(1, (len(voices) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    chunk = voices[start:start + page_size]

    labels = {}
    buttons = []
    for idx, voice in enumerate(chunk, start=start):
        short = voice.get("ShortName", "")
        locale = voice.get("Locale", "")
        gender = voice.get("Gender", "")
        # Friendly names on text-to-speech.online are essentially "Name - US".
        display_name = short
        parts = short.split("-")
        if len(parts) >= 3:
            display_name = f"{parts[2].replace('Neural','')} - {parts[1]}"
        if "Andrew" in short:
            display_name = f"Andrew - {parts[1] if len(parts) >= 2 else 'US'} ⭐"
        label = f"🔊 {display_name} ({gender})"
        label = label[:64]
        labels[label] = idx
        buttons.append([label])

    nav = []
    if page > 0:
        nav.append("⬅️ قبلی")
    if page < total_pages - 1:
        nav.append("➡️ بعدی")
    if nav:
        buttons.append(nav)
    buttons.append(["◀️ بازگشت به منوی اصلی"])

    context.user_data["tts_labels"] = labels
    context.user_data["tts_nav"] = {"⬅️ قبلی": page - 1, "➡️ بعدی": page + 1}
    await message.reply_text(
        f"🔊 یک صدا را انتخاب کن:\nصفحه {page + 1} از {total_pages}",
        reply_markup=reply_keyboard(buttons),
    )


async def _do_tts_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text == "◀️ بازگشت به منوی اصلی":
        context.user_data.clear()
        await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
        return

    if text in ("⬅️ قبلی", "➡️ بعدی"):
        page = context.user_data.get("tts_nav", {}).get(text)
        if page is not None:
            await _send_tts_voice_page(update.message, context, page)
        return

    index = context.user_data.get("tts_labels", {}).get(text)
    voices = context.user_data.get("tts_voices", [])
    content = context.user_data.get("tts_text", "")
    if index is None or index >= len(voices) or not content:
        await update.message.reply_text("❌ یکی از صداهای نمایش‌داده‌شده را انتخاب کنید.")
        return

    voice = voices[index]
    voice_name = voice.get("ShortName")
    if not voice_name:
        await update.message.reply_text("❌ نام صدا معتبر نیست.")
        return

    status = await update.message.reply_text(f"⏳ در حال ساخت صدا با {voice_name}...")
    out_path = WORK_DIR / f"tts_{update.effective_user.id}_{update.message.message_id}.mp3"
    try:
        import edge_tts
        communicate = edge_tts.Communicate(content, voice_name)
        await communicate.save(str(out_path))
        with open(out_path, "rb") as f:
            await update.message.reply_audio(
                audio=f,
                filename="speech.mp3",
                title=f"TTS - {voice_name}",
                caption=f"🔊 صدا آماده شد\n🎙️ {voice_name}",
                reply_markup=main_menu_keyboard(),
            )
        await status.delete()
    except ImportError:
        await status.edit_text("❌ پکیج `edge-tts` نصب نیست.", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    except Exception as exc:
        logger.exception("TTS synthesis error")
        await status.edit_text(f"❌ خطا در ساخت صدا:\n{str(exc)[:500]}", reply_markup=main_menu_keyboard())
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass
        context.user_data.pop("state", None)
        context.user_data.pop("tts_text", None)
        context.user_data.pop("tts_voices", None)
        context.user_data.pop("tts_labels", None)
        context.user_data.pop("tts_nav", None)


# ---------------------------------------------------------------------------
# /done + fallback
# ---------------------------------------------------------------------------

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("state") == "v2ray_add":
        await handle_v2ray_config(update, context)
    else:
        await update.message.reply_text("❓ دستور /done در این بخش کاربردی ندارد.")


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("state")
    if text == "✅ پایان" and state == "image_to_pdf":
        await _finish_images_to_pdf(update, context)
        return
    if state == "mod_query":
        await handle_mod_text(update, context); return
    if state == "barcode_text":
        await handle_mod_text(update, context); return
    if state == "text_to_txt":
        await handle_mod_text(update, context); return
    if state == "v2ray_add":
        await handle_v2ray_config(update, context); return
    if state == "tts_text":
        await _do_tts_text(update, context); return
    if state == "tts_voice":
        await _do_tts_voice(update, context); return
    if update.message and update.message.photo:
        await update.message.reply_text("❌ تصویر دریافت شد. برای تبدیل تصویر، آن را به صورت document ارسال کنید.", reply_markup=main_menu_keyboard())
        return
    if update.message:
        await update.message.reply_text("❓ متوجه نشدم. از منوی زیر استفاده کنید یا /help را بزنید.", reply_markup=main_menu_keyboard())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception: %s", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ خطای داخلی ربات. لطفاً دوباره تلاش کنید.", reply_markup=main_menu_keyboard())
        except Exception:
            pass

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("done", cmd_done))

    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^(menu_|v2ray_).*"))
    app.add_handler(CallbackQueryHandler(mod_pick_callback, pattern=r"^modpick:"))
    app.add_handler(CallbackQueryHandler(mod_minor_callback, pattern=r"^modminor:"))
    app.add_handler(CallbackQueryHandler(mod_page_callback, pattern=r"^modpage:"))
    app.add_handler(CallbackQueryHandler(mod_families_callback, pattern=r"^modfamilies$"))
    app.add_handler(CallbackQueryHandler(mod_ver_callback, pattern=r"^modver:"))
    app.add_handler(CallbackQueryHandler(convert_callback, pattern=r"^convert:"))

    app.add_handler(MessageHandler(filters.VIDEO | (filters.Document.ALL & filters.Document.MimeType("video/")), handle_video))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mod_text))
    app.add_handler(MessageHandler(filters.ALL, unknown_message))
    app.add_error_handler(error_handler)

    logger.info("ToolBot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
