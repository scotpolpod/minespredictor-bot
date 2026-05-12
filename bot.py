import os
import json
import random
import string
import threading
import time
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

# ── REDIS (опционально) ───────────────────────────────────
_redis = None
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("REDIS_PRIVATE_URL")
if REDIS_URL:
    try:
        import redis as _redis_lib
        _redis = _redis_lib.from_url(REDIS_URL, decode_responses=True)
        _redis.ping()
        print("Redis połączony.")
    except Exception as _e:
        print(f"Redis błąd połączenia: {_e}")
        _redis = None

PLANS = [
    {"label": "🎁 7 dni Trial",  "days": 7,  "price": "BEZPŁATNY"},
    {"label": "📅 14 dni",       "days": 14, "price": "250 zł"},
    {"label": "📅 30 dni",       "days": 30, "price": "399 zł"},
    {"label": "📅 60 dni",       "days": 60, "price": "649 zł"},
    {"label": "📅 90 dni",       "days": 90, "price": "849 zł"},
]

user_state = {}  # uid -> 'entering_id' | 'entering_code' | 'entering_broadcast'

# Времена авто-пушей (HH:MM, по UTC+2 — меняй под нужный TZ)
PUSH_TIMES = ["07:00", "12:00", "17:00"]

PUSH_MESSAGES = [
    "🔄 <b>Algorytm zaktualizowany!</b>\n\nDzisiejsze sygnały są gotowe — sprawdź przewidywania i zacznij wygrywać 💎",
    "⚡ <b>Uwaga!</b>\n\nDziś algorytm wykrył wyjątkowo wysoką skuteczność predykcji. Nie przegap okazji — sygnały czekają 🎯",
    "🌙 <b>Wieczorna sesja startuje!</b>\n\nAlgorytm przeanalizował wzorce — Twoje sygnały są gotowe 📡",
    "💰 <b>Nasi gracze dziś już zbierają!</b>\n\nAlgorytm pracuje na pełnych obrotach — Twoje sygnały czekają 🚀",
    "👋 <b>Hej!</b>\n\nAlgorytm non-stop analizuje wzorce i ma dla Ciebie gotowe sygnały na dziś 💎 Wejdź teraz",
]

# ── DATA ──────────────────────────────────────────────────

def load_data():
    if _redis:
        try:
            raw = _redis.get("bot_data")
            if raw:
                return json.loads(raw)
        except Exception as e:
            print(f"Redis load error: {e}")
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"codes": {}, "users": {}}

def save_data(data):
    if _redis:
        try:
            _redis.set("bot_data", json.dumps(data, ensure_ascii=False))
        except Exception as e:
            print(f"Redis save error: {e}")
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def is_subscribed(data, uid):
    user = data["users"].get(str(uid), {})
    end  = user.get("subscription_end")
    if not end:
        return False
    return datetime.now() < datetime.fromisoformat(end)

def days_left(data, uid):
    user = data["users"].get(str(uid), {})
    end  = user.get("subscription_end")
    if not end:
        return 0
    return max(0, (datetime.fromisoformat(end) - datetime.now()).days)

def is_admin(obj):
    u = obj.from_user.username
    return u and u.lower() == ADMIN_USERNAME.lower()

def gen_code():
    return "MP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

# ── BROADCAST ─────────────────────────────────────────────

def send_push_to_all(text):
    data  = load_data()
    users = data.get("users", {})
    sent  = 0
    for uid_str in list(users.keys()):
        if not is_subscribed(data, uid_str):
            continue
        try:
            bot.send_message(int(uid_str), text, parse_mode="HTML")
            sent += 1
            time.sleep(0.05)   # защита от flood
        except Exception as e:
            print(f"Push error uid={uid_str}: {e}")
    print(f"Push wysłany: {sent} użytkowników.")
    return sent

