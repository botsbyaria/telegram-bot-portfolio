# 📅 Booking Assistant Bot

Appointment booking for small businesses (salons, clinics, tutors, studios) inside Telegram.

**Live demo:** [@ariabook_demo_bot](https://t.me/ariabook_demo_bot). In demo mode you also see the notification the business owner would receive.

## For customers
- Pick a service, a day and a free time slot with buttons. No typing needed.
- Double bookings are impossible: slots that are taken, or too close to now, are hidden.
- See and cancel your bookings.
- Automatic reminder 1 hour before the appointment.

## For the business
- Instant notification for every new or cancelled booking.
- `/today` and `/upcoming` list the schedule.
- Services, prices, opening hours, closed days, slot length and timezone are all configurable.

## Run it yourself
```bash
pip install -r requirements.txt
export BOT_TOKEN=123456:your-token-from-BotFather
export ADMIN_IDS=111111111          # your Telegram user id (comma-separated for several staff)
export BUSINESS_NAME="Your Salon" TZ_NAME="Europe/Berlin"
python bot.py
```

## Customisation ideas
Google Calendar sync, deposits or payments, multiple staff members, multi-language support, or SMS/email confirmations.
