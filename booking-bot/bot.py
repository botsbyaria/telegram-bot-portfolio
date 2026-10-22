"""
Booking Assistant Bot
=====================
Appointment booking for small businesses (salon, clinic, tutor, studio...) inside Telegram.

Customers:  pick a service -> pick a day -> pick a free time slot -> confirm.
            See and cancel their bookings, and get a reminder 1 hour before.
Business:   gets a notification for every new or cancelled booking and can list
            today's / upcoming bookings with /today and /upcoming.

Services, opening hours, slot length and timezone are set in the CONFIG block below
(or via environment variables), so the same bot works for any business.
"""
import logging
import os
import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import (InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update)
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ---------------------------------------------------------------- CONFIG
TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "bookings.db")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Berlin"))
# Who receives booking notifications. Empty = demo mode: the customer also sees the
# notification the business would get, so people trying the demo see both sides.
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
BUSINESS = os.environ.get("BUSINESS_NAME", "Demo Studio")
SERVICES = {                      # id: (name, minutes, price)
    "cut": ("✂️ Haircut", 30, "$25"),
    "color": ("🎨 Coloring", 90, "$70"),
    "consult": ("💬 Consultation", 30, "Free"),
}
OPEN, CLOSE = time(10, 0), time(19, 0)
SLOT_MIN = 30
CLOSED_WEEKDAYS = {6}             # 6 = Sunday
DAYS_AHEAD = 7

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)      # request URLs contain the token
log = logging.getLogger("booking-bot")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, name TEXT, service TEXT,
        start TEXT, minutes INTEGER, reminded INTEGER DEFAULT 0)""")
    return conn


def now():
    return datetime.now(TZ)


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%a %d %b, %H:%M")


# ---------------------------------------------------------------- availability
def taken_ranges(day: date):
    with db() as c:
        rows = c.execute("SELECT start, minutes FROM bookings WHERE start LIKE ?", (day.isoformat() + "%",)).fetchall()
    out = []
    for s, m in rows:
        st = datetime.fromisoformat(s)
        out.append((st, st + timedelta(minutes=m)))
    return out


def free_slots(day: date, minutes: int):
    slots, taken = [], taken_ranges(day)
    t = datetime.combine(day, OPEN, TZ)
    end_of_day = datetime.combine(day, CLOSE, TZ)
    while t + timedelta(minutes=minutes) <= end_of_day:
        end = t + timedelta(minutes=minutes)
        if t > now() + timedelta(minutes=30) and all(end <= a or t >= b for a, b in taken):
            slots.append(t)
        t += timedelta(minutes=SLOT_MIN)
    return slots


def open_days():
    d, out = now().date(), []
    while len(out) < DAYS_AHEAD:
        if d.weekday() not in CLOSED_WEEKDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- screens
def main_menu():
    return Kb([[Btn("📅 Book an appointment", callback_data="book")],
               [Btn("📋 My bookings", callback_data="mine"), Btn("ℹ️ Info", callback_data="info")]])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        f"👋 Welcome to <b>{BUSINESS}</b>!\nBook your appointment in a few taps.", reply_markup=main_menu())


async def notify_admins(ctx, text, customer_chat):
    targets = ADMIN_IDS or {customer_chat}
    prefix = "" if ADMIN_IDS else "👀 <i>Demo: this is what the business owner receives</i>\n"
    for a in targets:
        try:
            await ctx.bot.send_message(a, prefix + text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning("admin notify failed: %s", e)


async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("|")
    kind = parts[0]

    if kind == "home":
        await q.edit_message_text(f"<b>{BUSINESS}</b> – what would you like to do?",
                                  parse_mode=ParseMode.HTML, reply_markup=main_menu())

    elif kind == "info":
        lines = "\n".join(f"{n} – {m} min – {p}" for n, m, p in SERVICES.values())
        closed = ", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] for d in sorted(CLOSED_WEEKDAYS))
        await q.edit_message_text(
            f"ℹ️ <b>{BUSINESS}</b>\n🕙 {OPEN:%H:%M}–{CLOSE:%H:%M} (closed: {closed or '—'})\n\n{lines}",
            parse_mode=ParseMode.HTML, reply_markup=Kb([[Btn("⬅️ Back", callback_data="home")]]))

    elif kind == "book":
        kb = [[Btn(f"{n} · {m} min · {p}", callback_data=f"svc|{sid}")] for sid, (n, m, p) in SERVICES.items()]
        kb.append([Btn("⬅️ Back", callback_data="home")])
        await q.edit_message_text("Choose a service:", reply_markup=Kb(kb))

    elif kind == "svc":
        sid = parts[1]
        days = [d for d in open_days() if free_slots(d, SERVICES[sid][1])]
        if not days:
            return await q.edit_message_text("😔 No free slots in the next days.", reply_markup=main_menu())
        kb = [[Btn(d.strftime("%a %d %b"), callback_data=f"day|{sid}|{d.isoformat()}")] for d in days]
        kb.append([Btn("⬅️ Back", callback_data="book")])
        await q.edit_message_text(f"{SERVICES[sid][0]} – choose a day:", reply_markup=Kb(kb))

    elif kind == "day":
        sid, d = parts[1], date.fromisoformat(parts[2])
        slots = free_slots(d, SERVICES[sid][1])
        rows, row = [], []
        for s in slots:
            row.append(Btn(s.strftime("%H:%M"), callback_data=f"slot|{sid}|{s.strftime('%Y-%m-%dT%H:%M')}"))
            if len(row) == 4:
                rows.append(row); row = []
        if row:
            rows.append(row)
        rows.append([Btn("⬅️ Back", callback_data=f"svc|{sid}")])
        await q.edit_message_text(f"{SERVICES[sid][0]} – {d:%a %d %b}\nChoose a time:", reply_markup=Kb(rows))

    elif kind == "slot":
        sid, st = parts[1], datetime.fromisoformat(parts[2]).replace(tzinfo=TZ)
        n, m, p = SERVICES[sid]
        await q.edit_message_text(
            f"Please confirm:\n\n{n}\n🗓 {fmt_dt(st)}\n⏱ {m} min · {p}",
            reply_markup=Kb([[Btn("✅ Confirm", callback_data=f"ok|{sid}|{parts[2]}")],
                             [Btn("⬅️ Change time", callback_data=f"day|{sid}|{st.date().isoformat()}")]]))

    elif kind == "ok":
        sid, st = parts[1], datetime.fromisoformat(parts[2]).replace(tzinfo=TZ)
        n, m, p = SERVICES[sid]
        if st not in free_slots(st.date(), m):                    # someone else took it meanwhile
            return await q.edit_message_text("😔 Sorry, that slot was just taken. Please pick another.",
                                             reply_markup=Kb([[Btn("📅 Pick again", callback_data=f"day|{sid}|{st.date().isoformat()}")]]))
        user = q.from_user
        name = (user.full_name or "Customer") + (f" (@{user.username})" if user.username else "")
        with db() as c:
            c.execute("INSERT INTO bookings(chat_id, name, service, start, minutes) VALUES (?,?,?,?,?)",
                      (q.message.chat_id, name, sid, st.isoformat(), m))
        await q.edit_message_text(f"🎉 Booked!\n\n{n}\n🗓 {fmt_dt(st)}\n\nI'll remind you 1 hour before.",
                                  reply_markup=main_menu())
        await notify_admins(ctx, f"🆕 <b>New booking</b>\n{name}\n{n}\n🗓 {fmt_dt(st)}", q.message.chat_id)

    elif kind == "mine":
        with db() as c:
            rows = c.execute("SELECT id, service, start FROM bookings WHERE chat_id=? AND start>=? ORDER BY start",
                             (q.message.chat_id, now().isoformat())).fetchall()
        if not rows:
            return await q.edit_message_text("You have no upcoming bookings.", reply_markup=main_menu())
        text = "📋 <b>Your bookings</b>\n" + "\n".join(
            f"• {SERVICES[s][0]} – {fmt_dt(datetime.fromisoformat(st))}" for _, s, st in rows)
        kb = [[Btn(f"❌ Cancel {SERVICES[s][0]} {datetime.fromisoformat(st):%d %b %H:%M}", callback_data=f"cancel|{bid}")]
              for bid, s, st in rows]
        kb.append([Btn("⬅️ Back", callback_data="home")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=Kb(kb))

    elif kind == "cancel":
        with db() as c:
            row = c.execute("SELECT name, service, start FROM bookings WHERE id=? AND chat_id=?",
                            (int(parts[1]), q.message.chat_id)).fetchone()
            c.execute("DELETE FROM bookings WHERE id=? AND chat_id=?", (int(parts[1]), q.message.chat_id))
        await q.edit_message_text("🗑 Booking cancelled.", reply_markup=main_menu())
        if row:
            await notify_admins(ctx, f"❌ <b>Booking cancelled</b>\n{row[0]}\n{SERVICES[row[1]][0]}\n"
                                     f"🗓 {fmt_dt(datetime.fromisoformat(row[2]))}", q.message.chat_id)


# ---------------------------------------------------------------- business owner commands
def is_admin(update: Update):
    return not ADMIN_IDS or update.effective_user.id in ADMIN_IDS


async def list_bookings(update: Update, until: datetime, title: str):
    with db() as c:
        rows = c.execute("SELECT name, service, start FROM bookings WHERE start>=? AND start<? ORDER BY start",
                         (now().isoformat(), until.isoformat())).fetchall()
    body = "\n".join(f"• {datetime.fromisoformat(st):%a %H:%M} – {SERVICES[s][0]} – {n}" for n, s, st in rows)
    await update.message.reply_html(f"<b>{title}</b>\n{body or 'No bookings.'}")


async def today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        end = datetime.combine(now().date() + timedelta(days=1), time(0), TZ)
        await list_bookings(update, end, "📅 Today")


async def upcoming(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        await list_bookings(update, now() + timedelta(days=DAYS_AHEAD + 1), "🗓 Upcoming bookings")


async def reminders(ctx: ContextTypes.DEFAULT_TYPE):
    soon = (now() + timedelta(hours=1)).isoformat()
    with db() as c:
        rows = c.execute("SELECT id, chat_id, service, start FROM bookings WHERE reminded=0 AND start<=? AND start>=?",
                         (soon, now().isoformat())).fetchall()
        for bid, chat_id, sid, st in rows:
            try:
                await ctx.bot.send_message(chat_id, f"⏰ Reminder: {SERVICES[sid][0]} at "
                                                    f"{datetime.fromisoformat(st):%H:%M} today at {BUSINESS}.")
            except Exception as e:
                log.warning("reminder failed: %s", e)
            c.execute("UPDATE bookings SET reminded=1 WHERE id=?", (bid,))


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "book", "help"], start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("upcoming", upcoming))
    app.add_handler(CallbackQueryHandler(buttons))
    app.job_queue.run_repeating(reminders, interval=120, first=15)
    db().close()
    log.info("booking bot started (%s, tz=%s, admins=%s)", BUSINESS, TZ, ADMIN_IDS or "demo mode")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
