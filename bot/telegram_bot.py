"""
ربات تلگرامِ پروتکل تشخیص دوگانه — نسخه‌ی مستقل (بخش اصلیِ ربات).

معماری:
  • بخش اصلی (این مخزن): رابط، منوی دکمه‌ای، پایش خطر، نکته‌های آموزشی،
    متن کامل پروتکل و جست‌وجوی آفلاین در آن.
  • بخش هوش مصنوعی (مخزن dual-diagnosis-rag + ورکر کلودفلر): پاسخ‌های
    گفت‌وگویی تولیدشده از مقالات و راهنماها از طریق AI_API_URL.

اجرا (بدون هیچ وابستگی سنگین — فقط python-telegram-bot):
    pip install -r bot/requirements.txt
    TELEGRAM_BOT_TOKEN=... python bot/telegram_bot.py
    # یا توکن را در فایل .env کنار مخزن بگذارید (گیت‌نادیده است)

حریم خصوصی: متن پیام‌های کاربران لاگ نمی‌شود؛ ارزیابی خطر بدون نام بیمار است.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("protocol-bot")


def _load_dotenv(path: str = os.path.join(os.path.dirname(__file__), "..", ".env")) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
AI_API_URL = os.getenv("AI_API_URL", "https://dual-diagnosis-clinical-hub.elasa2next.workers.dev/api/chat")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
CHANNEL_POST_COOLDOWN_S = float(os.getenv("CHANNEL_POST_COOLDOWN_S", "600"))
COOLDOWN_S = float(os.getenv("TELEGRAM_COOLDOWN_S", "4"))

if not TELEGRAM_BOT_TOKEN:
    raise SystemExit(
        "TELEGRAM_BOT_TOKEN تنظیم نشده است.\n"
        "آن را در فایل .env کنار مخزن بگذارید یا به‌صورت متغیر محیطی بدهید."
    )

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand

try:
    from caregiver_quiz import CAREGIVER_MODULES, SITE_ACADEMY_URL
except ImportError:  # اجرا/ایمپورت از ریشه‌ی مخزن
    from bot.caregiver_quiz import CAREGIVER_MODULES, SITE_ACADEMY_URL
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters,
    PicklePersistence,
)

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DISCLAIMER = "⚠️ این پاسخ آموزشی/اطلاعاتی است و جایگزین مشاوره پزشک نیست."

# ============================================================
# ۱) موتور پایش خطر (همان موتور مخزن هوش مصنوعی — وزن‌های یکسان)
# ============================================================
RISK_WEIGHTS = {"psychosis": 3, "suicide": 4, "violence": 3, "withdrawal": 3,
                "substance_use": 2, "sleep_loss": 2, "nonadherence": 2}
RED_FLAGS = {"suicidal_plan", "severe_withdrawal", "violent_intent", "catatonia", "delirium"}
RISK_FIELDS = [
    ("psychosis", "روان‌پریشی"),
    ("suicide", "خودکشی (افکار/برنامه)"),
    ("violence", "خشونت"),
    ("withdrawal", "علائم ترک ماده"),
    ("substance_use", "مصرف ماده"),
    ("sleep_loss", "کم‌خوابی"),
    ("nonadherence", "عدم پایبندی به درمان"),
]


def assess_risk(data: dict) -> dict:
    x = {k: max(0, min(4, int(data.get(k, 0)))) for k in RISK_WEIGHTS}
    flags = set(data.get("flags", []))
    score = sum(x[k] * w for k, w in RISK_WEIGHTS.items())
    immediate = bool(flags & RED_FLAGS) or x["suicide"] == 4 or x["withdrawal"] == 4
    if immediate:
        level, action = "بحرانی", "ارزیابی فوری حضوری/اورژانس و تماس با تیم درمان؛ بیمار تنها نماند."
    elif score >= 45:
        level, action = "خیلی بالا", "بازبینی همان‌روز توسط روان‌پزشک و به‌روزرسانی طرح ایمنی/درمان."
    elif score >= 28:
        level, action = "بالا", "تماس با تیم درمان طی ۲۴ ساعت و افزایش دفعات پایش."
    elif score >= 14:
        level, action = "متوسط", "بازبینی بالینی طی ۷۲ ساعت و مقایسه با خط پایه."
    else:
        level, action = "پایین", "ادامه پایش برنامه‌ریزی‌شده؛ هر تغییر ناگهانی را گزارش کنید."
    return {"score": score, "level": level, "action": action,
            "red_flags": sorted(flags & RED_FLAGS), "inputs": x}


# ============================================================
# ۲) نکته‌های آموزشی (برگرفته از پروتکل و راهنماها)
# ============================================================
TIPS = [
    ("درمان یکپارچه، نه موازی", "در تشخیص دوگانه، درمان همزمان سایکوز و مصرف مواد بهتر از درمان موازی یا متوالی است؛ محروم‌کردن بیمار از خدمات به‌خاطر «اعتیاد» خلاف توصیه NICE است.", "پروتکل · NICE CG120"),
    ("حشیش و خطر سایکوز", "مصرف حشیش در نوجوانی خطر سایکوز در بزرگسالی را تا حدود ۴ برابر افزایش می‌دهد؛ اثر دوز-واکنش مستند است.", "پروتکل · یافته‌های ۲۰۲۳-۲۰۲۴"),
    ("خواب، سنگ‌بنای ثبات", "کم‌خوابی هم علامت هشدار عود است هم عامل تشدیدکننده؛ حفظ نظم خواب و غربالگری مصرف مواد در تشدید علائم اولین گام‌های ارزیابی‌اند.", "پروتکل · ارزیابی تشدید"),
    ("کلوزاپین و پایش", "کلوزاپین در اسکیزوفرنی مقاوم به درمان و کاهش مصرف الکل همراه با سایکوز قوی‌ترین سیگنال را دارد؛ پایش منظم خون و عوارض متابولیک الزامی است.", "پروتکل · APA 2020"),
    ("لغزش = سیگنال، نه شکست", "لغزش مصرف نشانه‌ی نیاز به بازبینی درمان و حمایت بیشتر است، نه دلیل قطع درمان یا سرزنش.", "پروتکل · کاهش آسیب"),
    ("علامت‌های هشدار عود", "بی‌خوابی، انزوا، بدگمانی فزاینده، بی‌قراری، قطع خودسرانه دارو یا برگشت به مصرف؛ دیدن چند علامت همزمان یعنی وقت تماس با تیم درمان.", "پروتکل · پیشگیری از عود"),
    ("پایبندی به دارو", "قطع خودسرانه دارو از قوی‌ترین پیش‌بین‌های عود است؛ درباره عارضه با پزشک صحبت کنید، نه قطع ناگهانی.", "پروتکل · APA 2020"),
    ("DBT برای BPD", "در اختلال شخصیت مرزی، روان‌درمانی ساختاریافته (DBT) محور درمان است؛ دارو برای هسته‌ی اختلال درمان اختصاصی ندارد.", "پروتکل · NICE CG78"),
    ("خانواده چه کند؟", "گوش‌دادن بدون قضاوت، اعتباربخشی هیجانی، بحث‌نکردن مستقیم با تجربه‌های روان‌پریشانه و تمرکز بر ایمنی؛ آموزش خانواده نرخ عود را کم می‌کند.", "پروتکل · همراه (Caregiver)"),
    ("مصاحبه انگیزشی، نه موعظه", "برای کاهش مصرف، گفت‌وگوی انگیزشی مؤثرتر از رویارویی است: پذیرش بی‌قضاوت و تقویت تغییر از خودِ فرد.", "پروتکل · مداخلات روانی"),
    ("خطر فوری؟ این کارها نکن", "در طرح یا قصد خودکشی فرد را تنها نگذارید، با توهم بحث نکنید و دارو را خودسرانه تغییر ندهید: اورژانس ۱۱۵ · اورژانس اجتماعی ۱۲۳.", "پروتکل · برنامه اجرایی فوری"),
    ("ترک شدید مواد", "ترک شدید با دلیریوم، تشنج یا بی‌ثباتی علائم حیاتی یک اورژانس پزشکی است؛ نه فقط «تحمل کردن».", "پروتکل · ارزیابی ترک"),
    ("مراقبت از خودِ همراه", "مراقبِ بیمار هم نیاز به مراقبت دارد: خواب خودتان، شبکه حمایتی و مرزهای واضح.", "پروتکل · همراه (Caregiver)"),
    ("شواهد تازه", "مقالات جدید هر هفته با PubMed رصد می‌شوند و پایگاه دانش دستیار به‌روز می‌ماند.", "دستیار هوش مصنوعی"),
]


def tip_of_day(offset: int = 0) -> str:
    day = time.localtime().tm_yday + offset
    title, body, source = TIPS[day % len(TIPS)]
    return f"🎓 *آموزش روز | {title}*\n\n{body}\n\n📖 منبع: {source}\n{DISCLAIMER}"


# ============================================================
# ۳) پروتکل: بخش‌ها + جست‌وجوی آفلاین (بدون مدل زبانی)
# ============================================================
def load_sections() -> list[dict]:
    """بخش‌های پروتکل را از bot/protocol.md می‌خواند."""
    with open(os.path.join(BOT_DIR, "protocol.md"), encoding="utf-8") as f:
        text = f.read()
    sections, cur_title, buf = [], "پروتکل درمان تشخیص دوگانه", []
    for line in text.splitlines():
        if re.match(r"^#{1,2}\s", line):
            if buf:
                sections.append({"title": cur_title, "text": "\n".join(buf).strip()})
            cur_title, buf = re.sub(r"^#+\s*", "", line).strip(), []
        else:
            buf.append(line)
    if buf:
        sections.append({"title": cur_title, "text": "\n".join(buf).strip()})
    return [s for s in sections if s["text"]]


SECTIONS = load_sections()

_STOPWORDS = {"و", "در", "به", "از", "که", "را", "با", "این", "برای", "است", "می", "های", "یا", "تا", "هم",
              "چیست", "چه", "چگونه", "چطور", "کند", "شود", "هستند", "دارد", "باشد", "بود", "نیست",
              "کنم", "کنید", "باید", "می‌شود", "میشود", "هایی", "ها", "اند", "داده", "دادن", "بگیرد"}


def _tokens(text: str) -> list[str]:
    return re.split(r"[^\u0600-\u06FF\w]+", text.lower())


_TOK_CACHE: dict = {}


def search_protocol(query: str, max_len: int = 900) -> dict | None:
    """جست‌وجوی کلیدواژه‌ای ساده روی بخش‌های پروتکل (حالت آفلاین/بدون AI).

    تطبیق «توکن کامل» است (نه زیررشته) تا واژه‌های کوتاه به‌اشتباه داخل واژه‌های
    دیگر (مثل «بیت» در «نسبت») تطبیق نخورند. امتیاز = فراوانی توکن در متن
    + پاداش بزرگ برای حضور واژه در عنوان بخش. پاسخ، پنجره‌ای حول اولین تطابق است.
    """
    from collections import Counter
    words = {w for w in _tokens(query) if len(w) > 2 and w not in _STOPWORDS}
    if not words:
        return None
    best, best_score, best_pos = None, 0, 0
    for sec in SECTIONS:
        key = id(sec)
        if key not in _TOK_CACHE:
            _TOK_CACHE[key] = (Counter(_tokens(sec["text"])), set(_tokens(sec["title"])))
        toks, title_toks = _TOK_CACHE[key]
        score = sum(toks[w] for w in words if w in toks)
        score += 10 * sum(1 for w in words if w in title_toks)
        if score > best_score:
            best, best_score = sec, score
            best_pos = min((sec["text"].lower().find(w) for w in words
                            if w in toks or w in title_toks), default=0)
    if not best or best_score < 2:
        return None
    text = best["text"]
    start = max(0, best_pos - 150)
    excerpt = text[start:start + max_len]
    if start > 0:
        excerpt = "…" + excerpt
    if start + max_len < len(text):
        excerpt += "…"
    return {"title": best["title"], "text": excerpt.strip(), "matched": best_score}


# ============================================================
# ۴) بخش هوش مصنوعی: تماس با ورکر کلودفلر (مخزن dual-diagnosis-rag)
# ============================================================
def ask_ai(question: str, role: str = "patient") -> dict | None:
    """پرسش را به دستیار هوش مصنوعی (ورکر کلودفلر) می‌فرستد."""
    body = json.dumps({"question": question, "role": role}).encode("utf-8")
    req = urllib.request.Request(
        AI_API_URL, data=body, method="POST",
        headers={"content-type": "application/json", "user-agent": "dual-diagnosis-protocol-bot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode("utf-8"))
    if "answer" in data:
        return data
    return None


# ============================================================
# رابط تلگرام
# ============================================================
MAX_LEN = 3800

BTN_RISK = "📈 پایش خطر"
BTN_TIP = "🎓 نکته امروز"
BTN_SECTIONS = "📖 بخش‌های پروتکل"
BTN_HELP = "❓ راهنما"
BTN_TRAINING = "🎓 آموزش همراه"
BTN_ROLE = "👤 نقش من"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_RISK, BTN_TIP], [BTN_SECTIONS, BTN_ROLE], [BTN_TRAINING, BTN_HELP]],
    resize_keyboard=True,
    input_field_placeholder="سؤال خود را فارسی بنویسید…",
)

WELCOME = (
    "سلام! 👋\n\n"
    "من ربات *پروتکل درمان تشخیص دوگانه* هستم\n"
    "(سایکوز / اسکیزوفرنی + اختلال مصرف مواد + BPD ± ADHD).\n\n"
    "🏛 *بخش اصلی:* پروتکل کامل درمان، پایش خطر و آموزش — در همین ربات\n"
    "🧠 *بخش هوش مصنوعی:* پاسخ‌های مقاله‌محور از دستیار کلودفلر\n\n"
    "👤 با دکمه‌ی «نقش من» مشخص کنید *بیمار* هستید، *همراه خانواده* یا *متخصص* "
    "تا پاسخ‌ها متناسب شود.\n\n"
    "سؤال‌تان را بنویسید یا از دکمه‌های پایین صفحه استفاده کنید 👇\n\n"
    "⚠️ _من جایگزین پزشک نیستم؛ در وضعیت اورژانسی فوراً با خدمات درمانی تماس بگیرید._"
)

HELP_TEXT = (
    "📖 *راهنما*\n\n"
    "• پیام آزاد → پاسخ هوشمند (فارسی، مستند به شواهد)\n"
    f"• {BTN_RISK} / /risk → ارزیابی خطر ۷ شاخصه (۰ تا ۴)\n"
    f"• {BTN_TIP} / /tip → نکته‌ی آموزشی امروز\n"
    f"• {BTN_SECTIONS} → مرور بخش‌های پروتکل درمان\n"
    f"• {BTN_ROLE} / /role → انتخاب نقش (بیمار / همراه / متخصص)\n"
    f"• {BTN_TRAINING} / /training → دوره‌ی آموزش همراه با آزمون\n"
    "• /history → روند امتیازهای پایش خطر شما\n"
    "• /about → درباره‌ی معماری ربات و منابع\n"
    "• /cancel → لغو ارزیابی در جریان\n\n"
    "🔒 حریم خصوصی: متن پیام‌های شما ذخیره نمی‌شود؛ از ارزیابی خطر فقط امتیاز و تاریخ "
    "(بدون نام و بدون متن) برای نمایش روند نگه داشته می‌شود."
)

ABOUT_TEXT = (
    "🤖 *درباره‌ی این ربات*\n\n"
    "این ربات از دو بخش تشکیل شده است:\n\n"
    "۱) *بخش اصلی — پروتکل درمان* (این مخزن)\n"
    "پروتکل کامل بالینی، پایش خطر شفاف، آموزش‌های روزانه و جست‌وجوی آفلاین.\n\n"
    "۲) *بخش هوش مصنوعی — dual-diagnosis-rag*\n"
    "بازیابی معنایی روی ۲۰۰+ مقاله و راهنماها + پاسخ‌گویی مدل زبانی (ورکر کلودفلر).\n\n"
    "📚 منابع: NICE · APA · WFSBP · WHO · UNODC · وزارت بهداشت ایران\n"
    "⚕️ سایکوز / اسکیزوفرنی + اختلال مصرف مواد + BPD ± ADHD\n\n"
    "⚠️ جایگزین مشاوره پزشک نیست."
)


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur.rstrip("\n"))
            cur = line + "\n"
        else:
            cur += line + "\n"
    if cur.strip():
        parts.append(cur.rstrip("\n"))
    return parts


async def send_long(update: Update, text: str, **kw):
    for part in split_message(text):
        await update.effective_message.reply_text(part, **kw)


def throttled(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    now = time.monotonic()
    last = context.user_data.get("_last_ts", 0.0)
    if now - last < COOLDOWN_S:
        return True
    context.user_data["_last_ts"] = now
    return False


# ---------- انتشار در کانال ----------
_channel_last_post = 0.0


def channel_link() -> str:
    """لینک عمومی کانال پیکربندی‌شده (اگر نبود، رشته‌ی خالی)."""
    if not TELEGRAM_CHANNEL_ID:
        return ""
    if TELEGRAM_CHANNEL_ID.startswith("@"):
        return f"https://t.me/{TELEGRAM_CHANNEL_ID[1:]}"
    return f"https://t.me/c/{str(TELEGRAM_CHANNEL_ID).lstrip('-').replace('-100', '', 1)}"


async def post_to_channel(bot, text: str) -> None:
    """متن را به کانال پیکربندی‌شده می‌فرستد (با احترام به سقف طول پیام)."""
    if not TELEGRAM_CHANNEL_ID:
        raise RuntimeError("TELEGRAM_CHANNEL_ID تنظیم نشده است.")
    for part in split_message(text):
        try:
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=part, parse_mode="Markdown")
        except Exception:
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=part)


async def job_daily_tip(context: ContextTypes.DEFAULT_TYPE) -> None:
    """پست آموزشی روزانه به کانال (هر روز ساعت ۹ صبح به وقت تهران)."""
    try:
        await post_to_channel(context.bot, tip_of_day())
        log.info("پست آموزشی روزانه به کانال ارسال شد.")
    except Exception as e:
        log.warning("ارسال پست آموزشی به کانال ناموفق: %s", e)


async def cmd_post_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال فوری نکته‌ی امروز به کانال (با خنک‌سازی ضد اسپم)."""
    global _channel_last_post
    if not TELEGRAM_CHANNEL_ID:
        await send_long(update, "کانالی پیکربندی نشده است (TELEGRAM_CHANNEL_ID در .env).")
        return
    now = time.monotonic()
    if now - _channel_last_post < CHANNEL_POST_COOLDOWN_S:
        await send_long(update, "⏳ همین حالا پستی ارسال شده است؛ کمی بعد دوباره تلاش کنید.")
        return
    try:
        await post_to_channel(context.bot, tip_of_day())
        _channel_last_post = now
        await send_long(update, "✅ نکته‌ی امروز به کانال ارسال شد.")
    except Exception as e:
        await send_long(update, f"❌ ارسال به کانال ناموفق بود: {e}")


