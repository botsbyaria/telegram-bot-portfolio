"""
Crypto Price Alert Bot
======================
A Telegram bot that tracks cryptocurrency prices and notifies users when a coin
crosses a target price. Prices come from Binance's public API (no API key needed).

Features
- /price <coin>          live price + 24h change (e.g. /price btc)
- /alert <coin> <price>  notify me when the price crosses that level (up or down)
- /alerts                list my active alerts (with one-tap delete buttons)
- /watch <coin ...>      build a personal watchlist
- /watchlist             prices of everything on my watchlist
- /daily on|off          a short market summary every day at 09:00 UTC
Data is stored in SQLite, so alerts survive restarts.
"""
import asyncio
import logging
import os
import sqlite3
from datetime import time as dtime

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes)

TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "alerts.db")
CHECK_EVERY_SEC = int(os.environ.get("CHECK_EVERY_SEC", "60"))
BINANCE = "https://api.binance.com/api/v3"

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("crypto-alert-bot")
logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx would log every request URL, which contains the bot token


# ---------------------------------------------------------------- storage
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, symbol TEXT,
        target REAL, direction TEXT, created REAL DEFAULT (strftime('%s','now')))""")
    conn.execute("CREATE TABLE IF NOT EXISTS watch (chat_id INTEGER, symbol TEXT, PRIMARY KEY(chat_id, symbol))")
    conn.execute("CREATE TABLE IF NOT EXISTS daily (chat_id INTEGER PRIMARY KEY)")
    return conn


# ---------------------------------------------------------------- prices
def to_symbol(coin: str) -> str:
    coin = coin.upper().strip()
    return coin if coin.endswith("USDT") else coin + "USDT"


async def fetch_prices(symbols):
    """{symbol: (price, change_pct)} for the given symbols, one HTTP call."""
    if not symbols:
        return {}
    import json
    params = {"symbols": json.dumps(sorted(set(symbols)), separators=(",", ":"))}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(f"{BINANCE}/ticker/24hr", params=params) as r:
            if r.status != 200:
                return {}
            data = await r.json()
    return {d["symbol"]: (float(d["lastPrice"]), float(d["priceChangePercent"])) for d in data}


async def one_price(symbol):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(f"{BINANCE}/ticker/24hr", params={"symbol": symbol}) as r:
            if r.status != 200:
                return None
            d = await r.json()
    return float(d["lastPrice"]), float(d["priceChangePercent"])


def fmt_price(p: float) -> str:
    return f"${p:,.2f}" if p >= 1 else f"${p:,.6f}"


def fmt_line(symbol, price, chg):
    arrow = "🟢" if chg >= 0 else "🔴"
    return f"{arrow} <b>{symbol[:-4]}</b>  {fmt_price(price)}  ({chg:+.2f}%)"


# ---------------------------------------------------------------- commands
HELP = (
    "👋 <b>Crypto Price Alert Bot</b>\n\n"
    "/price <code>btc</code> – live price\n"
    "/alert <code>btc 70000</code> – notify me when BTC crosses $70,000\n"
    "/alerts – my active alerts\n"
    "/watch <code>btc eth sol</code> – add coins to my watchlist\n"
    "/watchlist – prices of my watchlist\n"
    "/daily <code>on</code> – daily market summary (09:00 UTC)\n"
)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📈 BTC", callback_data="p:BTCUSDT"),
                                InlineKeyboardButton("📈 ETH", callback_data="p:ETHUSDT"),
                                InlineKeyboardButton("⭐ Watchlist", callback_data="w")]])
    await update.message.reply_html(HELP, reply_markup=kb)


async def price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_html("Usage: /price <code>btc</code>")
    sym = to_symbol(ctx.args[0])
    res = await one_price(sym)
    if not res:
        return await update.message.reply_text(f"❌ Unknown coin: {ctx.args[0]}")
    await update.message.reply_html(fmt_line(sym, *res))


async def alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) != 2:
        return await update.message.reply_html("Usage: /alert <code>btc 70000</code>")
    sym = to_symbol(ctx.args[0])
    try:
        target = float(ctx.args[1].replace(",", "").replace("$", ""))
    except ValueError:
        return await update.message.reply_text("❌ The price must be a number, e.g. /alert btc 70000")
    res = await one_price(sym)
    if not res:
        return await update.message.reply_text(f"❌ Unknown coin: {ctx.args[0]}")
    now = res[0]
    direction = "above" if target > now else "below"
    with db() as c:
        c.execute("INSERT INTO alerts(chat_id, symbol, target, direction) VALUES (?,?,?,?)",
                  (update.effective_chat.id, sym, target, direction))
    await update.message.reply_html(
        f"✅ Alert set: I'll message you when <b>{sym[:-4]}</b> goes <b>{direction}</b> {fmt_price(target)}.\n"
        f"Current price: {fmt_price(now)}")


async def alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = c.execute("SELECT id, symbol, target, direction FROM alerts WHERE chat_id=? ORDER BY id",
                         (update.effective_chat.id,)).fetchall()
    if not rows:
        return await update.message.reply_text("You have no active alerts. Create one with /alert btc 70000")
    text = "🔔 <b>Your alerts</b>\n" + "\n".join(
        f"{i}. {s[:-4]} {d} {fmt_price(t)}" for i, (_, s, t, d) in enumerate(rows, 1))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🗑 Delete #{i}", callback_data=f"d:{aid}")]
                               for i, (aid, *_r) in enumerate(rows, 1)])
    await update.message.reply_html(text, reply_markup=kb)


async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_html("Usage: /watch <code>btc eth sol</code>")
    syms = [to_symbol(a) for a in ctx.args]
    known = await fetch_prices(syms)
    with db() as c:
        for s in known:
            c.execute("INSERT OR IGNORE INTO watch VALUES (?,?)", (update.effective_chat.id, s))
    bad = [s[:-4] for s in syms if s not in known]
    msg = f"⭐ Added: {', '.join(s[:-4] for s in known) or '—'}"
    if bad:
        msg += f"\n❌ Unknown: {', '.join(bad)}"
    await update.message.reply_text(msg)


async def watchlist_text(chat_id):
    with db() as c:
        syms = [r[0] for r in c.execute("SELECT symbol FROM watch WHERE chat_id=?", (chat_id,))]
    if not syms:
        return "Your watchlist is empty. Add coins with /watch btc eth"
    prices = await fetch_prices(syms)
    return "⭐ <b>Watchlist</b>\n" + "\n".join(fmt_line(s, *prices[s]) for s in syms if s in prices)


async def watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(await watchlist_text(update.effective_chat.id))


async def daily(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    on = bool(ctx.args) and ctx.args[0].lower() == "on"
    with db() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO daily VALUES (?)", (update.effective_chat.id,))
        else:
            c.execute("DELETE FROM daily WHERE chat_id=?", (update.effective_chat.id,))
    await update.message.reply_text("📰 Daily summary: ON (09:00 UTC)" if on else "📰 Daily summary: OFF")


async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data.startswith("p:"):
        res = await one_price(q.data[2:])
        if res:
            await q.message.reply_html(fmt_line(q.data[2:], *res))
    elif q.data == "w":
        await q.message.reply_html(await watchlist_text(q.message.chat_id))
    elif q.data.startswith("d:"):
        with db() as c:
            c.execute("DELETE FROM alerts WHERE id=? AND chat_id=?", (int(q.data[2:]), q.message.chat_id))
        await q.edit_message_text("🗑 Alert deleted. Use /alerts to see the rest.")


# ---------------------------------------------------------------- background jobs
async def check_alerts(ctx: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = c.execute("SELECT id, chat_id, symbol, target, direction FROM alerts").fetchall()
    if not rows:
        return
    prices = await fetch_prices([r[2] for r in rows])
    for aid, chat_id, sym, target, direction in rows:
        if sym not in prices:
            continue
        now = prices[sym][0]
        if (direction == "above" and now >= target) or (direction == "below" and now <= target):
            try:
                await ctx.bot.send_message(chat_id, f"🚨 <b>{sym[:-4]}</b> is now {fmt_price(now)} "
                                                    f"({direction} your target {fmt_price(target)})",
                                           parse_mode=ParseMode.HTML)
            except Exception as e:                                # user blocked the bot, etc.
                log.warning("send failed for %s: %s", chat_id, e)
            with db() as c:
                c.execute("DELETE FROM alerts WHERE id=?", (aid,))


async def daily_summary(ctx: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        chats = [r[0] for r in c.execute("SELECT chat_id FROM daily")]
    if not chats:
        return
    prices = await fetch_prices(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"])
    text = "📰 <b>Daily market summary</b>\n" + "\n".join(fmt_line(s, *v) for s, v in prices.items())
    for chat_id in chats:
        try:
            await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning("daily send failed for %s: %s", chat_id, e)
        await asyncio.sleep(0.05)                                 # stay under Telegram rate limits


def main():
    app = Application.builder().token(TOKEN).build()
    for name, fn in [("start", start), ("help", start), ("price", price), ("alert", alert),
                     ("alerts", alerts), ("watch", watch), ("watchlist", watchlist), ("daily", daily)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(buttons))
    app.job_queue.run_repeating(check_alerts, interval=CHECK_EVERY_SEC, first=10)
    app.job_queue.run_daily(daily_summary, time=dtime(9, 0))
    db().close()
    log.info("bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
