#!/usr/bin/env bash
# راه‌اندازی ربات با نصب خودکار وابستگی‌ها + ری‌استارت خودکار بعد از خطا
# (برای ری‌استارت‌های محیطی، همین اسکریپت را دوباره اجرا کنید)
set -e
cd "$(dirname "$0")"
pip install -q -r requirements.txt
while true; do
  python3 telegram_bot.py && break
  echo "⚠️ بات خطا داد — ۵ ثانیه دیگر دوباره..." >&2
  sleep 5
done
