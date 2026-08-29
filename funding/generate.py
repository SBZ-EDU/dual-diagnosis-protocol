#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""تولید پیش‌نویس درخواست برای هر برنامه + داشبورد وضعیت.

اجرا:  python funding/generate.py
ورودی: funding/programs.json + funding/profile.md + .env (تماس)
خروجی: funding/proposals/<id>.md و funding/dashboard.html
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import date, datetime

FUND = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(FUND)
TODAY = date.today()

ACCESS_FA = {
    "ok": ("✅ بدون مانع تحریم", "#0c7a43"),
    "company": ("⚠️ نیاز به شرکت غیرایرانی", "#b45309"),
    "unclear": ("🟡 نامشخص — بررسی شود", "#8a6d00"),
}
STATUS_FA = {
    "open": ("باز", "#0c7a43"),
    "closed": ("بسته — رصد", "#b43d4d"),
    "monitor": ("رصد", "#8a6d00"),
    "unavailable": ("خارج از دسترس", "#6b7280"),
}
PRIORITY_FA = {1: "۱ — همین حالا", 2: "۲ — بعد از ثبت شرکت", 3: "۳ — بررسی/شرایط خاص", 4: "۴ — رصد"}


def load_env(path: str) -> dict:
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def parse_profile(path: str) -> dict[str, str]:
    """فایل profile.md را به دیکشنری بخش‌ها (## سرصفحه) تبدیل می‌کند."""
    sections: dict[str, str] = {}
    cur, buf = None, []
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if cur:
                sections[cur] = "".join(buf).strip()
            cur, buf = m.group(1).strip(), []
        elif cur is not None:
            buf.append(line)
    if cur:
        sections[cur] = "".join(buf).strip()
    return sections


def fa_digits(s) -> str:
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def days_left(deadline: str | None) -> int | None:
    if not deadline:
        return None
    try:
        return (datetime.strptime(deadline, "%Y-%m-%d").date() - TODAY).days
    except ValueError:
        return None


def load_contact() -> tuple[str, str]:
    env = load_env(os.path.join(ROOT, ".env"))
    return env.get("FUNDING_CONTACT_EMAIL", "[email — set in .env]"), \
           env.get("FUNDING_WHATSAPP", "[whatsapp — set in .env]")


# ---------------- پیش‌نویس درخواست هر برنامه ----------------
def proposal(prog: dict, profile: dict[str, str], startup: dict, email: str, wa: str) -> str:
    dl = days_left(prog.get("deadline"))
    dl_note = ""
    if dl is not None:
        dl_note = f"\n**⏳ Days left: {dl}**" if dl >= 0 else f"\n**⛔ Deadline passed {abs(dl)} days ago**"
    req = "\n".join(f"- [ ] {r}" for r in prog.get("requirements", [])) or "- [ ] (see program page)"
    blockers = "\n".join(f"- {b}" for b in prog.get("blockers", [])) or "- None flagged"
    return f"""# Application Draft — {prog['name']}

*Generated {TODAY.isoformat()} for **{startup['name']}** ({startup['persian_name']})*
*Program status: {prog['status']} · {prog.get('deadline_note') or 'no deadline'}{dl_note}*
*Apply at: {prog['link']}*

---

## Why this program / why us

{prog['angle']}

**What the program offers:** {prog['benefits']}
**Cost to us:** {prog['cost']} · **Equity:** {prog['equity']}

## The problem we solve

{profile.get('Problem', '(fill profile.md)')}

## Our solution

{profile.get('Solution', '(fill profile.md)')}

## Traction

{profile.get('Traction', '(fill profile.md)')}

## Why now

{profile.get('Why now', '(fill profile.md)')}

## Team

{profile.get('Company', '(fill profile.md)')}

## Our ask

{profile.get('Ask', '(fill profile.md)')}

## Application checklist

{req}

**Known blockers to address honestly in the form:**

{blockers}

**Response time:** {prog['response_time']} → if no reply, send a follow-up email
**{prog['followup_days']} days** after applying. Follow-up from: {email}

## Contact

- Email: {email}
- WhatsApp: {wa}
- Product: {startup['site']} · Bot: {startup['bot']} · Channel: {startup['channel']}
- Code: {startup['github']}

---
*⚠️ این پیش‌نویس خروجی خودکار است — پیش از ارسال، پاسخ‌های فرم را با جزئیات واقعی تیم و اعداد
traction تکمیل و ویرایش کنید. دستور بازتولید: `python funding/generate.py`*
"""


