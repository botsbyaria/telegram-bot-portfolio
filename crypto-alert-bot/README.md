# 🪙 Crypto Price Alert Bot

A Telegram bot that watches cryptocurrency prices and pings you the moment a coin crosses your target.

**Live demo:** [@aria_pricewatch_bot](https://t.me/aria_pricewatch_bot)

## Features
| Command | What it does |
|---|---|
| `/price btc` | Live price and 24h change |
| `/alert btc 70000` | Notify me when BTC crosses $70,000 (works for rises and drops) |
| `/alerts` | List active alerts, with one-tap delete buttons |
| `/watch btc eth sol` | Build a personal watchlist |
| `/watchlist` | Prices of all coins on my watchlist |
| `/daily on` | Daily market summary at 09:00 UTC |

- Real-time prices from Binance's public API (no API key needed)
- Alerts are checked every 60 seconds and stored in SQLite, so they survive restarts
- Async (python-telegram-bot v21), lightweight, runs on a $5 VPS

## Run it yourself
```bash
pip install -r requirements.txt
export BOT_TOKEN=123456:your-token-from-BotFather
python bot.py
```

Optional settings: `DB_PATH` (default `alerts.db`) and `CHECK_EVERY_SEC` (default `60`).

## Customisation ideas
Other exchanges, percentage-move alerts, multi-language support, admin broadcast, or paid premium tiers.