async def cmd_channel_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بررسی اتصال و وضعیت کانال."""
    if not TELEGRAM_CHANNEL_ID:
        await send_long(update, "کانالی پیکربندی نشده است. TELEGRAM_CHANNEL_ID را در .env بگذارید.")
        return
    try:
        chat = await context.bot.get_chat(TELEGRAM_CHANNEL_ID)
        link = f"https://t.me/{chat.username}" if getattr(chat, "username", None) else (
            chat.invite_link or "-")
        await send_long(
            update,
            f"📢 کانال: {chat.title}\n"
            f"🔗 لینک: {link}\n"
            f"✅ ربات به کانال متصل است و هر روز ساعت ۹ (به وقت تهران) نکته‌ی آموزشی منتشر می‌کند.\n"
            f"برای ارسال فوری: /post_tip",
        )
    except Exception as e:
        await send_long(update, f"❌ دسترسی به کانال ناموفق: {e}")


# ---------- دستورها ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = WELCOME + (f"\n\n📢 کانال اخبار و آموزش روزانه: {channel_link()}" if TELEGRAM_CHANNEL_ID else "")
    await update.effective_message.reply_text(text, parse_mode="Markdown",
                                              reply_markup=MAIN_KEYBOARD)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_TEXT, parse_mode="Markdown",
                                              reply_markup=MAIN_KEYBOARD)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ABOUT_TEXT + (f"\n\n📢 کانال اخبار: {channel_link()}" if TELEGRAM_CHANNEL_ID else "")
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def cmd_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(tip_of_day(), parse_mode="Markdown",
                                              reply_markup=MAIN_KEYBOARD)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.pop("risk", None) is not None:
        await update.effective_message.reply_text("ارزیابی خطر لغو شد. ✅")
    else:
        await update.effective_message.reply_text("ارزیابی در جریانی وجود ندارد.")


# ---------- بخش‌های پروتکل ----------
async def cmd_sections(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [InlineKeyboardButton(s["title"][:40], callback_data=f"sec:{i}")
               for i, s in enumerate(SECTIONS[:24])]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    await update.effective_message.reply_text(
        "📖 بخش‌های پروتکل درمان — کدام بخش را می‌خواهید ببینید؟",
        reply_markup=InlineKeyboardMarkup(rows))


async def on_section_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        idx = int(q.data.split(":", 1)[1])
        sec = SECTIONS[idx]
    except (ValueError, IndexError):
        await q.answer("بخش نامعتبر است.")
        return
    await send_long(update, f"📖 *{sec['title']}*\n\n{sec['text']}\n\n{DISCLAIMER}",
                    parse_mode="Markdown")


# ---------- پرسش و پاسخ ----------
async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str):
    question = (question or "").strip()
    if not question:
        await update.effective_message.reply_text("لطفاً سؤال خود را بنویسید.")
        return
    if throttled(update.effective_user.id, context):
        await update.effective_message.reply_text("⏳ چند لحظه صبر کنید و دوباره بپرسید.")
        return
    typing = asyncio.create_task(_keep_typing(update.effective_chat.id, context))
    try:
        data = await asyncio.to_thread(ask_ai, question, get_role(context))  # بخش هوش مصنوعی
        if data:
            lines = [data["answer"].rstrip(), ""]
            src = data.get("source") or ""
            if src:
                lines.append(f"🧠 منبع پاسخ: {src}")
            lines.append(DISCLAIMER)
            await send_long(update, "\n".join(lines))
            return
    except Exception as e:
        log.warning("دستیار هوش مصنوعی در دسترس نیست (%s)؛ پاسخ از پروتکل محلی.", e)
    # حالت آفلاین/بدون AI: جست‌وجو در متن پروتکل (بخش اصلی)
    hit = await asyncio.to_thread(search_protocol, question)
    if hit:
        await send_long(update, f"📖 *از پروتکل درمان — {hit['title']}*\n\n{hit['text']}\n\n{DISCLAIMER}",
                        parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(
            "در حال حاضر پاسخی پیدا نشد. لطفاً سؤال را دقیق‌تر بپرسید یا بعداً تلاش کنید.\n" + DISCLAIMER)


async def _keep_typing(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        while True:
            await context.bot.send_chat_action(chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if text == BTN_RISK:
        return await cmd_risk(update, context)
    if text == BTN_TIP:
        return await cmd_tip(update, context)
    if text == BTN_SECTIONS:
        return await cmd_sections(update, context)
    if text == BTN_ROLE:
        return await cmd_role(update, context)
    if text == BTN_TRAINING:
        return await cmd_training(update, context)
    if text == BTN_HELP:
        return await cmd_help(update, context)
    await answer_question(update, context, text)


# ---------- آموزش همراه (دوره + آزمون) ----------
def _academy_progress(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("academy", {})


async def cmd_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prog = _academy_progress(context)
    done = sum(1 for m in CAREGIVER_MODULES if m["id"] in prog)
    rows = [[InlineKeyboardButton(("✅ " if m["id"] in prog else "▫️ ") + m["title"][:38],
                                  callback_data=f"train:{m['id']}")]
            for m in CAREGIVER_MODULES]
    await update.effective_message.reply_text(
        "🎓 *دوره‌ی آموزش همراه*\n\n"
        f"پیشرفت شما: {done} از {len(CAREGIVER_MODULES)} ماژول\n\n"
        "هر ماژول یک منبع آموزشی (ویدئو یا متن علمی) و یک آزمون ۳ پرسشی دارد؛ "
        "برای قبولی باید هر ۳ پرسش را درست پاسخ دهید.\n"
        "پس از تکمیل همه‌ی ماژول‌ها، گواهی آموزش همراه با کد اعتبارسنجی از سایت مرکز صادر می‌شود.",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")


async def _ask_training_question(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 module: dict, first: bool = False):
    st = context.user_data.get("training") or {}
    qi = st.get("qi", 0)
    quiz = module["quiz"]
    if qi >= len(quiz):
        return await _finish_training(update, context, module)
    question = quiz[qi]
    head = ""
    if first:
        head = (f"🎓 *{module['title']}*\n"
                f"📚 منبع: {module['source']} ({module['duration']})\n"
                f"{module['summary']}\n"
                f"🔗 {module['url']}\n\n")
    msg = await update.effective_message.reply_text(
        head + f"❓ *پرسش {qi + 1} از {len(quiz)}*\n\n{question['q']}",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(o[:60], callback_data=f"tq:{module['id']}:{qi}:{i}")]
             for i, o in enumerate(question["o"])]))
    st["qmsg"] = msg.message_id


async def on_train_module(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    mid = (q.data or "").split(":", 1)[1]
    module = next((m for m in CAREGIVER_MODULES if m["id"] == mid), None)
    if module is None:
        await q.answer("ماژول یافت نشد.")
        return
    context.user_data["training"] = {"mid": mid, "qi": 0, "answers": []}
    await q.answer()
    await _ask_training_question(update, context, module, first=True)


async def on_train_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    st = context.user_data.get("training")
    if not st:
        await q.answer("جلسه‌ی آموزشی فعال نیست؛ /training را بزنید.", show_alert=True)
        return
    if not q.message or st.get("qmsg") != q.message.message_id:
        await q.answer("به آخرین پرسش پاسخ دهید.")
        return
    try:
        _, mid, qi, oi = (q.data or "").split(":")
        qi, oi = int(qi), int(oi)
    except ValueError:
        await q.answer()
        return
    if qi != st.get("qi") or mid != st.get("mid"):
        await q.answer("این پرسش قدیمی است.")
        return
    module = next((m for m in CAREGIVER_MODULES if m["id"] == mid), None)
    if module is None:
        await q.answer("ماژول یافت نشد.")
        return
    st["answers"].append(oi)
    st["qi"] = qi + 1
    await q.answer()
    await _ask_training_question(update, context, module)


async def _finish_training(update: Update, context: ContextTypes.DEFAULT_TYPE, module: dict):
    answers = context.user_data.pop("training", {}).get("answers", [])
    quiz = module["quiz"]
    ok = sum(1 for i, a in enumerate(answers) if i < len(quiz) and a == quiz[i]["a"])
    total = len(quiz)
    score = round(100 * ok / total) if total else 0
    if score >= 100:
        prog = _academy_progress(context)
        prog[module["id"]] = score
        done = sum(1 for m in CAREGIVER_MODULES if m["id"] in prog)
        text = (f"✅ *ماژول «{module['title']}» تکمیل شد!*\n\n"
                f"نمره: {score} از ۱۰۰ ({ok} از {total} درست)\n"
                f"پیشرفت دوره: {done} از {len(CAREGIVER_MODULES)} ماژول")
        if done == len(CAREGIVER_MODULES):
            text += ("\n\n🎉 تبریک! همه‌ی ماژول‌های *دوره‌ی آموزش همراه* را گذراندید.\n"
                     "برای دریافت گواهی با کد اعتبارسنجی، از بخش آکادمی سایت مرکز اقدام کنید:\n"
                     + SITE_ACADEMY_URL)
        await send_long(update, text, reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(
            f"📚 نمره: {ok} از {total} درست — برای قبولی باید هر ۳ پرسش درست باشد.\n"
            "منبع را دوباره مرور کنید و ماژول را تکرار کنید:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 تلاش دوباره", callback_data=f"train:{module['id']}")]]))


# ---------- نقش کاربر (بیمار / همراه / متخصص) ----------
ROLE_OPTIONS = {
    "patient": ("🧍 بیمار", "پاسخ‌ها ساده، امیدبخش و بدون جزئیات دوز دارو"),
    "family": ("👨‍👩‍👦 همراه / خانواده", "پاسخ‌ها روی حمایت عملی، علائم هشدار و زمان تماس با پزشک"),
    "doctor": ("🩺 متخصص / درمانگر", "پاسخ‌ها فنی، با محدودیت شواهد و ارجاع به مقالات"),
}


def get_role(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("role", "patient")


async def cmd_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = get_role(context)
    rows = [[InlineKeyboardButton(label, callback_data=f"role:{key}")]
            for key, (label, _) in ROLE_OPTIONS.items()]
    await update.effective_message.reply_text(
        "👤 *نقش شما*\n\n"
        f"انتخاب فعلی: {ROLE_OPTIONS[current][0]}\n\n"
        "پاسخ‌های دستیار هوشمند بر اساس نقش شما تنظیم می‌شود:",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")


async def on_role_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    key = (q.data or "").split(":", 1)[-1]
    if key not in ROLE_OPTIONS:
        await q.answer("گزینه نامعتبر است.")
        return
    context.user_data["role"] = key
    label, desc = ROLE_OPTIONS[key]
    await q.answer("ثبت شد ✅")
    if q.message is not None:
        await q.message.reply_text(
            f"✅ نقش شما: *{label}*\n{desc}\n\nاز این پس پاسخ‌ها برای همین نقش تنظیم می‌شود.",
            parse_mode="Markdown")


# ---------- پایش خطر ----------
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["risk"] = {"step": 0, "answers": {}}
    await _ask_risk_step(update, context)


async def _ask_risk_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("risk")
    if state is None:
        await update.effective_message.reply_text("برای شروع /risk را بزنید.")
        return
    step = state["step"]
    if step >= len(RISK_FIELDS):
        return await _finish_risk(update, context)
    key, label = RISK_FIELDS[step]
    msg = await update.effective_message.reply_text(
        f"🩺 پایش خطر — {step + 1} از {len(RISK_FIELDS)}\n\n"
        f"«{label}» الان چقدر شدت دارد؟\n(۰ = ندارد … ۴ = شدید)",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(str(i), callback_data=f"risk:{i}") for i in range(5)]]),
    )
    state["qmsg"] = msg.message_id


async def on_risk_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    state = context.user_data.get("risk")
    if state is None:
        await q.answer("جلسه‌ی ارزیابی منقضی شده؛ دوباره /risk را بزنید.", show_alert=True)
        return
    if q.message is None or state.get("qmsg") != q.message.message_id:
        await q.answer("این پرسش قدیمی است؛ به آخرین پرسش پاسخ دهید.")
        return
    try:
        value = max(0, min(4, int(q.data.split(":", 1)[1])))
    except ValueError:
        return
    await q.answer()
    key, label = RISK_FIELDS[state["step"]]
    state["answers"][key] = value
    state["step"] += 1
    await q.edit_message_text(f"«{label}»: {value} ثبت شد ✅")
    if state["step"] < len(RISK_FIELDS):
        await _ask_risk_step(update, context)
    else:
        await _finish_risk(update, context)


async def _finish_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.pop("risk", None)
    if state is None:
        return
    result = assess_risk(state["answers"])
    # ثبت در سابقه (فقط امتیاز و تاریخ — بدون نام و بدون متن)
    hist = context.user_data.setdefault("risk_history", [])
    prev = hist[-1]["score"] if hist else None
    hist.append({"date": datetime.now().isoformat(timespec="minutes"),
                 "score": result["score"], "level": result["level"]})
    del hist[:-20]  # فقط ۲۰ ارزیابی آخر نگه داشته می‌شود
    delta = ""
    if prev is not None:
        d = result["score"] - prev
        arrow = "⬆️" if d > 0 else ("⬇️" if d < 0 else "➡️")
        delta = f"\n📊 تغییر نسبت به دفعه‌ی قبل: {arrow} {'+' if d > 0 else ''}{d} نقطه\n"
    vals = " | ".join(f"{label}: {result['inputs'].get(key, 0)}" for key, label in RISK_FIELDS)
    await send_long(update, (
        f"🩺 *گزارش پایش خطر*\n\n"
        f"امتیاز: *{result['score']} از ۷۶*\n"
        f"سطح: *{result['level']}*\n{delta}\n"
        f"📌 اقدام پیشنهادی:\n{result['action']}\n\n"
        f"ثبت‌شده‌ها: {vals}\n\n"
        f"📈 روند کامل ارزیابی‌ها: /history\n\n"
        f"⚠️ این خروجی جایگزین ارزیابی پزشک نیست و نباید خودکار دارو را تغییر دهد."
    ), parse_mode="Markdown")


# ---------- سابقه و روند پایش ----------
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(value) -> str:
    """تبدیل ارقام لاتین به فارسی."""
    return str(value).translate(_PERSIAN_DIGITS)


def _gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """تبدیل تاریخ میلادی به شمسی (الگوریتم استاندارد جلالی)."""
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2, gm2, gd2 = gy - 1600, gm - 1, gd - 1
    day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    day_no += g_d_m[gm2]
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)):
        day_no += 1
    day_no += gd2
    j_day_no = day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    if j_day_no < 186:
        jm, jd = 1 + j_day_no // 31, 1 + j_day_no % 31
    else:
        jm, jd = 7 + (j_day_no - 186) // 30, 1 + (j_day_no - 186) % 30
    return jy, jm, jd


def jalali_date(iso: str) -> str:
    """تاریخ میلادی ISO را به شمسی «۱۴۰۵/۰۶/۰۷» تبدیل می‌کند."""
    dt = datetime.fromisoformat(iso)
    jy, jm, jd = _gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{fa_digits(f'{jy:04d}/{jm:02d}/{jd:02d}')}"


def history_report(hist: list) -> str:
    """گزارش متنی روند پایش خطر (تست‌پذیر آفلاین)."""
    if not hist:
        return ("📈 هنوز ارزیابی ثبت‌شده‌ای ندارید.\n\n"
                "برای اولین ثبت، /risk را بزنید — بعد از هر ارزیابی، امتیاز شما اینجا نگه داشته می‌شود.")
    lines = ["📈 *روند پایش خطر شما*", ""]
    prev = None
    for h in hist:
        s = h["score"]
        arrow = ""
        if prev is not None:
            d = s - prev
            arrow = " ⬆️" if d > 0 else (" ⬇️" if d < 0 else " ➡️")
        lines.append(f"• {jalali_date(h['date'])} — {fa_digits(s)} ({h['level']}){arrow}")
        prev = s
    if len(hist) > 1:
        total = hist[-1]["score"] - hist[0]["score"]
        if total < 0:
            lines.append(f"\n✅ روند کلی: کاهش {fa_digits(-total)} نقطه از اولین ارزیابی.")
        elif total > 0:
            lines.append("\n⚠️ امتیازها بالاتر رفته — وضعیت را با پزشک یا تیم درمان در میان بگذارید.")
        else:
            lines.append("\n➡️ روند کلی بدون تغییر.")
    lines.append("\n🔒 فقط امتیاز و تاریخ ذخیره می‌شود (بدون نام و بدون متن پیام‌ها).")
    lines.append("⚠️ این گزارش آموزشی است و جایگزین ارزیابی پزشک نیست.")
    return "\n".join(lines)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_long(update, history_report(context.user_data.get("risk_history") or []),
                    parse_mode="Markdown")


# ---------- راه‌اندازی ----------
async def _post_init(app: Application) -> None:
    commands = [
        BotCommand("start", "شروع و معرفی ربات"),
        BotCommand("help", "راهنما"),
        BotCommand("risk", "پایش خطر ۷ شاخصه"),
        BotCommand("tip", "نکته‌ی آموزشی امروز"),
        BotCommand("sections", "بخش‌های پروتکل درمان"),
        BotCommand("role", "نقش شما: بیمار / همراه / متخصص"),
        BotCommand("training", "دوره‌ی آموزش همراه + آزمون"),
        BotCommand("history", "روند پایش‌های قبلی شما"),
        BotCommand("about", "درباره‌ی ربات و منابع"),
        BotCommand("cancel", "لغو ارزیابی در جریان"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        log.info("منوی دستورها ثبت شد.")
    except Exception as e:
        log.warning("ثبت منوی دستورها ناموفق: %s", e)


def main():
    # ماندگاری داده‌ها (نقش کاربر و سابقه‌ی پایش) بین اجراها — بدون پایگاه‌داده
    data_dir = os.path.join(BOT_DIR, "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    persistence = PicklePersistence(filepath=os.path.join(data_dir, "bot_data.pkl"))
    app = (Application.builder()
           .token(TELEGRAM_BOT_TOKEN)
           .persistence(persistence)
           .post_init(_post_init)
           .build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("tip", cmd_tip))
    app.add_handler(CommandHandler("sections", cmd_sections))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("role", cmd_role))
    app.add_handler(CommandHandler("history", cmd_history))
    if TELEGRAM_CHANNEL_ID:
        app.add_handler(CommandHandler("post_tip", cmd_post_tip))
        app.add_handler(CommandHandler("channel_status", cmd_channel_status))
    app.add_handler(CallbackQueryHandler(on_risk_button, pattern=r"^risk:\d$"))
    app.add_handler(CallbackQueryHandler(on_role_button, pattern=r"^role:(patient|family|doctor)$"))
    app.add_handler(CallbackQueryHandler(on_section_button, pattern=r"^sec:\d+$"))
    app.add_handler(CommandHandler("training", cmd_training))
    app.add_handler(CallbackQueryHandler(on_train_module, pattern=r"^train:[a-z-]+$"))
    app.add_handler(CallbackQueryHandler(on_train_answer, pattern=r"^tq:[a-z-]+:\d+:\d+$"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_message))
    if TELEGRAM_CHANNEL_ID:
        if app.job_queue is not None:
            from datetime import time as dtime
            import zoneinfo
            app.job_queue.run_daily(
                job_daily_tip,
                time=dtime(9, 0, tzinfo=zoneinfo.ZoneInfo("Asia/Tehran")),
                name="daily_tip",
            )
            log.info("پست روزانه‌ی کانال (%s) هر روز ساعت ۹ (تهران) زمان‌بندی شد.", TELEGRAM_CHANNEL_ID)
        else:
            log.warning("job_queue در دسترس نیست (python-telegram-bot[job-queue])؛ پست روزانه فعال نشد.")
    log.info("ربات پروتکل (بخش اصلی) + دستیار هوش مصنوعی (%s) راه‌اندازی می‌شود...", AI_API_URL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