# ---------------- داشبورد HTML ----------------
def dashboard(data: dict) -> str:
    progs = sorted(data["programs"], key=lambda p: (p["priority"], days_left(p.get("deadline")) if days_left(p.get("deadline")) is not None else 9999))
    cards = []
    for p in progs:
        st, stc = STATUS_FA[p["status"]]
        ac, acolor = ACCESS_FA.get(p["iran_access"], ("؟", "#6b7280"))
        dl = days_left(p.get("deadline"))
        if dl is None:
            dltxt = p.get("deadline_note") or "بدون مهلت"
            dlc = "#4b5563"
        elif dl < 0:
            dltxt = f"مهلت {fa_digits(abs(dl))} روز پیش تمام شد"
            dlc = "#b43d4d"
        elif dl <= 3:
            dltxt = f"⏳ فقط {fa_digits(dl)} روز مانده!"
            dlc = "#b43d4d"
        else:
            dltxt = f"{fa_digits(dl)} روز مانده"
            dlc = "#0c7a43"
        link = p["link"]
        cards.append(f"""
<div class="card">
  <div class="head"><span class="dot" style="background:{stc}"></span>
    <b>{p['name']}</b>
    <span class="badge" style="background:{stc}1a;color:{stc}">{st}</span></div>
  <div class="meta">{p['org']} · {p['country']} · اولویت {PRIORITY_FA[p['priority']]}</div>
  <div class="deadline" style="color:{dlc}">🗓 {dltxt}</div>
  <div class="row"><span class="k">مزیت:</span> {p['benefits']}</div>
  <div class="row"><span class="k">دسترسی از ایران:</span> <span style="color:{acolor}">{ac}</span>
   · <span class="k">پاسخ:</span> {p['response_time']}
   · <span class="k">پیگیری:</span> {fa_digits(p['followup_days']) if p['followup_days'] else '—'} روز بعد</div>
  <div class="row"><span class="k">سهام:</span> {p['equity']} · <span class="k">هزینه:</span> {p['cost']}</div>
  {f'<div class="row warn">⚠️ {p["blockers"][0]}</div>' if p.get('blockers') else ''}
  <a class="lnk" href="{link}" target="_blank" rel="noopener">صفحه‌ی رسمی ↗</a>
  <span class="verified">تأیید: {fa_digits(p['verified'].replace('-', '/'))}</span>
</div>""")
    n_open = sum(1 for p in data["programs"] if p["status"] == "open")
    urgent = [p for p in progs if p["priority"] == 1]
    urgent_html = "".join(f"<li><b>{p['name']}</b> — {p.get('deadline_note','')}</li>" for p in urgent)
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>یابنده‌ی شتاب‌دهنده | همراه</title><style>
body{{margin:0;font-family:Tahoma,Arial,sans-serif;background:#f4f8f7;color:#102f35;line-height:1.8}}
.wrap{{max-width:1080px;margin:auto;padding:24px 18px 60px}}
header{{background:linear-gradient(135deg,#083c43,#08786f);color:#fff;border-radius:18px;padding:26px;margin-bottom:22px}}
h1{{margin:0 0 6px;font-size:24px}} .sub{{opacity:.85;font-size:14px}}
.stats{{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}}
.stat{{background:#ffffff1c;border:1px solid #ffffff30;border-radius:12px;padding:8px 14px;font-size:14px}}
.plan{{background:#fff;border:1px solid #dce8e6;border-right:5px solid #087d78;border-radius:14px;padding:16px 18px;margin-bottom:22px}}
.plan h2{{margin:0 0 8px;font-size:17px}} .plan ol{{margin:6px 0 0;padding-right:20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}
.card{{background:#fff;border:1px solid #dce8e6;border-radius:16px;padding:16px;box-shadow:0 8px 24px #163e380a}}
.head{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:16px}}
.dot{{width:10px;height:10px;border-radius:50%;flex:none}}
.badge{{font-size:12px;border-radius:20px;padding:2px 10px;margin-right:auto}}
.meta{{color:#627a7e;font-size:13px;margin:4px 0}}
.deadline{{font-weight:bold;font-size:14px;margin:6px 0}}
.row{{font-size:13.5px;margin:3px 0}} .k{{color:#087d78;font-weight:bold}}
.warn{{color:#9a3412;background:#fff7ed;border-radius:8px;padding:4px 8px}}
.lnk{{display:inline-block;margin-top:8px;color:#087d78;font-weight:bold;text-decoration:none;font-size:14px}}
.verified{{float:left;color:#9aa;font-size:12px}}
.unlock{{background:#fffbeb;border:1px solid #fde68a;border-radius:14px;padding:14px 18px;margin-bottom:22px;font-size:14px}}
footer{{margin-top:26px;color:#627a7e;font-size:12.5px}}
</style></head><body><div class="wrap">
<header><h1>🚀 یابنده‌ی شتاب‌دهنده و اعتبار ابری — پروژه‌ی «همراه»</h1>
<div class="sub">داشبورد فرصت‌های سرمایه/اعتبار برای مرکز هوشمند تشخیص دوگانه · تولیدشده {fa_digits(TODAY.strftime('%Y/%m/%d'))} · <code>python funding/generate.py</code> برای تازه‌سازی</div>
<div class="stats">
<div class="stat">📁 {fa_digits(len(data['programs']))} برنامه</div>
<div class="stat">🟢 {fa_digits(n_open)} باز</div>
<div class="stat">⚠️ {fa_digits(sum(1 for p in data['programs'] if p['iran_access']=='company'))} نیاز به شرکت غیرایرانی</div>
<div class="stat">✅ {fa_digits(sum(1 for p in data['programs'] if p['iran_access']=='ok'))} بدون مانع تحریم</div>
</div></header>
<div class="unlock">🔑 <b>قفل اصلی:</b> {data['common_blocker']['title']} — {data['common_blocker']['detail']}<br>
<span style="color:#087d78"><b>زنجیره‌ی بازکردن:</b> {data['common_blocker']['unlock_chain']}</span></div>
<div class="plan"><h2>🎯 برنامه‌ی اقدام (اولویت ۱)</h2><ol>{urgent_html}</ol></div>
<div class="grid">{''.join(cards)}</div>
<footer>⚠️ تاریخ‌ها را پیش از اقدام روی صفحه‌ی رسمی هر برنامه دوباره تأیید کنید.
برای به‌روزرسانی وضعیت‌ها: <code>python funding/check_status.py</code> و سپس بازتولید داشبورد.
پیش‌نویس درخواست هر برنامه: پوشه‌ی <code>funding/proposals/</code>.</footer>
</div></body></html>"""


def main() -> None:
    data = json.load(open(os.path.join(FUND, "programs.json"), encoding="utf-8"))
    profile = parse_profile(os.path.join(FUND, "profile.md"))
    email, wa = load_contact()
    out = os.path.join(FUND, "proposals")
    os.makedirs(out, exist_ok=True)
    for prog in data["programs"]:
        path = os.path.join(out, f"{prog['id']}.md")
        open(path, "w", encoding="utf-8").write(proposal(prog, profile, data["startup"], email, wa))
    open(os.path.join(FUND, "dashboard.html"), "w", encoding="utf-8").write(dashboard(data))
    print(f"OK: {len(data['programs'])} proposals → funding/proposals/ + dashboard.html")


if __name__ == "__main__":
    main()
