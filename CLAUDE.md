# Telegram Autopost Bot

AI-driven Telegram channel autoposting system. Gemini generates Kazakh-language content, admin approves via bot, posts publish on schedule.

## Stack
- Python 3.11 + aiogram 3.x + google-genai SDK + asyncpg + APScheduler
- PostgreSQL on Railway (managed, no local DB)
- Deploy: git push → Railway rebuilds Docker image → test via Telegram bot

## Key files
- `prompts.py` — ALL prompt templates live here; edit to change content style/language
- `config.py` — all env vars loaded and validated here
- `database.py` — all DB operations (asyncpg, $1 $2 placeholders)
- `moderator_bot.py` — bot commands and FSM moderation flow

## Niche
`CONTENT_NICHE=вайб-кодинг және бизнесті автоматтандыру`

## Language
Posts: **Kazakh (қазақ тілі)**. Image prompts: English.

## Post status flow
draft → pending_review → approved → published
                       ↘ rejected

## Testing workflow (no local run)
Push to GitHub → Railway rebuilds (1-2 min) → check Logs → test via /start in Telegram

## Bot commands
/plan, /show_plan, /generate, /generate_all, /queue, /stats, /publish_now
