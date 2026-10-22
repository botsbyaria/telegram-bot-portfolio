# 🛟 Support Assistant Bot

24/7 customer support for online shops and small businesses, inside Telegram.

**Live demo:** [@ariahelp_demo_bot](https://t.me/ariahelp_demo_bot). In demo mode you also see the agent's side and can answer your own ticket by replying to it.

## For customers
- Browse FAQ topics with buttons, or just **type a question**: the bot finds the best matching answer.
- "Did this help?" → if not, one tap opens a **ticket** and a human agent takes over the chat (text, photos and files).
- Outside business hours the bot says when the team will reply.
- After the ticket is closed, the customer rates the support (1–5 ⭐).

## For the business
- Agents get every ticket message and answer by simply **replying** to it in Telegram. No extra app needed.
- Several agents are supported, and any of them can close a ticket with one button.
- `/tickets` lists open tickets. `/stats` shows FAQ answers, ticket count, average rating and the most-asked questions.

## Run it yourself
```bash
pip install -r requirements.txt
export BOT_TOKEN=123456:your-token-from-BotFather
export ADMIN_IDS=111111111          # agent Telegram user ids, comma-separated
export BUSINESS_NAME="Your Shop" TZ_NAME="Europe/Berlin"
python bot.py
```
FAQ topics, questions, answers and business hours are in the `CONFIG` block at the top of `bot.py`.

## Customisation ideas
Multi-language FAQ, AI answers from your own documents, a support group instead of private chats, a CRM/Helpdesk export (Google Sheets, Notion), or order lookup from your shop's API.
