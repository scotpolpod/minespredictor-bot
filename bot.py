import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN  = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")

bot = telebot.TeleBot(BOT_TOKEN)

# Хранилище состояний пользователей
user_state    = {}  # user_id -> 'choosing' | 'entering_id'
user_platform = {}  # user_id -> platform name


def platform_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎰 Vavada",     callback_data="platform:Vavada"))
    kb.add(InlineKeyboardButton("🎲 SpinBetter", callback_data="platform:SpinBetter"))
    kb.add(InlineKeyboardButton("💎 SlotsGem",   callback_data="platform:SlotsGem"))
    return kb


# ── /start ────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def start(message):
    uid = message.from_user.id
    user_state[uid]    = "choosing"
    user_platform[uid] = None

    bot.send_message(
        uid,
        "👋 Witaj w <b>MinesPredictor</b>!\n\n"
        "🎯 Nasz algorytm przewiduje lokalizację min i kryształów "
        "oraz optymalną strefę uderzenia w Penalty.\n\n"
        "📌 <b>Wybierz swoją platformę:</b>",
        parse_mode="HTML",
        reply_markup=platform_keyboard()
    )


# ── /help ─────────────────────────────────────────────────
@bot.message_handler(commands=["help"])
def help_cmd(message):
    bot.send_message(
        message.from_user.id,
        "ℹ️ <b>Pomoc MinesPredictor</b>\n\n"
        "• /start — uruchom i skonfiguruj bota\n"
        "• /help — ta wiadomość\n\n"
        "Obsługiwane platformy: Vavada, SpinBetter, SlotsGem",
        parse_mode="HTML"
    )


# ── Выбор платформы ───────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("platform:"))
def platform_chosen(call):
    uid      = call.from_user.id
    platform = call.data.split(":")[1]
    user_platform[uid] = platform
    user_state[uid]    = "entering_id"

    bot.edit_message_text(
        f"✅ Platforma: <b>{platform}</b>\n\n"
        f"🔢 Teraz wpisz swoje <b>ID gracza</b> z {platform}:",
        chat_id=uid,
        message_id=call.message.message_id,
        parse_mode="HTML"
    )
    bot.answer_callback_query(call.id)


# ── Рестарт ───────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "restart")
def restart(call):
    uid = call.from_user.id
    bot.delete_message(uid, call.message.message_id)
    start(call.message)
    bot.answer_callback_query(call.id)


# ── Ввод ID ───────────────────────────────────────────────
@bot.message_handler(func=lambda m: user_state.get(m.from_user.id) == "entering_id")
def id_entered(message):
    uid      = message.from_user.id
    player_id = message.text.strip()

    if not player_id or len(player_id) < 2:
        bot.send_message(uid, "⚠️ Nieprawidłowe ID. Spróbuj ponownie:")
        return

    platform = user_platform.get(uid, "Kasyno")
    user_state[uid] = None

    webapp_url = f"{WEBAPP_URL}?platform={platform}&uid={player_id}"

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(
        "🚀 Otwórz MinesPredictor",
        web_app=WebAppInfo(url=webapp_url)
    ))
    kb.add(InlineKeyboardButton("🔄 Zmień platformę/ID", callback_data="restart"))

    bot.send_message(
        uid,
        f"🎉 Wszystko gotowe!\n\n"
        f"🏷 Platforma: <b>{platform}</b>\n"
        f"🔢 ID gracza: <b>{player_id}</b>\n\n"
        f"👇 Naciśnij przycisk, aby otworzyć predyktor:",
        parse_mode="HTML",
        reply_markup=kb
    )


if __name__ == "__main__":
    print("MinesPredictor bot uruchomiony...")
    bot.infinity_polling()
