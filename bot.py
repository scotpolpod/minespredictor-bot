import os
import json
import random
import string
from datetime import datetime, timedelta
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN")
WEBAPP_URL     = os.getenv("WEBAPP_URL")
ADMIN_USERNAME = "rmpl13"
MANAGER_LINK   = "https://t.me/rmpl13"
DATA_FILE      = "data.json"

bot = telebot.TeleBot(BOT_TOKEN)

# ╔══════════════════════════════════════════════════════╗
#   ХРАНИЛИЩЕ ДАННЫХ
# ╚══════════════════════════════════════════════════════╝

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"codes": {}, "users": {}}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_user(data, uid):
    return data["users"].get(str(uid), {})

def is_subscribed(data, uid):
    user = get_user(data, uid)
    end  = user.get("subscription_end")
    if not end:
        return False
    return datetime.now() < datetime.fromisoformat(end)

def days_left(data, uid):
    user = get_user(data, uid)
    end  = user.get("subscription_end")
    if not end:
        return 0
    delta = datetime.fromisoformat(end) - datetime.now()
    return max(0, delta.days)

def is_admin(msg):
    u = msg.from_user.username
    return u and u.lower() == ADMIN_USERNAME.lower()

def gen_code():
    chars = string.ascii_uppercase + string.digits
    return "MP-" + "".join(random.choices(chars, k=8))

# ╔══════════════════════════════════════════════════════╗
#   СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ
# ╚══════════════════════════════════════════════════════╝
user_state    = {}   # uid -> 'choosing_platform' | 'entering_id' | 'entering_code'
user_platform = {}   # uid -> platform

PLANS = [
    {"label": "🎁 7 dni Trial",  "days": 7,  "price": "BEZPŁATNY",  "free": True},
    {"label": "📅 14 dni",       "days": 14, "price": "250 zł",     "free": False},
    {"label": "📅 30 dni",       "days": 30, "price": "399 zł",     "free": False},
    {"label": "📅 60 dni",       "days": 60, "price": "649 zł",     "free": False},
    {"label": "📅 90 dni",       "days": 90, "price": "849 zł",     "free": False},
]

# ╔══════════════════════════════════════════════════════╗
#   КЛАВИАТУРЫ
# ╚══════════════════════════════════════════════════════╝

def kb_platform():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎰 Vavada",     callback_data="pl:Vavada"))
    kb.add(InlineKeyboardButton("🎲 SpinBetter", callback_data="pl:SpinBetter"))
    kb.add(InlineKeyboardButton("💎 SlotsGem",   callback_data="pl:SlotsGem"))
    return kb

def kb_plans():
    kb = InlineKeyboardMarkup()
    for i, p in enumerate(PLANS):
        label = f"{p['label']} — {p['price']}"
        kb.add(InlineKeyboardButton(label, callback_data=f"plan:{i}"))
    kb.add(InlineKeyboardButton("🔑 Mam kod aktywacyjny", callback_data="enter_code"))
    return kb

def kb_open_app(url):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🚀 Otwórz MinesPredictor", web_app=WebAppInfo(url=url)))
    kb.add(InlineKeyboardButton("🔄 Zmień platformę/ID",    callback_data="restart"))
    kb.add(InlineKeyboardButton("📊 Moja subskrypcja",      callback_data="my_sub"))
    return kb

def kb_back():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("◀️ Wróć", callback_data="restart"))
    return kb

# ╔══════════════════════════════════════════════════════╗
#   /start
# ╚══════════════════════════════════════════════════════╝

@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid  = message.from_user.id
    data = load_data()

    user_state.pop(uid, None)
    user_platform.pop(uid, None)

    if is_subscribed(data, uid):
        dl = days_left(data, uid)
        bot.send_message(
            uid,
            f"👋 Witaj z powrotem w <b>MinesPredictor</b>!\n\n"
            f"✅ Subskrypcja aktywna — pozostało <b>{dl} dni</b>\n\n"
            f"📌 Wybierz platformę, aby rozpocząć:",
            parse_mode="HTML",
            reply_markup=kb_platform()
        )
        user_state[uid] = "choosing_platform"
    else:
        bot.send_message(
            uid,
            "👋 Witaj w <b>MinesPredictor</b>!\n\n"
            "🎯 Algorytm przewiduje lokalizację min, kryształów\n"
            "i optymalną strefę uderzenia w Penalty.\n\n"
            "🔒 Aby korzystać z predyktora, aktywuj subskrypcję.\n"
            "Masz kod? Kliknij <b>«Mam kod aktywacyjny»</b>.\n\n"
            "💳 <b>Wybierz plan:</b>",
            parse_mode="HTML",
            reply_markup=kb_plans()
        )

