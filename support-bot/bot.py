"""
Support Assistant Bot
=====================
24/7 customer support for an online shop (or any small business) inside Telegram.

Customers:  browse FAQ topics with buttons, or just type a question - the bot finds the
            best matching answer. If it doesn't help, one tap opens a ticket and a human
            agent takes over the chat. After the ticket is closed they rate the support.
Business:   agents receive every ticket message and answer by simply *replying* to it.
            /tickets lists open tickets, /stats shows tickets, ratings and top FAQ topics.

FAQ content and business hours are set in the CONFIG block below (or via environment
variables), so the same bot works for any business.
"""
import logging
import os
import re
import sqlite3
from datetime import datetime, time
from zoneinfo import ZoneInfo

from telegram import (InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

# ---------------------------------------------------------------- CONFIG
TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "support.db")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Berlin"))
# Who receives tickets. Empty = demo mode: the customer also sees the agent's side and can
# answer their own ticket by replying to it, so people trying the demo see both sides.
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
BUSINESS = os.environ.get("BUSINESS_NAME", "Demo Shop")
HOURS = (time(9, 0), time(18, 0))   # agents online (Mon-Fri)
REPLY_PROMISE = "within a few hours"

# topic id: (button title, [(question, answer, keywords)])
FAQ = {
    "orders": ("📦 Orders & shipping", [
        ("How long does shipping take?",
         "Orders ship within 1 business day. Delivery takes 2-4 days in the EU and 5-10 days worldwide.",
         "shipping ship delivery deliver long days arrive when"),
        ("How can I track my order?",
         "You get a tracking link by email as soon as your parcel ships. You can also send us your order number here.",
         "track tracking where order parcel package status"),
        ("Do you ship internationally?",
         "Yes, we ship to 40+ countries. Shipping costs are shown at checkout before you pay.",
         "international worldwide country countries abroad cost"),
        ("Can I cancel or change my order?",
         "Yes, until it ships (usually within a few hours). Tap 'Talk to a human' and send your order number.",
         "cancel change modify edit order mistake"),
    ]),
    "returns": ("↩️ Returns & refunds", [
        ("What is your return policy?",
         "You can return any unused item within 30 days. Returns are free within the EU.",
         "return returns policy send back exchange"),
        ("When will I get my refund?",
         "Refunds are sent within 3 business days after we receive the item, to your original payment method.",
         "refund money back when receive"),
        ("My item arrived damaged",
         "Sorry about that! Send us a photo with your order number and we'll ship a replacement for free.",
         "damaged broken wrong defective faulty missing"),
    ]),
    "payment": ("💳 Payment", [
        ("Which payment methods do you accept?",
         "Cards (Visa, Mastercard), PayPal, Apple Pay, Google Pay and crypto (USDT, BTC).",
         "payment pay methods card paypal crypto usdt btc accept"),
        ("Is my payment secure?",
         "Yes. Payments are processed by certified providers; we never see or store your card number.",
         "secure safe security card data"),
        ("Do you have discount codes?",
         "Join our newsletter for 10% off your first order. Codes are entered at checkout.",
         "discount coupon code promo sale offer cheaper"),
    ]),
    "account": ("👤 Account", [
        ("I forgot my password",
         "Tap 'Forgot password' on the login page and we'll email you a reset link.",
         "password forgot reset login log sign"),
        ("How do I change my address or email?",
         "Go to Account → Settings. Address changes for an order already shipped are not possible.",
         "change address email update settings"),
        ("How do I delete my account?",
         "Ask here and an agent will delete your account and data within 48 hours.",
         "delete account remove data gdpr close"),
    ]),
}

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)      # request URLs contain the token
log = logging.getLogger("support-bot")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, name TEXT,
            status TEXT DEFAULT 'open', created TEXT, closed TEXT, rating INTEGER);
        CREATE TABLE IF NOT EXISTS relay (       -- agent-side message -> ticket
            agent_chat INTEGER, agent_msg INTEGER, ticket_id INTEGER,
            PRIMARY KEY (agent_chat, agent_msg));
        CREATE TABLE IF NOT EXISTS faq_hits (key TEXT PRIMARY KEY, n INTEGER DEFAULT 0);""")
    return conn


def now():
    return datetime.now(TZ)


def agents_online():
    t = now()
    return t.weekday() < 5 and HOURS[0] <= t.time() < HOURS[1]


def open_ticket(chat_id):
    with db() as c:
        row = c.execute("SELECT id FROM tickets WHERE chat_id=? AND status='open'", (chat_id,)).fetchone()
    return row[0] if row else None


def count_hit(key):
    with db() as c:
        c.execute("INSERT INTO faq_hits(key, n) VALUES(?, 1) ON CONFLICT(key) DO UPDATE SET n=n+1", (key,))


# ---------------------------------------------------------------- keyboards
def main_menu():
    rows = [[Btn(title, callback_data=f"topic:{tid}")] for tid, (title, _) in FAQ.items()]
    rows.append([Btn("🙋 Talk to a human", callback_data="human")])
    return Kb(rows)


def topic_menu(tid):
    rows = [[Btn(q, callback_data=f"q:{tid}:{i}")] for i, (q, _, _) in enumerate(FAQ[tid][1])]
    rows.append([Btn("⬅️ Back", callback_data="menu")])
    return Kb(rows)


def helpful_kb():
    return Kb([[Btn("👍 Yes, thanks", callback_data="helped"), Btn("🙋 No, talk to a human", callback_data="human")],
               [Btn("📚 Other questions", callback_data="menu")]])


def agent_kb(ticket_id):
    return Kb([[Btn(f"✅ Close ticket #{ticket_id}", callback_data=f"close:{ticket_id}")]])


# ---------------------------------------------------------------- FAQ search
WORD = re.compile(r"[a-z]+")
STOP = set("a an the i my me you your we our is are am do does did can could how what when where why which who "
           "to of in on at for with and or it this that be have has was will would please hi hello there".split())


def best_answer(text):
    words = set(WORD.findall(text.lower())) - STOP
    best, best_score = None, 0
    for tid, (_, items) in FAQ.items():
        for i, (q, a, kw) in enumerate(items):
            keys = (set(kw.split()) | set(WORD.findall(q.lower()))) - STOP
            score = sum(1 for w in words if w in keys or any(k.startswith(w) and len(w) > 3 for k in keys))
            if score > best_score:
                best, best_score = (tid, i), score
    return best if best_score >= 1 else None


# ---------------------------------------------------------------- customer side
WELCOME = ("👋 Hi {name}! I'm the support assistant of <b>{biz}</b>.\n\n"
           "Pick a topic below, or simply <b>type your question</b>. "
           "If I can't help, a human agent will take over.")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(WELCOME.format(name=update.effective_user.first_name, biz=BUSINESS),
                                    reply_markup=main_menu())
    if not ADMIN_IDS:
        await update.message.reply_html(
            "ℹ️ <i>Demo mode: when you open a ticket you'll also see what the support agent sees. "
            "Reply to that agent message to answer as the agent.</i>")


async def notify_agents(ctx, text, ticket_id, fallback_chat, buttons=True):
    """Send a ticket message to all agents (or back to the customer in demo mode) and remember it."""
    targets = ADMIN_IDS or {fallback_chat}
    for chat in targets:
        try:
            m = await ctx.bot.send_message(chat, text, parse_mode="HTML",
                                         reply_markup=agent_kb(ticket_id) if buttons else None)
            with db() as c:
                c.execute("INSERT OR REPLACE INTO relay VALUES(?,?,?)", (chat, m.message_id, ticket_id))
        except Exception as e:
            log.warning("agent notify failed: %s", e)


async def create_ticket(update: Update, ctx, first_message=None):
    user, chat_id = update.effective_user, update.effective_chat.id
    tid = open_ticket(chat_id)
    if not tid:
        with db() as c:
            tid = c.execute("INSERT INTO tickets(chat_id, name, created) VALUES(?,?,?)",
                            (chat_id, user.full_name, now().isoformat())).lastrowid
    when = "An agent will answer shortly." if agents_online() else \
        f"Our team is offline right now (Mon-Fri {HOURS[0]:%H:%M}-{HOURS[1]:%H:%M}), we'll answer {REPLY_PROMISE}."
    await ctx.bot.send_message(chat_id, f"🎫 Ticket <b>#{tid}</b> opened. {when}\n"
                                        "Write your message here - you can also send photos.", parse_mode="HTML")
    header = f"🆕 <b>Ticket #{tid}</b> from {user.full_name}"
    if user.username:
        header += f" (@{user.username})"
    body = f"\n\n{first_message}" if first_message else "\n\n<i>(customer is typing…)</i>"
    await notify_agents(ctx, header + body + "\n\n↩️ <i>Reply to this message to answer.</i>", tid, chat_id)


async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    if parts[0] == "menu":
        await q.edit_message_text("What can I help you with?", reply_markup=main_menu())
    elif parts[0] == "topic":
        await q.edit_message_text(FAQ[parts[1]][0], reply_markup=topic_menu(parts[1]))
    elif parts[0] == "q":
        tid, i = parts[1], int(parts[2])
        question, answer, _ = FAQ[tid][1][i]
        count_hit(f"{tid}:{i}")
        await q.edit_message_text(f"<b>{question}</b>\n\n{answer}\n\nDid this answer your question?",
                                  parse_mode="HTML", reply_markup=helpful_kb())
    elif parts[0] == "helped":
        await q.edit_message_text("🙂 Glad I could help! Type /start anytime.")
    elif parts[0] == "human":
        await q.edit_message_reply_markup(None)
        await create_ticket(update, ctx)
    elif parts[0] == "close":
        await close_ticket(ctx, int(parts[1]), q.message.chat_id)
        await q.edit_message_reply_markup(None)
    elif parts[0] == "rate":
        with db() as c:
            c.execute("UPDATE tickets SET rating=? WHERE id=? AND chat_id=?",
                      (int(parts[2]), int(parts[1]), q.message.chat_id))
        await q.edit_message_text(f"Thanks for your feedback! {'⭐' * int(parts[2])}")
        await notify_agents(ctx, f"⭐ Ticket #{parts[1]} rated <b>{parts[2]}/5</b>", int(parts[1]),
                            q.message.chat_id, buttons=False)


async def close_ticket(ctx, ticket_id, by_chat):
    with db() as c:
        row = c.execute("SELECT chat_id FROM tickets WHERE id=? AND status='open'", (ticket_id,)).fetchone()
        if not row:
            await ctx.bot.send_message(by_chat, f"Ticket #{ticket_id} is already closed.")
            return
        c.execute("UPDATE tickets SET status='closed', closed=? WHERE id=?", (now().isoformat(), ticket_id))
    stars = Kb([[Btn(f"{n} ⭐", callback_data=f"rate:{ticket_id}:{n}") for n in range(1, 6)]])
    await ctx.bot.send_message(row[0], f"✅ Ticket #{ticket_id} has been closed. How was our support?",
                               reply_markup=stars)
    if by_chat != row[0]:
        await ctx.bot.send_message(by_chat, f"✅ Ticket #{ticket_id} closed.")


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg, chat_id = update.message, update.effective_chat.id

    # 1) an agent replying to a ticket message -> deliver to the customer
    if msg.reply_to_message and (not ADMIN_IDS or update.effective_user.id in ADMIN_IDS):
        with db() as c:
            row = c.execute("SELECT r.ticket_id, t.chat_id, t.status FROM relay r JOIN tickets t ON t.id=r.ticket_id "
                            "WHERE r.agent_chat=? AND r.agent_msg=?",
                            (chat_id, msg.reply_to_message.message_id)).fetchone()
        if row:
            ticket_id, customer, status = row
            if status != "open":
                await msg.reply_text(f"Ticket #{ticket_id} is closed.")
                return
            if msg.text:
                await ctx.bot.send_message(customer, f"💬 <b>Support:</b> {msg.text_html}", parse_mode="HTML")
            else:
                await msg.copy(customer)
            await msg.reply_text(f"✉️ Sent to ticket #{ticket_id}.")
            return

    # 2) a customer with an open ticket -> forward to agents
    ticket_id = open_ticket(chat_id)
    if ticket_id:
        user = update.effective_user
        if msg.text:
            await notify_agents(ctx, f"💬 <b>Ticket #{ticket_id}</b> – {user.full_name}:\n{msg.text_html}",
                                ticket_id, chat_id)
        else:
            for chat in ADMIN_IDS or {chat_id}:
                try:
                    m = await msg.copy(chat, reply_markup=agent_kb(ticket_id))
                    await ctx.bot.send_message(chat, f"📎 Ticket #{ticket_id} – {user.full_name} sent this.",
                                               reply_to_message_id=m.message_id)
                except Exception as e:
                    log.warning("agent copy failed: %s", e)
                    continue
                with db() as c:
                    c.execute("INSERT OR REPLACE INTO relay VALUES(?,?,?)", (chat, m.message_id, ticket_id))
        return

    # 3) a plain question -> search the FAQ
    if not msg.text:
        await msg.reply_text("Please type your question, or tap 'Talk to a human' to send files to an agent.",
                             reply_markup=main_menu())
        return
    hit = best_answer(msg.text)
    if hit:
        tid, i = hit
        question, answer, _ = FAQ[tid][1][i]
        count_hit(f"{tid}:{i}")
        await msg.reply_html(f"<b>{question}</b>\n\n{answer}\n\nDid this answer your question?",
                             reply_markup=helpful_kb())
    else:
        await msg.reply_html("🤔 I couldn't find an answer to that.",
                             reply_markup=Kb([[Btn("🙋 Talk to a human", callback_data="human")],
                                              [Btn("📚 Browse topics", callback_data="menu")]]))


# ---------------------------------------------------------------- customer & agent commands
async def human(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await create_ticket(update, ctx)


async def end(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ticket_id = open_ticket(update.effective_chat.id)
    if ticket_id:
        await close_ticket(ctx, ticket_id, update.effective_chat.id)
    else:
        await update.message.reply_text("You have no open ticket.", reply_markup=main_menu())


def is_admin(update: Update):
    return not ADMIN_IDS or update.effective_user.id in ADMIN_IDS


async def tickets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with db() as c:
        rows = c.execute("SELECT id, name, created FROM tickets WHERE status='open' ORDER BY id").fetchall()
    body = "\n".join(f"• #{i} – {n} – since {datetime.fromisoformat(cr):%d %b %H:%M}" for i, n, cr in rows)
    await update.message.reply_html(f"<b>🎫 Open tickets</b>\n{body or 'None 🎉'}")


async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with db() as c:
        total, open_n = c.execute("SELECT COUNT(*), SUM(status='open') FROM tickets").fetchone()
        avg, rated = c.execute("SELECT AVG(rating), COUNT(rating) FROM tickets").fetchone()
        top = c.execute("SELECT key, n FROM faq_hits ORDER BY n DESC LIMIT 3").fetchall()
        answered = c.execute("SELECT COALESCE(SUM(n), 0) FROM faq_hits").fetchone()[0]
    top_txt = "\n".join(f"  {n}× {FAQ[k.split(':')[0]][1][int(k.split(':')[1])][0]}" for k, n in top) or "  –"
    await update.message.reply_html(
        f"<b>📊 Support stats</b>\n"
        f"Answered by FAQ: {answered}\nTickets: {total} ({open_n or 0} open)\n"
        f"Rating: {f'{avg:.1f}/5' if avg else '–'} ({rated} ratings)\n\n<b>Top questions</b>\n{top_txt}")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("human", human))
    app.add_handler(CommandHandler("end", end))
    app.add_handler(CommandHandler("tickets", tickets))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message))
    db().close()
    log.info("support bot started (%s, admins=%s)", BUSINESS, ADMIN_IDS or "demo mode")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