def push_scheduler():
    sent_today = {}
    while True:
        try:
            now  = datetime.now()
            hm   = now.strftime("%H:%M")
            date = now.strftime("%Y-%m-%d")
            if sent_today.get("_date") != date:
                sent_today = {"_date": date}
            for push_time in PUSH_TIMES:
                if hm == push_time and push_time not in sent_today:
                    sent_today[push_time] = True
                    msg = random.choice(PUSH_MESSAGES)
                    threading.Thread(target=send_push_to_all, args=(msg,), daemon=True).start()
        except Exception as e:
            print(f"Scheduler error: {e}")
        time.sleep(60)

# ── KEYBOARDS ─────────────────────────────────────────────

def kb_plans():
    kb = InlineKeyboardMarkup()
    for i, p in enumerate(PLANS):
        kb.add(InlineKeyboardButton(f"{p['label']} — {p['price']}", callback_data=f"plan_{i}"))
    kb.add(InlineKeyboardButton("🔑 Mam kod aktywacyjny", callback_data="enter_code"))
    return kb

def kb_open_app(url):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🚀 Otwórz MinesPredictor", web_app=WebAppInfo(url=url)))
    kb.add(InlineKeyboardButton("🔄 Zmień ID",          callback_data="change_id"))
    kb.add(InlineKeyboardButton("📊 Moja subskrypcja",  callback_data="my_sub"))
    return kb

def kb_back_plans():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("◀️ Wróć do planów", callback_data="back_plans"))
    return kb

def kb_admin_main():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Nowy kod",    callback_data="adm_newcode"),
        InlineKeyboardButton("📋 Lista kodów", callback_data="adm_codes"),
        InlineKeyboardButton("👥 Użytkownicy", callback_data="adm_users"),
        InlineKeyboardButton("📊 Statystyki",  callback_data="adm_stats"),
    )
    kb.add(InlineKeyboardButton("📢 Wyślij push", callback_data="adm_push"))
    return kb

# ── /start ────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid  = message.from_user.id
    data = load_data()
    user_state.pop(uid, None)

    if is_subscribed(data, uid):
        dl        = days_left(data, uid)
        player_id = data["users"].get(str(uid), {}).get("player_id", "")
        if player_id:
            url = f"{WEBAPP_URL}?uid={player_id}&days={dl}&v=5"
            bot.send_message(uid,
                f"👋 Witaj w <b>MinesPredictor</b>!\n\n"
                f"✅ Subskrypcja aktywna — pozostało <b>{dl} dni</b>\n\n"
                f"👇 Otwórz predyktor:",
                parse_mode="HTML",
                reply_markup=kb_open_app(url))
        else:
            user_state[uid] = "entering_id"
            bot.send_message(uid,
                f"👋 Witaj w <b>MinesPredictor</b>!\n\n"
                f"✅ Subskrypcja aktywna — pozostało <b>{dl} dni</b>\n\n"
                f"🔢 Wpisz swoje <b>ID gracza</b> z kasyna:",
                parse_mode="HTML")
    else:
        bot.send_message(uid,
            "👋 Witaj w <b>MinesPredictor</b>!\n\n"
            "🎯 Algorytm przewiduje miny, kryształy i strefy penalty.\n\n"
            "🔒 Aby korzystać — aktywuj subskrypcję.\n"
            "Masz kod? Kliknij <b>«Mam kod aktywacyjny»</b>.\n\n"
            "💳 <b>Wybierz plan:</b>",
            parse_mode="HTML",
            reply_markup=kb_plans())

# ── PLAN SELECTION ────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("plan_"))
def cb_plan(call):
    try:
        idx  = int(call.data.split("_")[1])
        plan = PLANS[idx]
        bot.edit_message_text(
            f"💳 <b>{plan['label']} — {plan['price']}</b>\n\n"
            f"Skontaktuj się z menedżerem w celu zakupu/aktywacji:\n\n"
            f"👤 <a href='{MANAGER_LINK}'>@rmpl13</a>\n\n"
            f"Po otrzymaniu kodu wróć i kliknij\n<b>«Mam kod aktywacyjny»</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb_back_plans(),
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"cb_plan error: {e}")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "back_plans")
def cb_back_plans(call):
    try:
        bot.edit_message_text(
            "💳 <b>Wybierz plan:</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb_plans()
        )
    except Exception as e:
        print(f"cb_back_plans error: {e}")
    bot.answer_callback_query(call.id)

