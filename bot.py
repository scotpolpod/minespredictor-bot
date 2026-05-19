import os
import json
import random
import string
import threading
import time
from datetime import datetime, timedelta
import telebot
import requests as _requests
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN")
WEBAPP_URL     = os.getenv("WEBAPP_URL")
ADMIN_USERNAME = "rmpl13"
MANAGER_LINK   = "https://t.me/rmpl13"
DATA_FILE      = "data.json"
VIP_USERNAMES  = ["iiiiiigggggg", "S_V_V1"]

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

# ── SPINBETTER VERIFICATION ───────────────────────────────
SPINBETTER_TOKEN    = os.getenv("SPINBETTER_TOKEN", "")
SPINBETTER_USER_ID  = os.getenv("SPINBETTER_USER_ID", "")
SPINBETTER_CUSTOMER = "casinoz"
SPINBETTER_CACHE_KEY = "spinbetter_players"
SPINBETTER_REFRESH  = 180  # секунд (3 минуты)

# in-memory fallback если Redis недоступен
# {player_id: True/False} где True = есть депозит
_sb_players_cache = {}

def _parse_amount(raw):
    """Парсит сумму вида '$13.70' или '13.70' → float."""
    try:
        return float(str(raw).replace("$", "").replace(",", ".").strip())
    except Exception:
        return 0.0

