#!/usr/bin/env python3
"""ساخت صفحه‌ی /protocol برای ورکر کلودفلر از روی سایت استاتیک پروتکل.

- CSS به‌صورت درون‌خطی داخل HTML می‌آید
- ویجت گفت‌وگو همان ویجت سایت است (منبع واحد)؛ فقط آدرس API مطلق به آدرس نسبی
  /api/chat تبدیل می‌شود چون صفحه روی همان مبدأ ورکر سرو می‌شود
- خروجی: cloudflare/src/protocol_page.js با export PROTOCOL_HTML
"""
import json
from pathlib import Path

# مسیر ریشه‌ی فضای‌کار (هر دو مخزن کنار هم) — با متغیر محیطی هم قابل‌تنظیم است
import os
ROOT = Path(os.environ.get("WORKSPACE_ROOT", str(Path(__file__).resolve().parents[2])))
SITE = ROOT / "dual-diagnosis-protocol"
OUT = ROOT / "dual-diagnosis-rag/cloudflare/src/protocol_page.js"

html = (SITE / "index.html").read_text(encoding="utf-8")
css = (SITE / "style.css").read_text(encoding="utf-8")

# ۱) درون‌خطی کردن CSS
html = html.replace('<link rel="stylesheet" href="style.css" />',
                    "<style>\n" + css + "\n</style>")

# ۲) API نسبی برای سرو روی مبدأ ورکر
ABS_API = "https://dual-diagnosis-clinical-hub.elasa2next.workers.dev/api/chat"
assert ABS_API in html, "ویجت سایت باید آدرس مطلق API را داشته باشد"
html = html.replace(ABS_API, "/api/chat")

assert html.count('btn.id = "ddx-chat-btn"') == 1, "ویجت نباید دوبار تزریق شود"
assert "/api/chat" in html

OUT.write_text(
    "// ساخته‌شده به‌صورت خودکار از SBZ-EDU/dual-diagnosis-protocol\n"
    "// (ویجت گفت‌وگو منبع واحد است؛ API نسبی برای همین مبدأ)\n"
    "export const PROTOCOL_HTML = " + json.dumps(html, ensure_ascii=False) + ";\n",
    encoding="utf-8")

print("written:", OUT, "| HTML size:", len(html), "chars")
