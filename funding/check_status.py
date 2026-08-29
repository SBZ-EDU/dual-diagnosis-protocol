#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""بررسی وضعیت صفحات رسمی برنامه‌ها (باز/بسته/قابل‌دسترس).

اجرا:  python funding/check_status.py
- هر صفحه را می‌گیرد، کد HTTP و عنوان و کلیدواژه‌های «closed / apply / deadline» را گزارش می‌کند.
- فیلد last_checked را در programs.json به‌روز می‌کند.
- برای تفسیر نهایی (تغییر مهلت‌ها و جزئیات) نتیجه را به دستیار هوشمند بدهید تا
  programs.json را ویرایش و dashboard را بازتولید کند.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.request
from datetime import date

FUND = os.path.dirname(os.path.abspath(__file__))
UA = {"user-agent": "hamrah-funding-checker/1.0"}
CTX = ssl.create_default_context()
FLAGS = {
    "closed": ["applications are closed", "application closed", "başvurular kapandı"],
    "open": ["apply now", "apply by", "applications open", "başvuruları açıldı", "apply today"],
    "deadline": [r"deadline[:\s][^<\n]{0,60}", r"apply by [A-Z][a-z]+ \d{1,2}", r"\d{1,2} [A-Z][a-z]+ 20\d\d"],
}


def fetch(url: str) -> tuple[int | None, str]:
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
            body = r.read(200_000).decode("utf-8", "replace")
            return r.status, body
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def main() -> None:
    path = os.path.join(FUND, "programs.json")
    data = json.load(open(path, encoding="utf-8"))
    today = date.today().isoformat()
    print(f"{'program':38} http  flags")
    print("-" * 78)
    for p in data["programs"]:
        code, body = fetch(p["link"])
        flags = []
        if code == 200 and body:
            low = body.lower()
            for name, pats in FLAGS.items():
                for pat in pats:
                    if re.search(pat, low):
                        flags.append(name)
                        break
        status = "OK" if code == 200 else f"ERR({code})"
        print(f"{p['id']:38} {status:5} {','.join(sorted(set(flags))) or '-'}")
        p["last_checked"] = today
        if code == 200:
            p.setdefault("verified", today)
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nlast_checked → {today} ذخیره شد. برای بازتولید داشبورد: python funding/generate.py")


if __name__ == "__main__":
    main()