# ╔══════════════════════════════════════════════════════╗
#   ВЫБОР ПЛАНА
# ╚══════════════════════════════════════════════════════╝

@bot.callback_query_handler(func=lambda c: c.data.startswith("plan:"))
def cb_plan(call):
    uid = call.from_user.id
    idx = int(call.data.split(":")[1])
    plan = PLANS[idx]

    if plan["free"]:
        bot.edit_message_text(
            f"🎁 <b>Trial 7 dni — BEZPŁATNY</b>\n\n"
            f"Aby aktywować trial, potrzebujesz kodu promocyjnego.\n"
            f"Skontaktuj się z menedżerem, aby go otrzymać:\n\n"
            f"👤 <a href='{MANAGER_LINK}'>@rmpl13</a>",
            chat_id=uid,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb_back()
        )
    else:
        bot.edit_message_text(
            f"💳 <b>{plan['label']} — {plan['price']}</b>\n\n"
            f"Aby zakupić subskrypcję, skontaktuj się z menedżerem:\n\n"
            f"👤 <a href='{MANAGER_LINK}'>@rmpl13</a>\n\n"
            f"Po opłaceniu otrzymasz <b>kod aktywacyjny</b>.\n"
            f"Wróć i wpisz go klikając «Mam kod aktywacyjny».",
            chat_id=uid,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb_back()
        )
    bot.answer_callback_query(call.id)

# ╔══════════════════════════════════════════════════════╗
#   ВВОД ПРОМОКОДА
# ╚══════════════════════════════════════════════════════╝

@bot.callback_query_handler(func=lambda c: c.data == "enter_code")
def cb_enter_code(call):
    uid = call.from_user.id
    user_state[uid] = "entering_code"
    bot.edit_message_text(
        "🔑 Wpisz swój kod aktywacyjny:",
        chat_id=uid,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb_back()
    )
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=["activate"])
def cmd_activate(message):
    uid  = message.from_user.id
    parts = message.text.strip().split()
    if len(parts) < 2:
        bot.send_message(uid, "Użycie: /activate KOD")
        return
    activate_code(uid, parts[1].upper(), message)

@bot.message_handler(func=lambda m: user_state.get(m.from_user.id) == "entering_code")
def msg_code(message):
    uid  = message.from_user.id
    code = message.text.strip().upper()
    activate_code(uid, code, message)

def activate_code(uid, code, message):
    data = load_data()

    if str(uid) in data["users"] and is_subscribed(data, uid):
        dl = days_left(data, uid)
        bot.send_message(uid, f"✅ Masz już aktywną subskrypcję — pozostało <b>{dl} dni</b>.", parse_mode="HTML")
        return

    codes = data.get("codes", {})
    if code not in codes:
        bot.send_message(uid, "❌ Nieprawidłowy kod. Sprawdź i spróbuj ponownie.", reply_markup=kb_back())
        return

    c = codes[code]
    if c["used"]:
        bot.send_message(uid, "❌ Ten kod został już użyty.", reply_markup=kb_back())
        return

    # Активируем
    days = c["days"]
    end  = (datetime.now() + timedelta(days=days)).isoformat()
    c["used"]    = True
    c["used_by"] = uid
    c["used_at"] = datetime.now().isoformat()

    uname = message.from_user.username or ""
    fname = message.from_user.first_name or ""
    data["users"][str(uid)] = {
        "subscription_end": end,
        "activated_code":   code,
        "username":         uname,
        "first_name":       fname,
        "activated_at":     datetime.now().isoformat()
    }
    save_data(data)

    user_state.pop(uid, None)
    bot.send_message(
        uid,
        f"🎉 Subskrypcja aktywowana!\n\n"
        f"📅 Plan: <b>{days} dni</b>\n"
        f"⏳ Aktywna do: <b>{(datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')}</b>\n\n"
        f"📌 Teraz wybierz platformę:",
        parse_mode="HTML",
        reply_markup=kb_platform()
    )
    user_state[uid] = "choosing_platform"

# ╔══════════════════════════════════════════════════════╗
#   ВЫБОР ПЛАТФОРМЫ → ВВОД ID
# ╚══════════════════════════════════════════════════════╝

