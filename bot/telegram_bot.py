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
COOLDOWN_S = float(os.getenv("TELEGRAM_COOLDOWN_S", "4"))

if not TELEGRAM_BOT_TOKEN:
    raise SystemExit(
        "TELEGRAM_BOT_TOKEN تنظیم نشده است.\n"
        "آن را در فایل .env کنار مخزن بگذارید یا به‌صورت متغیر محیطی بدهید."
    )

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters,
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

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_RISK, BTN_TIP], [BTN_SECTIONS, BTN_HELP]],
    resize_keyboard=True,
    input_field_placeholder="سؤال خود را فارسی بنویسید…",
)

WELCOME = (
    "سلام! 👋\n\n"
    "من ربات *پروتکل درمان تشخیص دوگانه* هستم\n"
    "(سایکوز / اسکیزوفرنی + اختلال مصرف مواد + BPD ± ADHD).\n\n"
    "🏛 *بخش اصلی:* پروتکل کامل درمان، پایش خطر و آموزش — در همین ربات\n"
    "🧠 *بخش هوش مصنوعی:* پاسخ‌های مقاله‌محور از دستیار کلودفلر\n\n"
    "سؤال‌تان را بنویسید یا از دکمه‌های پایین صفحه استفاده کنید 👇\n\n"
    "⚠️ _من جایگزین پزشک نیستم؛ در وضعیت اورژانسی فوراً با خدمات درمانی تماس بگیرید._"
)

HELP_TEXT = (
    "📖 *راهنما*\n\n"
    "• پیام آزاد → پاسخ هوشمند (فارسی، مستند به شواهد)\n"
    f"• {BTN_RISK} / /risk → ارزیابی خطر ۷ شاخصه (۰ تا ۴)\n"
    f"• {BTN_TIP} / /tip → نکته‌ی آموزشی امروز\n"
    f"• {BTN_SECTIONS} → مرور بخش‌های پروتکل درمان\n"
    "• /about → درباره‌ی معماری ربات و منابع\n"
    "• /cancel → لغو ارزیابی در جریان\n\n"
    "🔒 حریم خصوصی: متن پیام‌های شما ذخیره نمی‌شود؛ ارزیابی خطر بدون نام بیمار است."
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


# ---------- دستورها ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(WELCOME, parse_mode="Markdown",
                                              reply_markup=MAIN_KEYBOARD)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_TEXT, parse_mode="Markdown",
                                              reply_markup=MAIN_KEYBOARD)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(ABOUT_TEXT, parse_mode="Markdown")


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
        data = await asyncio.to_thread(ask_ai, question)          # بخش هوش مصنوعی
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
    if text == BTN_HELP:
        return await cmd_help(update, context)
    await answer_question(update, context, text)


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
    vals = " | ".join(f"{label}: {result['inputs'].get(key, 0)}" for key, label in RISK_FIELDS)
    await send_long(update, (
        f"🩺 *گزارش پایش خطر*\n\n"
        f"امتیاز: *{result['score']} از ۷۶*\n"
        f"سطح: *{result['level']}*\n\n"
        f"📌 اقدام پیشنهادی:\n{result['action']}\n\n"
        f"ثبت‌شده‌ها: {vals}\n\n"
        f"⚠️ این خروجی جایگزین ارزیابی پزشک نیست و نباید خودکار دارو را تغییر دهد."
    ), parse_mode="Markdown")


# ---------- راه‌اندازی ----------
async def _post_init(app: Application) -> None:
    commands = [
        BotCommand("start", "شروع و معرفی ربات"),
        BotCommand("help", "راهنما"),
        BotCommand("risk", "پایش خطر ۷ شاخصه"),
        BotCommand("tip", "نکته‌ی آموزشی امروز"),
        BotCommand("sections", "بخش‌های پروتکل درمان"),
        BotCommand("about", "درباره‌ی ربات و منابع"),
        BotCommand("cancel", "لغو ارزیابی در جریان"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        log.info("منوی دستورها ثبت شد.")
    except Exception as e:
        log.warning("ثبت منوی دستورها ناموفق: %s", e)


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("tip", cmd_tip))
    app.add_handler(CommandHandler("sections", cmd_sections))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_risk_button, pattern=r"^risk:\d$"))
    app.add_handler(CallbackQueryHandler(on_section_button, pattern=r"^sec:\d+$"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_message))
    log.info("ربات پروتکل (بخش اصلی) + دستیار هوش مصنوعی (%s) راه‌اندازی می‌شود...", AI_API_URL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
