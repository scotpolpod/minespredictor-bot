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
BOT_USERNAME   = os.getenv("BOT_USERNAME", "")   # e.g. "MinesPredictorBot"
ADMIN_USERNAME = "rmpl13"
ADMIN_ID       = int(os.getenv("ADMIN_ID", "0"))  # числовой Telegram ID админа
MANAGER_LINK   = "https://t.me/rmpl13"
DATA_FILE      = "data.json"
VIP_USERNAMES  = ["iiiiiigggggg", "S_V_V1"]

bot = telebot.TeleBot(BOT_TOKEN)

def notify_admin(text):
    if ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, text, parse_mode="HTML")
        except Exception as e:
            print(f"notify_admin error: {e}")

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

# ── VAVADA VERIFICATION ───────────────────────────────────
VAVADA_TOKEN      = os.getenv("VAVADA_TOKEN", "").strip()
VAVADA_REFRESH_TOKEN = os.getenv("VAVADA_REFRESH_TOKEN", "").strip()
VAVADA_USER_ID    = os.getenv("VAVADA_USER_ID", "7e97d47a-a531-4b6e-966f-94a67b462c94").strip()
# Comma-separated referral link IDs from Vavada Partners panel
VAVADA_LINK_IDS   = [x.strip() for x in os.getenv("VAVADA_LINK_IDS", "bc250ff7-01ae-4903-a18e-e4ca67f77e45").split(",") if x.strip()]
VAVADA_API_URL    = "https://api.vavadapart.com/graphql"
VAVADA_REFRESH_URL = "https://api.vavadapart.com/auth/token/refresh"
VAVADA_CACHE_KEY  = "vavada_players"
VAVADA_CACHE_REFRESH = 180  # секунд (3 минуты)

_vavada_token         = VAVADA_TOKEN          # текущий access token
_vavada_refresh_token = VAVADA_REFRESH_TOKEN  # текущий refresh token (ротируется)
_vavada_cache         = {}                    # {login_lower: deposit_usd}

VAVADA_RTOKEN_REDIS_KEY = "vavada_refresh_token"

def _vavada_init_tokens():
    """При старте берём свежий refresh token из Redis если он там есть."""
    global _vavada_refresh_token
    if _redis:
        try:
            saved = _redis.get(VAVADA_RTOKEN_REDIS_KEY)
            if saved:
                _vavada_refresh_token = saved
                print("Vavada: refresh token loaded from Redis")
        except Exception as e:
            print(f"Vavada: could not load refresh token from Redis: {e}")

# GraphQL запрос — точная копия из DevTools браузера
_VAVADA_GQL = """query GetCpaStatisticDetailed($after: Cursor, $cpaMediaItemId: ID!, $end: Date!, $filters: [CpaStatisticDetailedFilterInput!]!, $first: Int, $referralNameSearch: String, $sort: CpaDetailedStatisticSortInput, $start: Date!, $userId: ID!) {
  user(id: $userId) {
    id
    ... on Partner {
      referralLinkMediaItem(id: $cpaMediaItemId) {
        ...CpaReferralLinkStatisticData
        __typename
      }
      __typename
    }
    ... on Company {
      referralLinkMediaItem(id: $cpaMediaItemId) {
        ...CpaReferralLinkStatisticData
        __typename
      }
      __typename
    }
    __typename
  }
}

fragment MoneyAmountData on MoneyAmount {
  currency
  value
  __typename
}

fragment CpaReferralLinkStatisticInfoData on CpaReferralLinkStatisticInfo {
  averageDeposit { ...MoneyAmountData __typename }
  depositsAll { ...MoneyAmountData __typename }
  firstDepositAll { ...MoneyAmountData __typename }
  numberOfRedeposits
  redepositsAll { ...MoneyAmountData __typename }
  wasFD
  withdrawalsAll { ...MoneyAmountData __typename }
  __typename
}

fragment CpaReferralLinkStatisticItemData on CpaReferralLinkStatisticItem {
  id
  playerName
  referralStatus
  statisticInfo { ...CpaReferralLinkStatisticInfoData __typename }
  target
  __typename
}

fragment PageInfoData on PageInfo {
  endCursor
  hasNextPage
  hasPreviousPage
  startCursor
  __typename
}

fragment CpaReferralLinkStatisticConnectionData on CpaReferralLinkStatisticConnection {
  edges {
    cursor
    node { ...CpaReferralLinkStatisticItemData __typename }
    __typename
  }
  pageInfo { ...PageInfoData __typename }
  __typename
}

fragment CpaReferralLinkStatisticData on ReferralLinkMediaItem {
  cpaStatistic {
    statisticItems(
      after: $after
      end: $end
      filters: $filters
      first: $first
      referralNameSearch: $referralNameSearch
      sort: $sort
      start: $start
    ) {
      ...CpaReferralLinkStatisticConnectionData
      __typename
    }
    __typename
  }
  id
  __typename
}"""

def _vavada_headers():
    return {
        "Authorization": f"Bearer {_vavada_token}",
        "Content-Type":  "application/json",
        "Origin":        "https://affiliates.vavadapart.com",
        "Referer":       "https://affiliates.vavadapart.com/",
    }

def _vavada_try_refresh():
    """Обновляет access token через refresh endpoint (cookie-based)."""
    global _vavada_token, _vavada_refresh_token
    # Берём refresh token из памяти или Redis
    rtoken = _vavada_refresh_token
    if not rtoken and _redis:
        try:
            rtoken = _redis.get(VAVADA_RTOKEN_REDIS_KEY) or ""
        except Exception:
            pass
    if not rtoken:
        print("Vavada refresh: no refresh token available")
        return False
    try:
        resp = _requests.post(
            VAVADA_REFRESH_URL,
            data=b"",   # пустое тело
            headers={
                "Content-Type": "application/json",
                "Content-Length": "0",
                "Origin":  "https://affiliates.vavadapart.com",
                "Referer": "https://affiliates.vavadapart.com/",
                "Cookie":  f"refresh_token={rtoken}",
            },
            timeout=15
        )
        print(f"Vavada refresh HTTP {resp.status_code}")
        if not resp.ok:
            print(f"Vavada refresh error body: {resp.text[:300]}")
            return False
        body = resp.json()
        new_access = body.get("token", "").strip()
        if new_access:
            _vavada_token = new_access
            print("Vavada access token refreshed OK")
        # Достаём новый refresh token — сначала через requests.cookies
        new_rtoken = resp.cookies.get("refresh_token", "")
        # Fallback: парсим Set-Cookie заголовок
        if not new_rtoken:
            for hdr in resp.headers.get("Set-Cookie", "").split(","):
                for part in hdr.split(";"):
                    part = part.strip()
                    if part.startswith("refresh_token="):
                        new_rtoken = part[len("refresh_token="):]
                        break
        if new_rtoken:
            _vavada_refresh_token = new_rtoken
            if _redis:
                try:
                    _redis.set(VAVADA_RTOKEN_REDIS_KEY, new_rtoken)
                except Exception:
                    pass
            print("Vavada refresh token rotated OK")
        return bool(new_access)
    except Exception as e:
        print(f"Vavada refresh error: {e}")
    return False