@bot.callback_query_handler(func=lambda c: c.data.startswith("pl:"))
def cb_platform(call):
    uid      = call.from_user.id
    data     = load_data()
    platform = call.data.split(":")[1]

    if not is_subscribed(data, uid):
        bot.answer_callback_query(call.id, "🔒 Najpierw aktywuj subskrypcję!", show_alert=True)
        return

    user_platform[uid] = platform
    user_state[uid]    = "entering_id"
    bot.edit_message_text(
        f"✅ Platforma: <b>{platform}</b>\n\n"
        f"🔢 Wpisz swoje <b>ID gracza</b> z {platform}:",
        chat_id=uid,
        message_id=call.message.message_id,
        parse_mode="HTML"
    )
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: user_state.get(m.from_user.id) == "entering_id")
def msg_id(message):
    uid       = message.from_user.id
    player_id = message.text.strip()
    data      = load_data()

    if not is_subscribed(data, uid):
        bot.send_message(uid, "🔒 Twoja subskrypcja wygasła. Użyj /start aby odnowić.")
        return

    if not player_id or len(player_id) < 2:
        bot.send_message(uid, "⚠️ Nieprawidłowe ID. Spróbuj ponownie:")
        return

    platform   = user_platform.get(uid, "Kasyno")
    dl         = days_left(data, uid)
    webapp_url = f"{WEBAPP_URL}?platform={platform}&uid={player_id}"
    user_state[uid] = None

    bot.send_message(
        uid,
        f"🎉 Wszystko gotowe!\n\n"
        f"🏷 Platforma: <b>{platform}</b>\n"
        f"🔢 ID gracza: <b>{player_id}</b>\n"
        f"⏳ Subskrypcja: <b>{dl} dni</b>\n\n"
        f"👇 Otwórz predyktor:",
        parse_mode="HTML",
        reply_markup=kb_open_app(webapp_url)
    )

# ╔══════════════════════════════════════════════════════╗
#   МОЯ ПОДПИСКА
# ╚══════════════════════════════════════════════════════╝

@bot.callback_query_handler(func=lambda c: c.data == "my_sub")
def cb_my_sub(call):
    uid  = call.from_user.id
    data = load_data()
    user = get_user(data, uid)

    if not user or not is_subscribed(data, uid):
        bot.answer_callback_query(call.id, "Brak aktywnej subskrypcji.", show_alert=True)
        return

    end = datetime.fromisoformat(user["subscription_end"]).strftime("%d.%m.%Y")
    dl  = days_left(data, uid)
    code = user.get("activated_code", "—")
    bot.answer_callback_query(call.id)
    bot.send_message(
        uid,
        f"📊 <b>Twoja subskrypcja</b>\n\n"
        f"✅ Status: Aktywna\n"
        f"📅 Wygasa: <b>{end}</b>\n"
        f"⏳ Pozostało: <b>{dl} dni</b>\n"
        f"🔑 Kod: <code>{code}</code>",
        parse_mode="HTML"
    )

# ╔══════════════════════════════════════════════════════╗
#   RESTART
# ╚══════════════════════════════════════════════════════╝

@bot.callback_query_handler(func=lambda c: c.data == "restart")
def cb_restart(call):
    uid = call.from_user.id
    user_state.pop(uid, None)
    user_platform.pop(uid, None)
    bot.delete_message(uid, call.message.message_id)
    cmd_start(call.message)
    bot.answer_callback_query(call.id)