# ── CODE ACTIVATION ───────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "enter_code")
def cb_enter_code(call):
    uid = call.from_user.id
    user_state[uid] = "entering_code"
    try:
        bot.edit_message_text(
            "🔑 Wpisz swój kod aktywacyjny:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb_back_plans()
        )
    except Exception as e:
        print(f"cb_enter_code error: {e}")
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=["activate"])
def cmd_activate(message):
    parts = message.text.strip().split()
    code  = parts[1].upper() if len(parts) > 1 else ""
    process_code(message.from_user.id, code, message.chat.id)

@bot.message_handler(func=lambda m: user_state.get(m.from_user.id) == "entering_code")
def msg_code(message):
    u = message.from_user
    process_code(u.id, message.text.strip().upper(), message.chat.id,
                 username=u.username or "", first_name=u.first_name or "")

def process_code(uid, code, chat_id, username="", first_name=""):
    data = load_data()

    if is_subscribed(data, uid):
        dl        = days_left(data, uid)
        player_id = data["users"].get(str(uid), {}).get("player_id", "")
        if player_id:
            url = f"{WEBAPP_URL}?uid={player_id}&days={dl}&v=5"
            bot.send_message(chat_id,
                f"✅ Masz już aktywną subskrypcję — pozostało <b>{dl} dni</b>.",
                parse_mode="HTML", reply_markup=kb_open_app(url))
        else:
            bot.send_message(chat_id,
                f"✅ Masz już aktywną subskrypcję — pozostało <b>{dl} dni</b>.", parse_mode="HTML")
        return

    codes = data.get("codes", {})
    if code not in codes:
        bot.send_message(chat_id, "❌ Nieprawidłowy kod. Sprawdź i spróbuj ponownie.", reply_markup=kb_back_plans())
        return

    c = codes[code]
    if c["used"]:
        bot.send_message(chat_id, "❌ Ten kod został już użyty.", reply_markup=kb_back_plans())
        return

    days     = c["days"]
    end      = (datetime.now() + timedelta(days=days)).isoformat()
    c["used"]    = True
    c["used_by"] = uid
    c["used_at"] = datetime.now().isoformat()

    existing  = data["users"].get(str(uid), {})
    player_id = existing.get("player_id", "")

    data["users"][str(uid)] = {
        "subscription_end": end,
        "activated_code":   code,
        "activated_at":     datetime.now().isoformat(),
        "username":         username or existing.get("username", ""),
        "first_name":       first_name or existing.get("first_name", ""),
        "player_id":        player_id,
        "tg_id":            uid
    }
    save_data(data)

    exp_str = (datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')

    if player_id:
        dl  = days_left(data, uid)
        url = f"{WEBAPP_URL}?uid={player_id}&days={dl}&v=5"
        user_state[uid] = None
        bot.send_message(chat_id,
            f"🎉 Subskrypcja aktywowana!\n\n"
            f"📅 Plan: <b>{days} dni</b>\n"
            f"⏳ Aktywna do: <b>{exp_str}</b>\n\n"
            f"👇 Otwórz predyktor:",
            parse_mode="HTML", reply_markup=kb_open_app(url))
    else:
        user_state[uid] = "entering_id"
        bot.send_message(chat_id,
            f"🎉 Subskrypcja aktywowana!\n\n"
            f"📅 Plan: <b>{days} dni</b>\n"
            f"⏳ Aktywna do: <b>{exp_str}</b>\n\n"
            f"🔢 Wpisz swoje <b>ID gracza</b> z kasyna:",
            parse_mode="HTML")

# ── ID ENTRY ──────────────────────────────────────────────

@bot.message_handler(func=lambda m: user_state.get(m.from_user.id) == "entering_id")
def msg_id(message):
    uid       = message.from_user.id
    player_id = message.text.strip()
    data      = load_data()

    if not is_subscribed(data, uid):
        bot.send_message(uid, "🔒 Twoja subskrypcja wygasła. Użyj /start aby odnowić.")
        user_state.pop(uid, None)
        return

    if not player_id or len(player_id) < 2:
        bot.send_message(uid, "⚠️ Nieprawidłowe ID. Spróbuj ponownie:")
        return

    # Zapisz player_id + dane użytkownika
    if str(uid) in data["users"]:
        data["users"][str(uid)]["player_id"]  = player_id
        data["users"][str(uid)]["tg_id"]      = uid
        data["users"][str(uid)]["username"]   = message.from_user.username or data["users"][str(uid)].get("username", "")
        data["users"][str(uid)]["first_name"] = message.from_user.first_name or data["users"][str(uid)].get("first_name", "")
        save_data(data)

    dl  = days_left(data, uid)
    url = f"{WEBAPP_URL}?uid={player_id}&days={dl}&v=5"
    user_state[uid] = None

    bot.send_message(uid,
        f"✅ ID: <b>{player_id}</b>\n"
        f"⏳ Subskrypcja: <b>{dl} dni</b>\n\n"
        f"👇 Otwórz predyktor:",
        parse_mode="HTML",
        reply_markup=kb_open_app(url))


@bot.message_handler(content_types=['web_app_data'])
def handle_webapp_data(message):
    uid = message.from_user.id
    try:
        payload = json.loads(message.web_app_data.data)
    except Exception as e:
        print(f"webapp_data parse error: {e}")
        return
    if payload.get('type') == 'activate_code':
        code = payload.get('code', '').strip().upper()
        if code:
            u = message.from_user
            process_code(uid, code, message.chat.id,
                         username=u.username or "", first_name=u.first_name or "")

@bot.callback_query_handler(func=lambda c: c.data == "change_id")
def cb_change_id(call):
    uid = call.from_user.id
    data = load_data()
    if not is_subscribed(data, uid):
        bot.answer_callback_query(call.id, "🔒 Subskrypcja wygasła.", show_alert=True)
        return
    user_state[uid] = "entering_id"
    try:
        bot.send_message(uid, "🔢 Wpisz nowe <b>ID gracza</b>:", parse_mode="HTML")
    except Exception as e:
        print(f"cb_change_id error: {e}")
    bot.answer_callback_query(call.id)

# ── MY SUB ────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "my_sub")
def cb_my_sub(call):
    uid  = call.from_user.id
    data = load_data()
    user = data["users"].get(str(uid), {})

    if not is_subscribed(data, uid):
        bot.answer_callback_query(call.id, "Brak aktywnej subskrypcji.", show_alert=True)
        return

    end  = datetime.fromisoformat(user["subscription_end"]).strftime("%d.%m.%Y")
    dl   = days_left(data, uid)
    code = user.get("activated_code", "—")
    bot.answer_callback_query(call.id)
    bot.send_message(uid,
        f"📊 <b>Twoja subskrypcja</b>\n\n"
        f"✅ Status: Aktywna\n"
        f"📅 Wygasa: <b>{end}</b>\n"
        f"⏳ Pozostało: <b>{dl} dni</b>\n"
        f"🔑 Kod: <code>{code}</code>",
        parse_mode="HTML")

# ── ADMIN ─────────────────────────────────────────────────

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if not is_admin(message):
        return
    bot.send_message(message.chat.id, "⚙️ <b>Panel Admina</b>",
        parse_mode="HTML", reply_markup=kb_admin_main())

@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def cb_adm_stats(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    data = load_data()
    total  = len(data["users"])
    active = sum(1 for u in data["users"] if is_subscribed(data, u))
    codes  = len(data["codes"])
    used   = sum(1 for c in data["codes"].values() if c["used"])
    free   = codes - used
    breakdown = {}
    for uid in data["users"]:
        if is_subscribed(data, uid):
            code = data["users"][uid].get("activated_code","")
            days = data["codes"].get(code,{}).get("days",0)
            breakdown[days] = breakdown.get(days,0) + 1
    bd = "\n".join(f"  • {d} dni: {n}" for d,n in sorted(breakdown.items())) or "  —"
    bot.edit_message_text(
        f"📊 <b>Statystyki</b>\n\n"
        f"👥 Użytkownicy: <b>{total}</b>\n"
        f"✅ Aktywne sub: <b>{active}</b>\n\n"
        f"🔑 Kody łącznie: <b>{codes}</b>\n"
        f"  ✅ Użyte: <b>{used}</b>\n"
        f"  🟡 Wolne: <b>{free}</b>\n\n"
        f"📅 Aktywne wg planu:\n{bd}",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("◀️ Wróć", callback_data="adm_back"))
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "adm_users")
def cb_adm_users(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    data = load_data()
    if not data["users"]:
        text = "👥 Brak użytkowników."
    else:
        lines = []
        for uid, u in list(data["users"].items())[-20:]:
            # Telegram identity
            if u.get("username"):
                tg_name = f"@{u['username']}"
            elif u.get("first_name"):
                tg_name = u["first_name"]
            else:
                tg_name = f"id{uid}"
            tg_id     = u.get("tg_id") or uid
            player_id = u.get("player_id") or "—"

            if is_subscribed(data, uid):
                end    = datetime.fromisoformat(u["subscription_end"]).strftime("%d.%m.%y")
                status = f"✅ до {end} ({days_left(data,uid)}d)"
            else:
                status = "❌ wygasła"

            lines.append(
                f"<b>{tg_name}</b> <code>{tg_id}</code>\n"
                f"🎰 <code>{player_id}</code> | {status}"
            )
        text = f"👥 <b>Użytkownicy ({len(data['users'])}):</b>\n\n" + "\n\n".join(lines)
    bot.edit_message_text(text,
        chat_id=call.message.chat.id, message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("◀️ Wróć", callback_data="adm_back")))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "adm_codes")