def _vavada_gql(variables, retry=True):
    """Выполняет GraphQL запрос. При 401 пробует обновить токен."""
    global _vavada_token
    resp = _requests.post(
        VAVADA_API_URL,
        json={"operationName": "GetCpaStatisticDetailed",
              "query": _VAVADA_GQL,
              "variables": variables},
        headers=_vavada_headers(),
        timeout=20
    )
    if resp.status_code == 401 and retry:
        print("Vavada 401 — trying token refresh...")
        if _vavada_try_refresh():
            return _vavada_gql(variables, retry=False)
    if not resp.ok:
        print(f"Vavada API error {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()

def _fetch_vavada_link_players(link_id):
    """Возвращает dict {login_lower: dep_usd} для одной реферальной ссылки."""
    players = {}
    cursor = None
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    page = 0
    while True:
        variables = {
            "after":          cursor,
            "cpaMediaItemId": link_id,
            "end":            end_date,
            "filters":        [],
            "first":          200,
            "referralNameSearch": None,
            "sort":           {"orderBy": "DEPOSITS_ALL", "sortOrder": "DESC"},
            "start":          start_date,
            "userId":         VAVADA_USER_ID,
        }
        data = _vavada_gql(variables)
        items = (data.get("data", {})
                     .get("user", {})
                     .get("referralLinkMediaItem", {})
                     .get("cpaStatistic", {})
                     .get("statisticItems", {}))
        edges = items.get("edges", [])
        for edge in edges:
            node = edge.get("node", {})
            name = node.get("playerName", "")
            if not name:
                continue
            dep = float(node.get("statisticInfo", {})
                            .get("depositsAll", {})
                            .get("value", 0) or 0)
            if dep == 0:
                dep = float(node.get("statisticInfo", {})
                                .get("firstDepositAll", {})
                                .get("value", 0) or 0)
            players[name.lower()] = max(players.get(name.lower(), 0.0), dep)
        page_info = items.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor", "")
        page += 1
        if page > 20:   # защита от бесконечного цикла
            break
    return players

def fetch_vavada_players():
    """Загружает всех игроков по всем ссылкам. Возвращает {login_lower: dep_usd}."""
    if not VAVADA_TOKEN and not _vavada_token:
        return None
    all_players = {}
    for link_id in VAVADA_LINK_IDS:
        try:
            p = _fetch_vavada_link_players(link_id)
            all_players.update(p)
            print(f"Vavada link {link_id[:8]}...: {len(p)} players")
        except Exception as e:
            print(f"Vavada fetch error (link {link_id[:8]}...): {e}")
    return all_players if all_players else None

_vavada_cache_ts = 0.0
VAVADA_CACHE_TTL = 180  # секунд

def refresh_vavada_cache(force=False):
    global _vavada_cache, _vavada_cache_ts
    import time as _time
    if not force and (_time.time() - _vavada_cache_ts) < VAVADA_CACHE_TTL:
        return  # кеш ещё свежий
    players = fetch_vavada_players()
    if players is None:
        return
    _vavada_cache = players
    _vavada_cache_ts = _time.time()
    if _redis:
        try:
            _redis.set(VAVADA_CACHE_KEY, json.dumps(players))
        except Exception as e:
            print(f"Vavada Redis save error: {e}")
    print(f"Vavada cache updated: {len(players)} players total")

MIN_DEPOSIT = 12.5  # ~50 zł в USD

def check_player(player_id):
    """Returns: 'not_found' | ('no_deposit', amount_usd) | 'ok'"""
    return "ok"   # верификация отключена — доступ через /setid
    if not VAVADA_TOKEN and not _vavada_token:
        return "ok"
    pid = str(player_id).strip().lower()
    players = _vavada_cache
    if _redis:
        try:
            raw = _redis.get(VAVADA_CACHE_KEY)
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
    """Updates Vavada cache every 3 minutes."""
    while True:
        try:
            refresh_vavada_cache()
        except Exception as e:
            print(f"vavada_scheduler error: {e}")
        time.sleep(VAVADA_CACHE_REFRESH)

def vavada_token_scheduler():
    """Refreshes Vavada access token every 50 minutes (token TTL = 1h)."""
    time.sleep(50 * 60)  # первый рефреш через 50 минут после старта
    while True:
        try:
            ok = _vavada_try_refresh()
            if not ok:
                notify_admin(
                    "🚨 <b>SlotsGems token refresh FAILED!</b>\n\n"
                    "Refresh token wygasł lub jest nieprawidłowy.\n\n"
                    "Zaloguj się na <b>affiliates.vavadapart.com</b>, skopiuj nowy "
                    "<code>refresh_token</code> z DevTools (Cookies) i zaktualizuj "
                    "zmienną <b>VAVADA_REFRESH_TOKEN</b> w Railway.")
        except Exception as e:
            print(f"vavada_token_scheduler error: {e}")
        time.sleep(50 * 60)

PLANS = [
    {"label": "🎁 24h Trial",    "days": 1,  "price": "BEZPŁATNY"},
    {"label": "📅 14 dni",       "days": 14, "price": "250 zł"},
    {"label": "📅 30 dni",       "days": 30, "price": "399 zł"},
    {"label": "📅 60 dni",       "days": 60, "price": "649 zł"},
    {"label": "📅 90 dni",       "days": 90, "price": "849 zł"},
]

user_state = {}  # uid -> 'entering_id' | 'entering_code' | 'entering_broadcast'

# Времена авто-пушей (HH:MM, по UTC+2 — меняй под нужный TZ)
PUSH_TIMES      = ["10:00"]   # 10:00 UTC = 12:00 czasu polskiego (UTC+2)
WHEEL_PUSH_TIME = "07:05"    # 07:05 UTC = 09:05 czasu polskiego — koło fortuny gotowe

WHEEL_MESSAGE = (
    "🎡 <b>Koło Fortuny jest gotowe!</b>\n\n"
    "Zakręć teraz i wygraj dodatkowe dni subskrypcji lub sygnały!\n\n"
    "🍀 Otwórz predyktor i sprawdź swoje szczęście"
)

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

def build_url(player_id, dl, username="", ref_bonus=0, ref_code="", extra=False):
    vip     = "&vip=1"    if is_vip(username) else ""
    bonus   = f"&bonus={ref_bonus}" if ref_bonus > 0 else ""
    ref     = f"&ref={ref_code}"    if ref_code    else ""
    bot_u   = f"&bot={BOT_USERNAME}" if BOT_USERNAME else ""
    ext     = "&extra=1"  if extra else ""
    return f"{WEBAPP_URL}?uid={player_id}&days={dl}&v=7{vip}{bonus}{ref}{bot_u}{ext}"

def build_url_for_user(uid, data):
    user      = data["users"].get(str(uid), {})
    player_id = user.get("player_id") or str(uid)
    dl        = days_left(data, uid)
    username  = user.get("username", "")
    ref_bonus = user.get("ref_bonus", 0)
    ref_code  = get_ref_code(uid, data)
    extra     = bool(user.get("extra_access"))
    return build_url(player_id, dl, username, ref_bonus, ref_code, extra=extra)

def gen_code():
    return "MP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def gen_ref_code():
    return "REF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def get_ref_code(uid, data):
    """Returns user's unique referral code, creates one if it doesn't exist."""
    user = data["users"].get(str(uid), {})
    code = user.get("ref_code")
    if not code:
        existing = {u.get("ref_code") for u in data["users"].values() if u.get("ref_code")}
        code = gen_ref_code()
        while code in existing:
            code = gen_ref_code()
        if str(uid) in data["users"]:
            data["users"][str(uid)]["ref_code"] = code
            save_data(data)
    return code

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
            # Wheel of Fortune daily notification
            if hm == WHEEL_PUSH_TIME and "wheel" not in sent_today:
                sent_today["wheel"] = True
                threading.Thread(target=send_push_to_all, args=(WHEEL_MESSAGE,), daemon=True).start()
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
        ref_bonus = user.get("ref_bonus", 0)
        ref_code  = user.get("ref_code", "")
        try:
            kb = InlineKeyboardMarkup()
            if player_id:
                url = build_url(player_id, dl, username, ref_bonus, ref_code)
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
    user_state.pop(uid, None)

    # Deep-link referral: /start REF-XXXXXXXX
    parts       = message.text.strip().split(maxsplit=1)
    start_param = parts[1].upper() if len(parts) > 1 else ""
    if start_param.startswith("REF-"):
        user = data["users"].setdefault(str(uid), {})
        if not user.get("pending_ref"):          # store only once, prevent overwrite
            user["pending_ref"] = start_param
    save_data(data)

    if is_subscribed(data, uid):
        dl        = days_left(data, uid)
        player_id = data["users"].get(str(uid), {}).get("player_id", "")
        uname     = message.from_user.username or ""
        if player_id:
            ref_bonus = data["users"].get(str(uid), {}).get("ref_bonus", 0)
            ref_code  = get_ref_code(uid, data)
            url = build_url(player_id, dl, uname, ref_bonus, ref_code)
            bot.send_message(uid,
                f"👋 Witaj w <b>MinesPredictor</b>!\n\n"
                f"✅ Subskrypcja aktywna — pozostało <b>{dl} dni</b>\n\n"
                f"👇 Otwórz predyktor:",
                parse_mode="HTML",
                reply_markup=kb_open_app(url))
        else:
            user_state[uid] = "waiting_screenshot"
            bot.send_message(uid,
                f"👋 Witaj w <b>MinesPredictor</b>!\n\n"
                f"✅ Subskrypcja aktywna — pozostało <b>{dl} dni</b>\n\n"
                f"📸 Wyślij <b>zrzut ekranu potwierdzenia wpłaty</b> w SlotsGems, "
                f"aby odblokować dostęp. Dostęp zostanie przyznany automatycznie po weryfikacji ✅",
                parse_mode="HTML")
    else:
        user = data["users"].setdefault(str(uid), {})
        if not user.get("start_at"):
            user["start_at"]   = datetime.now().isoformat()
            user["tg_id"]      = uid
            user["username"]   = message.from_user.username or ""
            user["first_name"] = message.from_user.first_name or ""
            save_data(data)
        bot.send_message(uid,
            "👋 Witaj w <b>MinesPredictor</b>!\n\n"
            "🎯 Algorytm przewiduje miny, kryształy i strefy penalty.\n\n"
            "🔒 Aby korzystać — aktywuj subskrypcję.\n"
            "Masz kod? Kliknij <b>«Mam kod aktywacyjny»</b>.\n\n"
            "🎁 Chcesz <b>24h za darmo</b>? Napisz do <a href='https://t.me/rmpl13'>@rmpl13</a>!\n\n"
            "💳 <b>Wybierz plan:</b>",
            parse_mode="HTML",
            disable_web_page_preview=True,
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

    existing    = data["users"].get(str(uid), {})
    player_id   = existing.get("player_id", "")
    pending_ref = existing.get("pending_ref", "")   # referral from deep link

    data["users"][str(uid)] = {
        "subscription_end": end,
        "activated_code":   code,
        "activated_at":     datetime.now().isoformat(),
        "last_activity":    datetime.now().isoformat(),
        "username":         username or existing.get("username", ""),
        "first_name":       first_name or existing.get("first_name", ""),
        "player_id":        player_id,
        "tg_id":            uid,
        "plan_days":        days,
        # preserve other fields so they survive the overwrite
        "start_at":         existing.get("start_at"),
        "intro_sent":       existing.get("intro_sent"),
        "ref_code":         existing.get("ref_code"),
        "ref_bonus":        existing.get("ref_bonus", 0),
    }
    save_data(data)

    # Reward referrer if friend joined via deep link
    if pending_ref:
        _apply_referral(uid, pending_ref)

    exp_str = (datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')

    if player_id:
        dl        = days_left(data, uid)
        ref_bonus = data["users"].get(str(uid), {}).get("ref_bonus", 0)
        ref_code  = get_ref_code(uid, data)
        url = build_url(player_id, dl, username, ref_bonus, ref_code)
        user_state[uid] = None
        bot.send_message(chat_id,
            f"🎉 Subskrypcja aktywowana!\n\n"
            f"📅 Plan: <b>{days} dni</b>\n"
            f"⏳ Aktywna do: <b>{exp_str}</b>\n\n"
            f"👇 Otwórz predyktor:",
            parse_mode="HTML", reply_markup=kb_open_app(url))
    else:
        user_state[uid] = "waiting_screenshot"
        bot.send_message(chat_id,
            f"🎉 Subskrypcja aktywowana!\n\n"
            f"📅 Plan: <b>{days} dni</b>\n"
            f"⏳ Aktywna do: <b>{exp_str}</b>\n\n"
            f"📸 Aby odblokować dostęp — dokonaj wpłaty w kasynie SlotsGems "
            f"i wyślij tutaj <b>zrzut ekranu potwierdzenia wpłaty</b>.\n\n"
            f"Dostęp zostanie przyznany automatycznie po weryfikacji ✅",
            parse_mode="HTML")

# ── REFERRAL SYSTEM ──────────────────────────────────────

def _apply_referral(new_uid, ref_code):
    """Awards +2 bonus signals to the referrer. Called after friend activates sub."""
    data = load_data()
    referrer_uid_str = None
    for uid_str, user in data["users"].items():
        if user.get("ref_code") == ref_code and int(uid_str) != new_uid:
            referrer_uid_str = uid_str
            break
    if not referrer_uid_str or not is_subscribed(data, referrer_uid_str):
        return
    data["users"][referrer_uid_str]["ref_bonus"] = \
        data["users"][referrer_uid_str].get("ref_bonus", 0) + 2
    save_data(data)
    try:
        new_bonus = data["users"][referrer_uid_str]["ref_bonus"]
        bot.send_message(int(referrer_uid_str),
            f"🎉 <b>Znajomy dołączył przez Twój link!</b>\n\n"
            f"Twój aktualny bonus: <b>+{new_bonus} sygnałów dziennie</b> 🚀",
            parse_mode="HTML")
    except Exception as e:
        print(f"_apply_referral notify error: {e}")

@bot.message_handler(commands=["ref"])
def cmd_ref(message):
    uid  = message.from_user.id
    data = load_data()
    if not is_subscribed(data, uid):
        bot.send_message(uid, "🔒 Musisz mieć aktywną subskrypcję aby korzystać z systemu poleceń.")
        return
    code  = get_ref_code(uid, data)
    bonus = data["users"].get(str(uid), {}).get("ref_bonus", 0)
    link  = f"https://t.me/{BOT_USERNAME}?start={code}" if BOT_USERNAME else f"(ustaw BOT_USERNAME) kod: {code}"
    bot.send_message(uid,
        f"👥 <b>Twój link polecenia</b>\n\n"
        f"🔗 <code>{link}</code>\n\n"
        f"🎁 Aktualny bonus: <b>+{bonus} sygnałów/dzień</b>\n\n"
        f"Za każdego znajomego, który dołączy przez Twój link i aktywuje subskrypcję, "
        f"otrzymujesz <b>+2 sygnały dziennie</b> na stałe! 🚀\n\n"
        f"Możesz też udostępnić link z zakładki <b>Konto</b> w mini apce 👇",
        parse_mode="HTML",
        disable_web_page_preview=True)

# ── SCREENSHOT VERIFICATION ───────────────────────────────

@bot.message_handler(
    content_types=["photo"],
    func=lambda m: user_state.get(m.from_user.id) == "waiting_screenshot"
)
def msg_screenshot(message):
    uid = message.from_user.id
    data = load_data()
    if not is_subscribed(data, uid):
        bot.send_message(uid, "🔒 Twoja subskrypcja wygasła. Użyj /start aby odnowić.")
        user_state.pop(uid, None)
        return

    uname_str = f"@{message.from_user.username}" if message.from_user.username else f"id={uid}"
    name_str  = message.from_user.first_name or ""

    bot.send_message(uid,
        "✅ Zrzut ekranu otrzymany!\n\n"
        "⏳ Zrzut ekranu jest weryfikowany. "
        "Dostęp zostanie przyznany automatycznie ✅",
        parse_mode="HTML")

    if not ADMIN_ID:
        return

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Zatwierdź dostęp", callback_data=f"approve_{uid}"),
        InlineKeyboardButton("❌ Odrzuć", callback_data=f"reject_{uid}")
    )
    caption = (
        f"📸 <b>Nowy zrzut ekranu wpłaty</b>\n\n"
        f"👤 {name_str} {uname_str}\n"
        f"🆔 Telegram ID: <code>{uid}</code>\n\n"
        f"Zatwierdź lub odrzuć dostęp:"
    )
    bot.forward_message(ADMIN_ID, uid, message.message_id)
    bot.send_message(ADMIN_ID, caption, parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("approve_") or c.data.startswith("reject_"))
def cb_approve_reject(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak uprawnień.")
        return

    action, target_uid_str = call.data.split("_", 1)
    target_uid = int(target_uid_str)
    data = load_data()

    if action == "approve":
        # Используем tg_id как player_id если нет другого
        user = data["users"].get(str(target_uid), {})
        player_id = user.get("player_id") or str(target_uid)
        data["users"][str(target_uid)]["player_id"] = player_id
        save_data(data)
        user_state.pop(target_uid, None)

        dl        = days_left(data, target_uid)
        ref_bonus = user.get("ref_bonus", 0)
        ref_code  = get_ref_code(target_uid, data)
        url       = build_url(player_id, dl, user.get("username", ""), ref_bonus, ref_code)

        bot.send_message(target_uid,
            "✅ <b>Wpłata potwierdzona!</b>\n\n"
            "🎉 Dostęp do predyktora został przyznany.\n\n"
            "👇 Otwórz predyktor:",
            parse_mode="HTML",
            reply_markup=kb_open_app(url))

        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.answer_callback_query(call.id, f"✅ Dostęp przyznany użytkownikowi {target_uid}")
        bot.send_message(ADMIN_ID, f"✅ Dostęp przyznany: <code>{target_uid}</code>", parse_mode="HTML")

    elif action == "reject":
        user_state[target_uid] = "waiting_screenshot"
        bot.send_message(target_uid,
            "❌ <b>Wpłata nie została potwierdzona.</b>\n\n"
            "Upewnij się, że zrzut ekranu pokazuje potwierdzenie wpłaty w SlotsGems "
            "i wyślij go ponownie 🔄",
            parse_mode="HTML")

        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.answer_callback_query(call.id, f"❌ Odrzucono, użytkownik może wysłać ponownie")
        bot.send_message(ADMIN_ID, f"❌ Odrzucono: <code>{target_uid}</code>", parse_mode="HTML")


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

    # Свежий запрос к Vavada при вводе логина
    refresh_vavada_cache()
    sb_status = check_player(player_id)

    uname_str = f"@{message.from_user.username}" if message.from_user.username else f"tg_id={uid}"

    if sb_status == "not_found":
        bot.send_message(uid,
            "❌ <b>Nie znaleziono takiego loginu.</b>\n\n"
            "Sprawdź czy wpisałeś poprawny login z kasyna SlotsGems.\n\n"
            "Jeśli właśnie założyłeś konto — poczekaj chwilę i spróbuj ponownie 🔄",
            parse_mode="HTML")
        notify_admin(
            f"⚠️ <b>Brak gracza w SlotsGems</b>\n\n"
            f"👤 User: {uname_str} (id={uid})\n"
            f"🔑 Login SlotsGems: <code>{player_id}</code>\n\n"
            f"Możliwe: błędny login lub gracz nie jest w Twoim linku.\n"
            f"Użyj /setid {uid} {player_id} aby przyznać dostęp ręcznie.")
        return
    if isinstance(sb_status, tuple) and sb_status[0] == "no_deposit":
        dep_usd  = sb_status[1]
        dep_pln  = round(dep_usd * 4.0)
        need_usd = MIN_DEPOSIT - dep_usd
        need_pln = round(need_usd * 4.0)
        notify_admin(
            f"💰 <b>Za mały depozyt</b>\n\n"
            f"👤 User: {uname_str} (id={uid})\n"
            f"🔑 Login SlotsGems: <code>{player_id}</code>\n"
            f"💵 Depozyt: <b>${dep_usd:.2f}</b> / wymagane ${MIN_DEPOSIT:.0f}\n"
            f"Brakuje: ~{need_pln} zł")
        if dep_usd > 0:
            bot.send_message(uid,
                f"✅ <b>Rejestracja potwierdzona!</b>\n\n"
                f"💰 Twój aktualny depozyt: <b>${dep_usd:.2f}</b> (~{dep_pln} zł)\n"
                f"🎯 Wymagane minimum: <b>~50 zł</b>\n\n"
                f"Brakuje Ci jeszcze <b>~{need_pln} zł</b> — dokonaj dopłaty w SlotsGems "
                f"i wyślij swój <b>login ponownie</b> 🔄",
                parse_mode="HTML")
        else:
            bot.send_message(uid,
                f"✅ <b>Rejestracja potwierdzona!</b>\n\n"
                f"Aby odblokować dostęp — dokonaj wpłaty w wysokości <b>minimum ~50 zł</b> w SlotsGems.\n\n"
                f"Po wpłacie wyślij swój <b>login ponownie</b> — dostęp zostanie przyznany automatycznie 🎯",
                parse_mode="HTML")
        return

    # Zapisz player_id + dane użytkownika
    if str(uid) in data["users"]:
        data["users"][str(uid)]["player_id"]  = player_id
        data["users"][str(uid)]["tg_id"]      = uid
        data["users"][str(uid)]["username"]   = message.from_user.username or data["users"][str(uid)].get("username", "")
        data["users"][str(uid)]["first_name"] = message.from_user.first_name or data["users"][str(uid)].get("first_name", "")
        save_data(data)

    dl        = days_left(data, uid)
    uname     = message.from_user.username if hasattr(message, 'from_user') else ""
    ref_bonus = data["users"].get(str(uid), {}).get("ref_bonus", 0)
    ref_code  = get_ref_code(uid, data)
    url       = build_url(player_id, dl, uname, ref_bonus, ref_code)
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

    elif payload.get('type') == 'wheel_prize':
        prize = payload.get('prize', '')
        value = int(payload.get('value', 0))
        data  = load_data()
        user  = data["users"].get(str(uid))
        if not user:
            return
        # Anti-cheat: server-side daily lock
        today = datetime.now().strftime('%Y-%m-%d')
        if user.get('wheel_last_spin') == today:
            bot.send_message(uid, "⚠️ Już dziś kręciłeś kołem fortuny! Wróć jutro 🎡")
            return
        user['wheel_last_spin'] = today
        data["users"][str(uid)] = user
        save_data(data)

        if prize == 'days' and value > 0:
            end = user.get('subscription_end')
            if end and datetime.fromisoformat(end) > datetime.now():
                new_end = (datetime.fromisoformat(end) + timedelta(days=value)).isoformat()
            else:
                new_end = (datetime.now() + timedelta(days=value)).isoformat()
            data["users"][str(uid)]['subscription_end'] = new_end
            save_data(data)
            word    = "dzień" if value == 1 else "dni"
            new_exp = datetime.fromisoformat(new_end).strftime('%d.%m.%Y')
            bot.send_message(uid,
                f"🎡 <b>Koło Fortuny!</b>\n\n"
                f"🎉 Wygrałeś <b>+{value} {word} subskrypcji</b>!\n"
                f"📅 Subskrypcja aktywna do: <b>{new_exp}</b>",
                parse_mode="HTML")
        elif prize == 'signals' and value > 0:
            data["users"][str(uid)]['ref_bonus'] = user.get('ref_bonus', 0) + value
            save_data(data)
            bot.send_message(uid,
                f"🎡 <b>Koło Fortuny!</b>\n\n"
                f"🎉 Wygrałeś <b>+{value} sygnały dziennie</b>!\n"
                f"📡 Dodatkowe sygnały aktywne w predyktorze 🚀",
                parse_mode="HTML")
        else:
            bot.send_message(uid,
                f"🎡 <b>Koło Fortuny!</b>\n\n"
                f"😔 Tym razem bez nagrody...\n"
                f"Spróbuj jutro — szczęście się uśmiechnie! 🍀",
                parse_mode="HTML")

    elif payload.get('type') == 'extra_request':
        data = load_data()
        user_state[uid] = "waiting_extra_screenshot"
        uname_str = f"@{message.from_user.username}" if message.from_user.username else f"id={uid}"
        bot.send_message(uid,
            "⭐ <b>Ekstra Sygnał — weryfikacja</b>\n\n"
            "Wyślij <b>zrzut ekranu potwierdzający wpłatę 300 zł</b> w SlotsGems. "
            "Po weryfikacji dostęp zostanie przyznany automatycznie ✅",
            parse_mode="HTML")
        if ADMIN_ID:
            notify_admin(
                f"⭐ <b>NOWE ZGŁOSZENIE — Ekstra Sygnał</b>\n\n"
                f"👤 {uname_str} (id=<code>{uid}</code>)\n"
                f"💰 Deklarowana wpłata: <b>300 zł</b>\n\n"
                f"Czekam na zrzut ekranu od użytkownika...")

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
        bot.send_message(uid, "🔢 Wpisz nowy <b>login SlotsGems</b>:", parse_mode="HTML")
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

# ── ADMIN: диагностика player login в кэше Vavada ──────────
# /checkid <player_login>
@bot.message_handler(commands=["checkid"])
def cmd_checkid(message):
    if not is_admin(message):
        return
    parts = message.text.strip().split()
    if len(parts) < 2:
        bot.send_message(message.chat.id,
            "❌ Użycie: <code>/checkid &lt;login_gracza&gt;</code>",
            parse_mode="HTML")
        return
    pid = parts[1].strip()

    bot.send_message(message.chat.id, "⏳ Odpytuję SlotsGems (odświeżam cache)...")

    # Сначала пробуем обновить токен
    refresh_ok = _vavada_try_refresh()
    bot.send_message(message.chat.id,
        f"🔑 Token refresh: {'✅ OK' if refresh_ok else '⚠️ не удался (используем текущий)'}")

    try:
        refresh_vavada_cache(force=True)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Błąd odświeżania cache: <code>{e}</code>", parse_mode="HTML")
        return

    players = _vavada_cache
    if _redis:
        try:
            raw = _redis.get(VAVADA_CACHE_KEY)
            if raw:
                players = json.loads(raw)
        except Exception:
            pass

    pid_lower = pid.lower()
    dep = players.get(pid_lower)

    lines = [f"🔍 Szukam: <code>{pid}</code>", f"📦 Graczy w cache: <b>{len(players)}</b>", ""]

    if dep is not None:
        dep = float(dep)
        lines.append(f"✅ <b>Znaleziono!</b>")
        lines.append(f"💰 Depozyt: <b>${dep:.2f}</b>")
        lines.append(f"{'✅ Dostęp OK' if dep >= MIN_DEPOSIT else f'❌ Za mało (min ${MIN_DEPOSIT:.0f}$)'}")
    else:
        # похожие логины
        similar = [(k, v) for k, v in players.items() if pid_lower in k or k in pid_lower][:5]
        lines.append(f"❌ <b>Login <code>{pid}</code> nie znaleziony</b>")
        if similar:
            lines.append("\n🔎 Podobne loginy:")
            for k, v in similar:
                lines.append(f"  <code>{k}</code> → ${float(v):.2f}")
        else:
            lines.append("Brak podobnych loginów w cache.")

    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")


# ── ADMIN: ручная привязка player_id ───────────────────────
# /deleteuser <tg_id or @username>
@bot.message_handler(commands=["deleteuser"])
def cmd_deleteuser(message):
    if not is_admin(message):
        return
    parts = message.text.strip().split()
    if len(parts) != 2:
        bot.send_message(message.chat.id,
            "❌ Użycie: <code>/deleteuser &lt;tg_id lub @username&gt;</code>",
            parse_mode="HTML")
        return
    target = parts[1].lstrip("@").lower()
    data = load_data()
    found_key = None
    for key, user in data["users"].items():
        if key == target or str(user.get("tg_id", "")) == target or (user.get("username") or "").lower() == target:
            found_key = key
            break
    if not found_key:
        bot.send_message(message.chat.id, f"❌ Nie znaleziono użytkownika: <code>{target}</code>", parse_mode="HTML")
        return
    del data["users"][found_key]
    save_data(data)
    bot.send_message(message.chat.id, f"✅ Użytkownik <code>{found_key}</code> usunięty z bazy.", parse_mode="HTML")


# /setid <tg_user_id> <player_id>
@bot.message_handler(commands=["setid"])
def cmd_setid(message):
    if not is_admin(message):
        return
    parts = message.text.strip().split()
    if len(parts) != 3:
        bot.send_message(message.chat.id,
            "❌ Użycie: <code>/setid &lt;tg_id&gt; &lt;player_id&gt;</code>",
            parse_mode="HTML")
        return
    _, tg_id_str, player_id = parts
    try:
        tg_uid = int(tg_id_str)
    except ValueError:
        bot.send_message(message.chat.id, "❌ tg_id musi być liczbą.")
        return

    data = load_data()
    if str(tg_uid) not in data["users"]:
        bot.send_message(message.chat.id,
            f"⚠️ Użytkownik <code>{tg_uid}</code> nie istnieje w bazie.\n"
            "Upewnij się że użytkownik wcześniej korzystał z bota.",
            parse_mode="HTML")
        return

    if not is_subscribed(data, tg_uid):
        bot.send_message(message.chat.id,
            f"⚠️ Użytkownik <code>{tg_uid}</code> nie ma aktywnej subskrypcji.\n"
            "Najpierw aktywuj subskrypcję, potem ustaw ID.",
            parse_mode="HTML")
        return

    data["users"][str(tg_uid)]["player_id"] = player_id
    save_data(data)

    dl        = days_left(data, tg_uid)
    ref_bonus = data["users"][str(tg_uid)].get("ref_bonus", 0)
    ref_code  = get_ref_code(tg_uid, data)
    uname     = data["users"][str(tg_uid)].get("username", "")
    url       = build_url(player_id, dl, uname, ref_bonus, ref_code)

    # Powiadom użytkownika
    try:
        bot.send_message(tg_uid,
            f"✅ Twoje ID zostało potwierdzone!\n\n"
            f"👇 Otwórz predyktor:",
            parse_mode="HTML",
            reply_markup=kb_open_app(url))
    except Exception as e:
        print(f"setid notify error: {e}")

    bot.send_message(message.chat.id,
        f"✅ <b>Ustawiono player_id</b>\n\n"
        f"👤 TG: <code>{tg_uid}</code>\n"
        f"🎰 Player ID: <code>{player_id}</code>\n"
        f"📅 Dni: <b>{dl}</b>",
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
        InlineKeyboardButton("24h (Trial)", callback_data="adm_gen_1"),
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
        f"📊 <b>SlotsGems cache</b>\n\n"
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
        f"🔍 <b>SlotsGems check</b>\n\n"
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
        "• /ref — Twój kod polecenia (+2 sygnały za przyjaciela)\n"
        "• /help — ta wiadomość\n\n"
        "Zakup subskrypcji / 7 dni trial: @rmpl13",
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

# ── TRIAL EXPIRY → offer +3 days for 100 zł ───────────────

TRIAL_DAYS        = 1          # trial length in days
EXTENSION_DAYS    = 3          # bonus days offered
EXTENSION_DEPOSIT = 100        # zł required

def send_trial_expiry_offer(uid_str, user):
    uid = int(uid_str)
    name = user.get("first_name") or user.get("username") or "Użytkownik"
    try:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📸 Wyślij potwierdzenie wpłaty", callback_data=f"ext_screenshot_{uid}"))
        bot.send_message(uid,
            f"⏰ <b>Twój darmowy dostęp wygasł!</b>\n\n"
            f"Mamy dla Ciebie ofertę specjalną:\n\n"
            f"💎 Wpłać <b>{EXTENSION_DEPOSIT} zł</b> w SlotsGems i otrzymaj "
            f"<b>+{EXTENSION_DAYS} dni</b> dostępu gratis!\n\n"
            f"Kliknij przycisk poniżej, wyślij zrzut ekranu potwierdzenia wpłaty "
            f"— dostęp zostanie przyznany automatycznie po weryfikacji ✅",
            parse_mode="HTML", reply_markup=kb)
        print(f"Trial expiry offer sent to {uid_str}")
    except Exception as e:
        print(f"Trial expiry offer error {uid_str}: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("ext_screenshot_"))
def cb_ext_screenshot(call):
    uid = call.from_user.id
    data = load_data()
    user = data["users"].get(str(uid), {})
    # Проверяем что подписка действительно истекла
    if is_subscribed(data, uid):
        bot.answer_callback_query(call.id, "У тебя ещё активна подписка.", show_alert=True)
        return
    user_state[uid] = "waiting_ext_screenshot"
    bot.answer_callback_query(call.id)
    bot.send_message(uid,
        f"📸 Wyślij zrzut ekranu potwierdzający wpłatę <b>{EXTENSION_DEPOSIT} zł</b> w SlotsGems:",
        parse_mode="HTML")

@bot.message_handler(
    content_types=["photo"],
    func=lambda m: user_state.get(m.from_user.id) == "waiting_ext_screenshot"
)
def msg_ext_screenshot(message):
    uid = message.from_user.id
    data = load_data()
    if is_subscribed(data, uid):
        bot.send_message(uid, "✅ Twoja subskrypcja jest już aktywna.")
        user_state.pop(uid, None)
        return

    uname_str = f"@{message.from_user.username}" if message.from_user.username else f"id={uid}"
    bot.send_message(uid,
        "✅ Zrzut ekranu otrzymany!\n\n"
        "⏳ Zrzut ekranu jest weryfikowany. "
        "Dostęp zostanie przyznany automatycznie ✅")

    if not ADMIN_ID:
        return
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton(f"✅ Zatwierdź +{EXTENSION_DAYS} dni", callback_data=f"ext_approve_{uid}"),
        InlineKeyboardButton("❌ Odrzuć", callback_data=f"ext_reject_{uid}")
    )
    bot.forward_message(ADMIN_ID, uid, message.message_id)
    bot.send_message(ADMIN_ID,
        f"📸 <b>Przedłużenie trialu (+{EXTENSION_DAYS} dni)</b>\n\n"
        f"👤 {uname_str} (id={uid})\n"
        f"💰 Deklarowana wpłata: {EXTENSION_DEPOSIT} zł\n\n"
        f"Zatwierdź lub odrzuć:",
        parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("ext_approve_") or c.data.startswith("ext_reject_"))
def cb_ext_approve_reject(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak uprawnień.")
        return

    action, target_uid_str = call.data.split("_", 2)[0], call.data.split("_", 2)[2]
    target_uid = int(target_uid_str)
    data = load_data()

    if action == "ext":
        # determine approve or reject from full callback data
        if call.data.startswith("ext_approve_"):
            # Add EXTENSION_DAYS to subscription
            user = data["users"].get(str(target_uid), {})
            old_end = user.get("subscription_end")
            if old_end and datetime.fromisoformat(old_end) > datetime.now():
                new_end = datetime.fromisoformat(old_end) + timedelta(days=EXTENSION_DAYS)
            else:
                new_end = datetime.now() + timedelta(days=EXTENSION_DAYS)
            data["users"][str(target_uid)]["subscription_end"] = new_end.isoformat()
            data["users"][str(target_uid)]["trial_offer_sent"] = True
            save_data(data)
            user_state.pop(target_uid, None)

            dl        = days_left(data, target_uid)
            player_id = user.get("player_id") or str(target_uid)
            ref_bonus = user.get("ref_bonus", 0)
            ref_code  = get_ref_code(target_uid, data)
            url       = build_url(player_id, dl, user.get("username", ""), ref_bonus, ref_code)

            bot.send_message(target_uid,
                f"✅ <b>Wpłata potwierdzona!</b>\n\n"
                f"🎉 Otrzymałeś <b>+{EXTENSION_DAYS} dni</b> dostępu!\n\n"
                f"👇 Otwórz predyktor:",
                parse_mode="HTML", reply_markup=kb_open_app(url))

            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            bot.answer_callback_query(call.id, f"✅ +{EXTENSION_DAYS} dni przyznane")
            bot.send_message(ADMIN_ID, f"✅ Przedłużono: <code>{target_uid}</code>", parse_mode="HTML")

        elif call.data.startswith("ext_reject_"):
            user_state[target_uid] = "waiting_ext_screenshot"
            bot.send_message(target_uid,
                "❌ <b>Wpłata nie została potwierdzona.</b>\n\n"
                f"Upewnij się że wpłaciłeś minimum <b>{EXTENSION_DEPOSIT} zł</b> w SlotsGems "
                f"i wyślij zrzut ekranu ponownie 🔄",
                parse_mode="HTML")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            bot.answer_callback_query(call.id, "❌ Odrzucono")

# ── EKSTRA SIGNAL: screenshot handler ─────────────────────

@bot.message_handler(
    content_types=["photo"],
    func=lambda m: user_state.get(m.from_user.id) == "waiting_extra_screenshot"
)
def msg_extra_screenshot(message):
    uid = message.from_user.id
    data = load_data()
    if not is_subscribed(data, uid):
        bot.send_message(uid, "🔒 Twoja subskrypcja wygasła. Użyj /start aby odnowić.")
        user_state.pop(uid, None)
        return

    uname_str = f"@{message.from_user.username}" if message.from_user.username else f"id={uid}"
    name_str  = message.from_user.first_name or ""
    user      = data["users"].get(str(uid), {})
    sub_end   = user.get("subscription_end", "—")
    plan_days = user.get("plan_days", "?")
    wins      = user.get("wins_mines", 0) + user.get("wins_penalty", 0)

    bot.send_message(uid,
        "✅ Zrzut ekranu otrzymany!\n\n"
        "⏳ Zrzut ekranu jest weryfikowany. "
        "Dostęp zostanie przyznany automatycznie ✅")

    if not ADMIN_ID:
        return

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("⭐ Zatwierdź Ekstra dostęp", callback_data=f"xtr_approve_{uid}"),
        InlineKeyboardButton("❌ Odrzuć", callback_data=f"xtr_reject_{uid}")
    )
    bot.forward_message(ADMIN_ID, uid, message.message_id)
    bot.send_message(ADMIN_ID,
        f"⭐ <b>EKSTRA SYGNAŁ — weryfikacja wpłaty 300 zł</b>\n\n"
        f"👤 {name_str} {uname_str}\n"
        f"🆔 Telegram ID: <code>{uid}</code>\n"
        f"📅 Subskrypcja do: {sub_end[:10] if sub_end != '—' else '—'}\n"
        f"🏆 Łączne wygrane: {wins}\n\n"
        f"Zatwierdź lub odrzuć dostęp do Ekstra Sygnału:",
        parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("xtr_approve_") or c.data.startswith("xtr_reject_"))
def cb_xtr_approve_reject(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "Brak uprawnień.")
        return

    parts      = call.data.split("_", 2)
    action     = parts[1]   # "approve" or "reject"
    target_uid = int(parts[2])
    data       = load_data()

    if action == "approve":
        data["users"][str(target_uid)]["extra_access"] = True
        save_data(data)
        user_state.pop(target_uid, None)

        url = build_url_for_user(target_uid, data)
        user = data["users"].get(str(target_uid), {})
        dl   = days_left(data, target_uid)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⭐ Otwórz Ekstra Sygnał", web_app=WebAppInfo(url=url)))
        bot.send_message(target_uid,
            "⭐ <b>Ekstra Sygnał odblokowany!</b>\n\n"
            "🎉 Wpłata potwierdzona — masz dostęp do ekskluzywnego sygnału penalty.\n\n"
            "Dostępny <b>1 raz dziennie</b>. Kliknij przycisk aby otworzyć:",
            parse_mode="HTML", reply_markup=kb)

        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.answer_callback_query(call.id, f"⭐ Ekstra dostęp przyznany!")
        bot.send_message(ADMIN_ID, f"⭐ Ekstra dostęp przyznany: <code>{target_uid}</code>", parse_mode="HTML")

    elif action == "reject":
        user_state[target_uid] = "waiting_extra_screenshot"
        bot.send_message(target_uid,
            "❌ <b>Wpłata nie została potwierdzona.</b>\n\n"
            "Upewnij się że wpłaciłeś minimum <b>300 zł</b> w SlotsGems "
            "i wyślij zrzut ekranu ponownie 🔄",
            parse_mode="HTML")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.answer_callback_query(call.id, "❌ Odrzucono")
        bot.send_message(ADMIN_ID, f"❌ Ekstra odrzucony: <code>{target_uid}</code>", parse_mode="HTML")


def trial_expiry_scheduler():
    """Checks every minute for expired 1-day trials and sends extension offer once."""
    while True:
        try:
            data = load_data()
            now  = datetime.now()
            changed = False
            for uid_str, user in list(data["users"].items()):
                # Только триальные (1 день), оффер ещё не отправляли
                if user.get("trial_offer_sent"):
                    continue
                activated_code = user.get("activated_code", "")
                plan_days = user.get("plan_days", 0)
                if plan_days != TRIAL_DAYS:
                    continue
                sub_end = user.get("subscription_end")
                if not sub_end:
                    continue
                end_dt = datetime.fromisoformat(sub_end)
                # Триал истёк
                if end_dt > now:
                    continue
                user["trial_offer_sent"] = True
                changed = True
                threading.Thread(target=send_trial_expiry_offer, args=(uid_str, dict(user)), daemon=True).start()
            if changed:
                save_data(data)
        except Exception as e:
            print(f"trial_expiry_scheduler error: {e}")
        time.sleep(60)


# ── RUN ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("MinesPredictor bot uruchomiony...")

    # Auto-fetch bot username for referral deep links
    if not BOT_USERNAME:
        try:
            BOT_USERNAME = bot.get_me().username or ""
            print(f"Bot username: @{BOT_USERNAME}")
        except Exception as _e:
            print(f"Could not fetch bot username: {_e}")

    _vavada_init_tokens()  # загрузить свежий refresh token из Redis

    threading.Thread(target=push_scheduler, daemon=True).start()
    threading.Thread(target=inactive_scheduler, daemon=True).start()
    threading.Thread(target=sb_scheduler, daemon=True).start()
    threading.Thread(target=intro_scheduler, daemon=True).start()
    threading.Thread(target=vavada_token_scheduler, daemon=True).start()
    threading.Thread(target=trial_expiry_scheduler, daemon=True).start()
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