# ╔══════════════════════════════════════════════════════╗
#   АДМИН-ПАНЕЛЬ
# ╚══════════════════════════════════════════════════════╝

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if not is_admin(message):
        return
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Nowy kod",    callback_data="adm:newcode"),
        InlineKeyboardButton("📋 Lista kodów", callback_data="adm:codes"),
        InlineKeyboardButton("👥 Użytkownicy", callback_data="adm:users"),
        InlineKeyboardButton("📊 Statystyki",  callback_data="adm:stats"),
    )
    bot.send_message(message.chat.id, "⚙️ <b>Panel Admina</b>", parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:"))
def cb_admin(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True)
        return

    action = call.data.split(":")[1]
    data   = load_data()

    # ── СТАТИСТИКА ─────────────────────────────────────
    if action == "stats":
        total_users  = len(data["users"])
        active_users = sum(1 for uid in data["users"] if is_subscribed(data, uid))
        total_codes  = len(data["codes"])
        used_codes   = sum(1 for c in data["codes"].values() if c["used"])
        free_codes   = sum(1 for c in data["codes"].values() if not c["used"])

        # Разбивка по дням
        breakdown = {}
        for uid, u in data["users"].items():
            if is_subscribed(data, uid):
                code = u.get("activated_code", "")
                days = data["codes"].get(code, {}).get("days", 0)
                breakdown[days] = breakdown.get(days, 0) + 1

        bd_text = "\n".join(f"  • {d} dni: {n} os." for d, n in sorted(breakdown.items())) or "  —"

        bot.edit_message_text(
            f"📊 <b>Statystyki</b>\n\n"
            f"👥 Wszystkich użytkowników: <b>{total_users}</b>\n"
            f"✅ Aktywnych subskrypcji: <b>{active_users}</b>\n\n"
            f"🔑 Wszystkich kodów: <b>{total_codes}</b>\n"
            f"  ✅ Użytych: <b>{used_codes}</b>\n"
            f"  🟡 Dostępnych: <b>{free_codes}</b>\n\n"
            f"📅 Aktywne wg planu:\n{bd_text}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("◀️ Wróć", callback_data="adm:back")
            )
        )

    # ── СПИСОК ПОЛЬЗОВАТЕЛЕЙ ───────────────────────────
    elif action == "users":
        if not data["users"]:
            text = "👥 Brak użytkowników."
        else:
            lines = []
            for uid, u in list(data["users"].items())[-20:]:  # последние 20
                uname = "@" + u.get("username") if u.get("username") else u.get("first_name", uid)
                if is_subscribed(data, uid):
                    end = datetime.fromisoformat(u["subscription_end"]).strftime("%d.%m.%Y")
                    dl  = days_left(data, uid)
                    lines.append(f"✅ {uname} — до {end} ({dl}d)")
                else:
                    lines.append(f"❌ {uname} — wygasła")
            text = "👥 <b>Użytkownicy (ostatnie 20):</b>\n\n" + "\n".join(lines)

        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("◀️ Wróć", callback_data="adm:back")
            )
        )

    # ── СПИСОК КОДОВ ───────────────────────────────────
    elif action == "codes":
        if not data["codes"]:
            text = "🔑 Brak kodów."
        else:
            lines = []
            for code, c in list(data["codes"].items())[-30:]:  # последние 30
                status = f"✅ użyty przez {c.get('used_by','?')}" if c["used"] else f"🟡 wolny — {c['days']}d"
                lines.append(f"<code>{code}</code> {status}")
            text = "🔑 <b>Kody (ostatnie 30):</b>\n\n" + "\n".join(lines)

        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("◀️ Wróć", callback_data="adm:back")
            )
        )

    # ── СОЗДАТЬ НОВЫЙ КОД ──────────────────────────────
    elif action == "newcode":
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🎁 7 dni",  callback_data="adm:gen:7"),
            InlineKeyboardButton("📅 14 dni", callback_data="adm:gen:14"),
            InlineKeyboardButton("📅 30 dni", callback_data="adm:gen:30"),
            InlineKeyboardButton("📅 60 dni", callback_data="adm:gen:60"),
            InlineKeyboardButton("📅 90 dni", callback_data="adm:gen:90"),
        )
        kb.add(InlineKeyboardButton("◀️ Wróć", callback_data="adm:back"))
        bot.edit_message_text(
            "➕ <b>Wybierz długość kodu:</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb
        )

    # ── ГЕНЕРАЦИЯ КОДА ─────────────────────────────────
    elif action.startswith("gen:"):
        days  = int(action.split(":")[1])
        code  = gen_code()
        while code in data["codes"]:
            code = gen_code()

        data["codes"][code] = {
            "days":       days,
            "used":       False,
            "used_by":    None,
            "used_at":    None,
            "created_at": datetime.now().isoformat()
        }
        save_data(data)

        bot.edit_message_text(
            f"✅ <b>Nowy kod wygenerowany!</b>\n\n"
            f"🔑 Kod: <code>{code}</code>\n"
            f"📅 Ważny: <b>{days} dni</b>\n\n"
            f"Wyślij ten kod użytkownikowi.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("➕ Kolejny kod", callback_data="adm:newcode"),
                InlineKeyboardButton("◀️ Menu",        callback_data="adm:back")
            )
        )

    # ── НАЗАД ──────────────────────────────────────────
    elif action == "back":
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("➕ Nowy kod",    callback_data="adm:newcode"),
            InlineKeyboardButton("📋 Lista kodów", callback_data="adm:codes"),
            InlineKeyboardButton("👥 Użytkownicy", callback_data="adm:users"),
            InlineKeyboardButton("📊 Statystyki",  callback_data="adm:stats"),
        )
        bot.edit_message_text(
            "⚙️ <b>Panel Admina</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb
        )

    bot.answer_callback_query(call.id)

# ╔══════════════════════════════════════════════════════╗
#   /help
# ╚══════════════════════════════════════════════════════╝

@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(
        message.chat.id,
        "ℹ️ <b>Pomoc MinesPredictor</b>\n\n"
        "• /start — uruchom bota\n"
        "• /activate KOD — aktywuj subskrypcję kodem\n"
        "• /help — ta wiadomość\n\n"
        "Aby zakupić subskrypcję: @rmpl13",
        parse_mode="HTML"
    )

# ╔══════════════════════════════════════════════════════╗
#   ЗАПУСК
# ╚══════════════════════════════════════════════════════╝

if __name__ == "__main__":
    print("MinesPredictor bot uruchomiony...")
    bot.infinity_polling()