def cb_adm_codes(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    data = load_data()
    if not data["codes"]:
        text = "🔑 Brak kodów."
    else:
        lines = []
        for code, c in list(data["codes"].items())[-30:]:
            status = f"✅ użyty" if c["used"] else f"🟡 wolny"
            lines.append(f"<code>{code}</code> — {c['days']}d {status}")
        text = "🔑 <b>Kody:</b>\n\n" + "\n".join(lines)
    bot.edit_message_text(text,
        chat_id=call.message.chat.id, message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("◀️ Wróć", callback_data="adm_back")))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "adm_newcode")
def cb_adm_newcode(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("7 dni",  callback_data="adm_gen_7"),
        InlineKeyboardButton("14 dni", callback_data="adm_gen_14"),
        InlineKeyboardButton("30 dni", callback_data="adm_gen_30"),
        InlineKeyboardButton("60 dni", callback_data="adm_gen_60"),
        InlineKeyboardButton("90 dni", callback_data="adm_gen_90"),
    )
    kb.add(InlineKeyboardButton("◀️ Wróć", callback_data="adm_back"))
    bot.edit_message_text("➕ <b>Wybierz długość kodu:</b>",
        chat_id=call.message.chat.id, message_id=call.message.message_id,
        parse_mode="HTML", reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_gen_"))
def cb_adm_gen(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    days = int(call.data.replace("adm_gen_", ""))
    data = load_data()
    code = gen_code()
    while code in data["codes"]:
        code = gen_code()
    data["codes"][code] = {
        "days": days, "used": False,
        "used_by": None, "used_at": None,
        "created_at": datetime.now().isoformat()
    }
    save_data(data)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Kolejny kod", callback_data="adm_newcode"),
        InlineKeyboardButton("◀️ Menu",        callback_data="adm_back")
    )
    bot.edit_message_text(
        f"✅ <b>Kod wygenerowany!</b>\n\n"
        f"🔑 <code>{code}</code>\n"
        f"📅 Ważny: <b>{days} dni</b>",
        chat_id=call.message.chat.id, message_id=call.message.message_id,
        parse_mode="HTML", reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "adm_back")
def cb_adm_back(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    try:
        bot.edit_message_text("⚙️ <b>Panel Admina</b>",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            parse_mode="HTML", reply_markup=kb_admin_main())
    except Exception as e:
        print(f"cb_adm_back error: {e}")
    bot.answer_callback_query(call.id)

# ── ADMIN PUSH ────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "adm_push")
def cb_adm_push(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    kb = InlineKeyboardMarkup()
    for i, msg in enumerate(PUSH_MESSAGES):
        preview = msg.replace("<b>","").replace("</b>","")[:45] + "..."
        kb.add(InlineKeyboardButton(preview, callback_data=f"adm_ps_{i}"))
    kb.add(InlineKeyboardButton("✏️ Własna wiadomość", callback_data="adm_push_custom"))
    kb.add(InlineKeyboardButton("◀️ Wróć", callback_data="adm_back"))
    try:
        bot.edit_message_text("📢 <b>Wybierz wiadomość push:</b>",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        print(f"cb_adm_push error: {e}")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ps_"))
def cb_adm_push_send(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    try:
        idx = int(call.data.replace("adm_ps_", ""))
        msg = PUSH_MESSAGES[idx]
    except Exception as e:
        bot.answer_callback_query(call.id, "Błąd.", show_alert=True); return
    threading.Thread(target=send_push_to_all, args=(msg,), daemon=True).start()
    try:
        bot.edit_message_text("📢 <b>Push wysyłany!</b>\n\nWiadomość dotrze do wszystkich aktywnych subskrybentów.",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("◀️ Menu", callback_data="adm_back")))
    except Exception as e:
        print(f"cb_adm_push_send error: {e}")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "adm_push_custom")
def cb_adm_push_custom(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    user_state[call.from_user.id] = "entering_broadcast"
    try:
        bot.edit_message_text(
            "✏️ <b>Wpisz treść wiadomości:</b>\n\nObsługuje HTML: &lt;b&gt;bold&lt;/b&gt;, &lt;i&gt;italic&lt;/i&gt;",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("◀️ Anuluj", callback_data="adm_back")))
    except Exception as e:
        print(f"cb_adm_push_custom error: {e}")
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: user_state.get(m.from_user.id) == "entering_broadcast")
def msg_broadcast(message):
    if not is_admin(message):
        return
    uid  = message.from_user.id
    text = message.text.strip()
    user_state.pop(uid, None)
    if not text:
        return
    _do_broadcast(uid, text)

@bot.message_handler(commands=["send"])
def cmd_send(message):
    if not is_admin(message):
        return
    text = message.text.partition(" ")[2].strip()
    if not text:
        bot.send_message(message.chat.id, "⚠️ Użycie: /send Twoja wiadomość tutaj")
        return
    _do_broadcast(message.from_user.id, text)

def _do_broadcast(uid, text):
    bot.send_message(uid, "📢 <b>Wysyłam...</b>", parse_mode="HTML")
    def do_send():
        try:
            sent = send_push_to_all(text)
            bot.send_message(uid, f"✅ Wysłano do <b>{sent}</b> subskrybentów.", parse_mode="HTML")
        except Exception as e:
            bot.send_message(uid, f"❌ Błąd: {e}", parse_mode="HTML")
    threading.Thread(target=do_send, daemon=True).start()

# ── /help ─────────────────────────────────────────────────

@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(message.chat.id,
        "ℹ️ <b>Pomoc MinesPredictor</b>\n\n"
        "• /start — uruchom bota\n"
        "• /activate KOD — aktywuj subskrypcję\n"
        "• /help — ta wiadomość\n\n"
        "Zakup subskrypcji: @rmpl13",
        parse_mode="HTML")

# ── RUN ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("MinesPredictor bot uruchomiony...")
    threading.Thread(target=push_scheduler, daemon=True).start()
    print("Scheduler uruchomiony.")

    try:
        bot.delete_webhook(drop_pending_updates=True)
        print("Webhook usunięty.")
    except Exception as e:
        print(f"delete_webhook error: {e}")

    time.sleep(3)

    while True:
        try:
            print("Rozpoczynam polling...")
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=20,
                skip_pending=True,
                logger_level=None,
                restart_on_change=False
            )
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(10)
            print("Restartuję polling...")