def _do_fetch(metrics):
    """Выполняет один запрос к SpinBetter API с заданными метриками."""
    payload = {
        "dimensions": ["site_player_id"],
        "filters": {},
        "from": "2020-01-01 00:00:00",
        "to":   datetime.now().strftime("%Y-%m-%d 23:59:59"),
        "having": {},
        "limit": 1000,
        "metrics": metrics,
        "metrics_format": "pretty",
        "offset": 0,
        "search": {},
        "sorting": [{"sort_by": "site_player_id", "sort_dir": "desc"}],
    }
    headers = {
        "Authorization": SPINBETTER_TOKEN,
        "Content-Type":  "application/json",
        "X-Customer-Id": SPINBETTER_CUSTOMER,
        "X-User-Id":     SPINBETTER_USER_ID,
        "Origin":        "https://panel.spinbetterpartners.com",
        "Referer":       "https://panel.spinbetterpartners.com/",
    }
    resp = _requests.post(
        "https://affiliatecontrol-api.com/affiliates/reports",
        json=payload, headers=headers, timeout=20
    )
    print(f"SpinBetter API status: {resp.status_code}")
    if not resp.ok:
        print(f"SpinBetter API error body: {resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()

def fetch_spinbetter_players():
    """Загружает игроков из SpinBetter. Возвращает dict {pid: total_dep_usd}."""
    try:
        # Пробуем с deposits_sum (все депозиты), fallback на deposits_first_sum
        try:
            data = _do_fetch([
                "registrations_count",
                "deposits_first_count",
                "deposits_first_sum",
                "deposits_sum",
            ])
            has_total = True
        except Exception as e:
            print(f"SpinBetter fetch with deposits_sum failed ({e}), retrying without it...")
            data = _do_fetch([
                "registrations_count",
                "deposits_first_count",
                "deposits_first_sum",
            ])
            has_total = False

        players = {}
        for item in data.get("payload", {}).get("data", []):
            pid = item.get("site_player_id")
            if not pid or pid == "Незарегистрированный":
                continue
            if has_total:
                total = _parse_amount(item.get("deposits_sum", "$0.00"))
                if total == 0.0:
                    total = _parse_amount(item.get("deposits_first_sum", "$0.00"))
            else:
                total = _parse_amount(item.get("deposits_first_sum", "$0.00"))
            players[str(pid)] = total
        print(f"SpinBetter: {len(players)} players loaded (total={'yes' if has_total else 'FD only'}).")
        return players
    except Exception as e:
        print(f"SpinBetter fetch error: {e}")
        return None


def refresh_sb_cache():
    global _sb_players_cache
    players = fetch_spinbetter_players()
    if players is None:
        return
    _sb_players_cache = players
    if _redis:
        try:
            _redis.set(SPINBETTER_CACHE_KEY, json.dumps(players))
        except Exception as e:
            print(f"SpinBetter Redis save error: {e}")

MIN_DEPOSIT = 10.0  # ~40 zł в USD (API SpinBetter zwraca wartości w dolarach)

def check_player(player_id):
    """Returns: 'not_found' | ('no_deposit', amount_usd) | 'ok'"""
    if not SPINBETTER_TOKEN:
        return "ok"
    pid = str(player_id).strip()
    players = _sb_players_cache
    if _redis:
        try:
            raw = _redis.get(SPINBETTER_CACHE_KEY)
            if raw:
                players = json.loads(raw)
        except Exception:
            pass
    if pid not in players:
        return "not_found"
    try:
        dep = float(players[pid])
    except (TypeError, ValueError):
        dep = 0.0
    if dep >= MIN_DEPOSIT:
        return "ok"
    return ("no_deposit", dep)

def sb_scheduler():
    """Updates SpinBetter cache every 3 minutes."""
    while True:
        try:
            refresh_sb_cache()
        except Exception as e:
            print(f"sb_scheduler error: {e}")
        time.sleep(SPINBETTER_REFRESH)

PLANS = [
    {"label": "🎁 7 dni Trial",  "days": 7,  "price": "BEZPŁATNY"},
    {"label": "📅 14 dni",       "days": 14, "price": "250 zł"},
    {"label": "📅 30 dni",       "days": 30, "price": "399 zł"},
    {"label": "📅 60 dni",       "days": 60, "price": "649 zł"},
    {"label": "📅 90 dni",       "days": 90, "price": "849 zł"},
]

user_state = {}  # uid -> 'entering_id' | 'entering_code' | 'entering_broadcast'

# Времена авто-пушей (HH:MM, по UTC+2 — меняй под нужный TZ)
PUSH_TIMES = ["10:00"]  # 10:00 UTC = 12:00 czasu polskiego (UTC+2)

INACTIVE_DAYS   = 3          # через сколько дней без активности слать пуш
INACTIVE_CHECK  = "09:00"    # 09:00 UTC = 11:00 czasu polskiego (UTC+2)

INACTIVE_MESSAGE = (
    "⚡ <b>Dawno Cię nie było!</b>\n\n"
    "Przez ostatnie dni algorytm zebrał nowe dane i znacznie poprawił "
    "dokładność predykcji.\n\n"
    "Twoje sygnały czekają — wejdź teraz i sprawdź 🎯"
)

PUSH_MESSAGES = [
    "🔄 <b>Algorytm zaktualizowany!</b>\n\nDzisiejsze sygnały są gotowe — sprawdź przewidywania i zacznij wygrywać 💎",
    "⚡ <b>Uwaga!</b>\n\nDziś algorytm wykrył wyjątkowo wysoką skuteczność predykcji. Nie przegap okazji — sygnały czekają 🎯",
    "☀️ <b>Dzień dobry!</b>\n\nAlgorytm przeanalizował wzorce — Twoje sygnały na dziś są gotowe 📡",
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
    diff = datetime.fromisoformat(end) - datetime.now()
    if diff.total_seconds() <= 0:
        return 0
    return max(1, diff.days)  # минимум 1 пока подписка активна

def is_admin(obj):
    u = obj.from_user.username
    return u and u.lower() == ADMIN_USERNAME.lower()

def is_vip(username):
    if not username:
        return False
    return username.lower() in [v.lower() for v in VIP_USERNAMES]

def build_url(player_id, dl, username=""):
    vip = "&vip=1" if is_vip(username) else ""
    return f"{WEBAPP_URL}?uid={player_id}&days={dl}&v=6{vip}"

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

def send_inactive_push():
    data  = load_data()
    now   = datetime.now()
    sent  = 0
    today = now.strftime("%Y-%m-%d")
    for uid_str, user in list(data["users"].items()):
        if not is_subscribed(data, uid_str):
            continue
        # не слать чаще раза в 3 дня
        last_push = user.get("inactive_push_date")
        if last_push and (now - datetime.fromisoformat(last_push)).days < 3:
            continue
        last = user.get("last_activity")
        if not last:
            continue
        diff = (now - datetime.fromisoformat(last)).days
        if diff < INACTIVE_DAYS:
            continue
        player_id = user.get("player_id", "")
        dl        = days_left(data, uid_str)
        username  = user.get("username", "")
        try:
            kb = InlineKeyboardMarkup()
            if player_id:
                url = build_url(player_id, dl, username)
                kb.add(InlineKeyboardButton("🚀 Otwórz MinesPredictor", web_app=WebAppInfo(url=url)))
            bot.send_message(int(uid_str), INACTIVE_MESSAGE, parse_mode="HTML",
                             reply_markup=kb if player_id else None)
            user["inactive_push_date"] = now.isoformat()
            sent += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"Inactive push error uid={uid_str}: {e}")
    if sent:
        save_data(data)
    print(f"Inactive push wysłany: {sent} użytkowników.")

def inactive_scheduler():
    sent_today = {}
    while True:
        try:
            now  = datetime.now()
            hm   = now.strftime("%H:%M")
            date = now.strftime("%Y-%m-%d")
            if sent_today.get("_date") != date:
                sent_today = {"_date": date}
            if hm == INACTIVE_CHECK and "done" not in sent_today:
                sent_today["done"] = True
                threading.Thread(target=send_inactive_push, daemon=True).start()
        except Exception as e:
            print(f"Inactive scheduler error: {e}")
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

def touch_activity(uid, data):
    """Обновляет last_activity юзера."""
    if str(uid) in data["users"]:
        data["users"][str(uid)]["last_activity"] = datetime.now().isoformat()

@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid  = message.from_user.id
    data = load_data()
    touch_activity(uid, data)
    save_data(data)
    user_state.pop(uid, None)

    if is_subscribed(data, uid):
        dl        = days_left(data, uid)
        player_id = data["users"].get(str(uid), {}).get("player_id", "")
        if player_id:
            url = build_url(player_id, dl, message.from_user.username if hasattr(message, 'from_user') else "")
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
        # Запоминаем время первого /start для автосообщения через 10 минут
        data = load_data()
        user = data["users"].setdefault(str(uid), {})
        if not user.get("start_at"):
            user["start_at"] = datetime.now().isoformat()
            user["tg_id"]    = uid
            user["username"]  = message.from_user.username or ""
            user["first_name"] = message.from_user.first_name or ""
            save_data(data)
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
            url = build_url(player_id, dl, username)
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
        "last_activity":    datetime.now().isoformat(),
        "username":         username or existing.get("username", ""),
        "first_name":       first_name or existing.get("first_name", ""),
        "player_id":        player_id,
        "tg_id":            uid
    }
    save_data(data)

    exp_str = (datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')

    if player_id:
        dl  = days_left(data, uid)
        url = build_url(player_id, dl, username)
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

    # Всегда делаем свежий запрос к SpinBetter при вводе ID
    # (избегаем ситуации когда кэш устарел и депозит ещё не подтянулся)
    refresh_sb_cache()
    sb_status = check_player(player_id)

    if sb_status == "not_found":
        bot.send_message(uid,
            "❌ <b>Nie znaleziono takiego ID.</b>\n\n"
            "Sprawdź czy wpisałeś poprawny numer — możesz go znaleźć w swoim profilu SpinBetter.\n\n"
            "Jeśli właśnie założyłeś konto — poczekaj chwilę i spróbuj ponownie 🔄",
            parse_mode="HTML")
        return
    if isinstance(sb_status, tuple) and sb_status[0] == "no_deposit":
        dep_usd  = sb_status[1]
        dep_pln  = round(dep_usd * 4.0)   # примерный курс $1 ≈ 4 zł
        need_usd = MIN_DEPOSIT - dep_usd
        need_pln = round(need_usd * 4.0)
        if dep_usd > 0:
            bot.send_message(uid,
                f"✅ <b>Rejestracja potwierdzona!</b>\n\n"
                f"💰 Twój aktualny depozyt: <b>${dep_usd:.2f}</b> (~{dep_pln} zł)\n"
                f"🎯 Wymagane minimum: <b>~40 zł</b>\n\n"
                f"Brakuje Ci jeszcze <b>~{need_pln} zł</b> — dokonaj dopłaty w SpinBetter "
                f"i wyślij swoje <b>ID ponownie</b> 🔄",
                parse_mode="HTML")
        else:
            bot.send_message(uid,
                f"✅ <b>Rejestracja potwierdzona!</b>\n\n"
                f"Aby odblokować dostęp — dokonaj wpłaty w wysokości <b>minimum ~40 zł</b> w SpinBetter.\n\n"
                f"Po wpłacie wyślij swoje <b>ID ponownie</b> — dostęp zostanie przyznany automatycznie 🎯",
                parse_mode="HTML")
        return

    # Zapisz player_id + dane użytkownika
    if str(uid) in data["users"]:
        data["users"][str(uid)]["player_id"]  = player_id
        data["users"][str(uid)]["tg_id"]      = uid
        data["users"][str(uid)]["username"]   = message.from_user.username or data["users"][str(uid)].get("username", "")
        data["users"][str(uid)]["first_name"] = message.from_user.first_name or data["users"][str(uid)].get("first_name", "")
        save_data(data)

    dl  = days_left(data, uid)
    url = build_url(player_id, dl, message.from_user.username if hasattr(message, 'from_user') else "")
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

    elif payload.get('type') == 'win':
        game = payload.get('game', 'mines')
        data = load_data()
        user = data["users"].get(str(uid), {})
        key  = f"wins_{game}"
        user[key] = user.get(key, 0) + 1
        wins_mines   = user.get("wins_mines", 0)
        wins_penalty = user.get("wins_penalty", 0)
        wins_total   = wins_mines + wins_penalty
        data["users"][str(uid)] = user
        save_data(data)
        game_name = "Mines 💎" if game == "mines" else "Penalty ⚽"
        bot.send_message(uid,
            f"🎉 <b>Gratulacje! Wygrałeś!</b>\n\n"
            f"🎮 Gra: <b>{game_name}</b>\n\n"
            f"📊 <b>Twoje statystyki:</b>\n"
            f"💎 Mines: <b>{wins_mines}</b> wygranych\n"
            f"⚽ Penalty: <b>{wins_penalty}</b> wygranych\n"
            f"🏆 Łącznie: <b>{wins_total}</b> wygranych\n\n"
            f"Tak trzymaj! Algorytm działa 🚀",
            parse_mode="HTML")

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
    kb.add(InlineKeyboardButton("✏️ Własna wiadomość",    callback_data="adm_push_custom"))
    kb.add(InlineKeyboardButton("📷 Wiadomość ze zdjęciem", callback_data="adm_push_photo"))
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

@bot.callback_query_handler(func=lambda c: c.data == "adm_push_photo")
def cb_adm_push_photo(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak dostępu.", show_alert=True); return
    user_state[call.from_user.id] = "entering_broadcast_photo"
    try:
        bot.edit_message_text(
            "📷 <b>Wyślij zdjęcie z podpisem:</b>\n\n"
            "Wyślij zdjęcie i wpisz tekst jako podpis (caption).\n"
            "Obsługuje HTML: &lt;b&gt;bold&lt;/b&gt;, &lt;i&gt;italic&lt;/i&gt;\n\n"
            "⚠️ Podpis jest opcjonalny — możesz wysłać samo zdjęcie.",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("◀️ Anuluj", callback_data="adm_back")))
    except Exception as e:
        print(f"cb_adm_push_photo error: {e}")
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

@bot.message_handler(
    content_types=["photo"],
    func=lambda m: user_state.get(m.from_user.id) == "entering_broadcast_photo"
)
def msg_broadcast_photo(message):
    if not is_admin(message):
        return
    uid     = message.from_user.id
    file_id = message.photo[-1].file_id          # берём наибольшее разрешение
    caption = (message.caption or "").strip()
    user_state.pop(uid, None)
    _do_broadcast_photo(uid, file_id, caption)

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

def _do_broadcast_photo(uid, file_id, caption):
    bot.send_message(uid, "📷 <b>Wysyłam zdjęcie...</b>", parse_mode="HTML")
    def do_send():
        data = load_data()
        sent = 0
        for uid_str, user in data["users"].items():
            if not is_subscribed(data, int(uid_str)):
                continue
            try:
                bot.send_photo(
                    int(uid_str),
                    file_id,
                    caption=caption if caption else None,
                    parse_mode="HTML"
                )
                sent += 1
                time.sleep(0.05)
            except Exception as e:
                print(f"broadcast_photo error {uid_str}: {e}")
        try:
            bot.send_message(uid, f"✅ Zdjęcie wysłane do <b>{sent}</b> subskrybentów.", parse_mode="HTML")
        except Exception:
            pass
    threading.Thread(target=do_send, daemon=True).start()

# ── /sbstatus ─────────────────────────────────────────────

@bot.message_handler(commands=["sbstatus"])
def cmd_sbstatus(message):
    if not is_admin(message):
        return
    players = _sb_players_cache
    if _redis:
        try:
            raw = _redis.get(SPINBETTER_CACHE_KEY)
            if raw:
                players = json.loads(raw)
        except Exception:
            pass
    total = len(players)
    with_dep = sum(1 for v in players.values() if v)
    bot.send_message(message.chat.id,
        f"📊 <b>SpinBetter cache</b>\n\n"
        f"👥 Игроков в кеше: <b>{total}</b>\n"
        f"💰 С депозитом: <b>{with_dep}</b>\n"
        f"🔑 Token: <code>{'OK' if SPINBETTER_TOKEN else 'НЕТ'}</code>\n"
        f"🆔 User-Id: <code>{'OK' if SPINBETTER_USER_ID else 'НЕТ'}</code>",
        parse_mode="HTML")

# ── /sbcheck ─────────────────────────────────────────────

@bot.message_handler(commands=["sbcheck"])
def cmd_sbcheck(message):
    if not is_admin(message):
        return
    parts = message.text.strip().split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Użycie: /sbcheck PLAYER_ID")
        return
    pid = parts[1].strip()
    players = _sb_players_cache
    if _redis:
        try:
            raw = _redis.get(SPINBETTER_CACHE_KEY)
            if raw:
                players = json.loads(raw)
        except Exception:
            pass
    if pid not in players:
        bot.send_message(message.chat.id, f"❌ ID <code>{pid}</code> — nie znaleziono w cache.", parse_mode="HTML")
        return
    dep = float(players[pid])
    dep_pln = round(dep * 4.0)
    status = "✅ OK" if dep >= MIN_DEPOSIT else f"⚠️ Za mało — brakuje ${MIN_DEPOSIT - dep:.2f} (~{round((MIN_DEPOSIT - dep) * 4)} zł)"
    bot.send_message(message.chat.id,
        f"🔍 <b>SpinBetter check</b>\n\n"
        f"🆔 ID: <code>{pid}</code>\n"
        f"💰 Suma depozytów: <b>${dep:.2f}</b> (~{dep_pln} zł)\n"
        f"📊 Status: {status}",
        parse_mode="HTML")

# ── /statystyki ───────────────────────────────────────────

@bot.message_handler(commands=["statystyki"])
def cmd_statystyki(message):
    uid  = message.from_user.id
    data = load_data()
    user = data["users"].get(str(uid), {})
    wins_mines   = user.get("wins_mines", 0)
    wins_penalty = user.get("wins_penalty", 0)
    wins_total   = wins_mines + wins_penalty
    dl = days_left(data, uid)
    sub_str = f"✅ Aktywna — {dl} dni" if is_subscribed(data, uid) else "❌ Brak subskrypcji"
    bot.send_message(uid,
        f"📊 <b>Twoje statystyki</b>\n\n"
        f"🏆 Łącznie wygranych: <b>{wins_total}</b>\n"
        f"💎 Mines: <b>{wins_mines}</b>\n"
        f"⚽ Penalty: <b>{wins_penalty}</b>\n\n"
        f"🔑 Subskrypcja: {sub_str}",
        parse_mode="HTML")

# ── CATCH-ALL: подписан но нет player_id (после перезапуска бота) ──────────

@bot.message_handler(func=lambda m: (
    m.content_type == 'text'
    and user_state.get(m.from_user.id) is None
    and not m.text.startswith('/')
))
def catch_text(message):
    uid  = message.from_user.id
    data = load_data()
    if not is_subscribed(data, uid):
        return
    player_id = data["users"].get(str(uid), {}).get("player_id", "")
    if player_id:
        return  # уже есть ID — игнорируем случайный текст
    # Нет player_id — считаем что юзер присылает ID
    user_state[uid] = "entering_id"
    msg_id(message)

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

# ── AUTO-MESSAGE: 10 min po /start bez subskrypcji ────────

INTRO_DELAY = 10 * 60  # 10 minut w sekundach

INTRO_MESSAGE = (
    "⏰ <b>Hej, jeszcze tu jesteś?</b>\n\n"
    "Zauważyliśmy, że nie aktywowałeś jeszcze subskrypcji.\n\n"
    "🎯 Nasz algorytm dziś już pomógł graczom zdobyć pierwsze wygrane — "
    "nie zostań z tyłu!\n\n"
    "💳 Wybierz plan i zacznij wygrywać:\n"
    "👉 Skontaktuj się z <a href='https://t.me/rmpl13'>@rmpl13</a> "
    "lub wpisz swój kod aktywacyjny"
)

def intro_scheduler():
    """Co minutę sprawdza czy minęło 10 min od /start i wysyła wiadomość."""
    while True:
        try:
            data = load_data()
            now  = datetime.now()
            changed = False
            for uid_str, user in data["users"].items():
                # Пропускаем если уже подписан или уже отправляли
                if user.get("subscription_end") or user.get("intro_sent"):
                    continue
                start_at = user.get("start_at")
                if not start_at:
                    continue
                elapsed = (now - datetime.fromisoformat(start_at)).total_seconds()
                if elapsed >= INTRO_DELAY:
                    try:
                        bot.send_message(
                            int(uid_str),
                            INTRO_MESSAGE,
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                            reply_markup=kb_plans()
                        )
                        print(f"intro_scheduler: sent to {uid_str}")
                    except Exception as e:
                        print(f"intro_scheduler send error {uid_str}: {e}")
                    user["intro_sent"] = True
                    changed = True
            if changed:
                save_data(data)
        except Exception as e:
            print(f"intro_scheduler error: {e}")
        time.sleep(60)

# ── RUN ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("MinesPredictor bot uruchomiony...")
    threading.Thread(target=push_scheduler, daemon=True).start()
    threading.Thread(target=inactive_scheduler, daemon=True).start()
    threading.Thread(target=sb_scheduler, daemon=True).start()
    threading.Thread(target=intro_scheduler, daemon=True).start()
    print("Schedulery uruchomione.")

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
