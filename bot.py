import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN  = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())


class Setup(StatesGroup):
    choosing_platform = State()
    entering_id       = State()


PLATFORMS = ["Vavada", "SpinBetter", "SlotsGem"]


# ── /start ──────────────────────────────────────────────
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Vavada",      callback_data="platform:Vavada")],
        [InlineKeyboardButton(text="🎲 SpinBetter",  callback_data="platform:SpinBetter")],
        [InlineKeyboardButton(text="💎 SlotsGem",    callback_data="platform:SlotsGem")],
    ])
    await message.answer(
        "👋 Witaj w <b>MinesPredictor</b>!\n\n"
        "🎯 Nasz algorytm przewiduje lokalizację min i kryształów "
        "oraz optymalną strefę uderzenia w Penalty.\n\n"
        "📌 <b>Wybierz swoją platformę:</b>",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.set_state(Setup.choosing_platform)


# ── Выбор платформы ──────────────────────────────────────
@dp.callback_query(F.data.startswith("platform:"), Setup.choosing_platform)
async def platform_chosen(call: types.CallbackQuery, state: FSMContext):
    platform = call.data.split(":")[1]
    await state.update_data(platform=platform)
    await call.message.edit_text(
        f"✅ Platforma: <b>{platform}</b>\n\n"
        f"🔢 Teraz wpisz swoje <b>ID gracza</b> z {platform}:",
        parse_mode="HTML"
    )
    await state.set_state(Setup.entering_id)
    await call.answer()


# ── Ввод ID ───────────────────────────────────────────────
@dp.message(Setup.entering_id)
async def id_entered(message: types.Message, state: FSMContext):
    uid = message.text.strip()
    if not uid or len(uid) < 2:
        await message.answer("⚠️ Nieprawidłowe ID. Spróbuj ponownie:")
        return

    data = await state.get_data()
    platform = data.get("platform", "Vavada")
    await state.clear()

    # Формируем URL с параметрами
    webapp_url = f"{WEBAPP_URL}?platform={platform}&uid={uid}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Otwórz MinesPredictor",
            web_app=WebAppInfo(url=webapp_url)
        )],
        [InlineKeyboardButton(text="🔄 Zmień platformę/ID", callback_data="restart")]
    ])

    await message.answer(
        f"🎉 Wszystko gotowe!\n\n"
        f"🏷 Platforma: <b>{platform}</b>\n"
        f"🔢 ID gracza: <b>{uid}</b>\n\n"
        f"👇 Naciśnij przycisk, aby otworzyć predyktor:",
        parse_mode="HTML",
        reply_markup=kb
    )


# ── Рестарт ───────────────────────────────────────────────
@dp.callback_query(F.data == "restart")
async def restart(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await start(call.message, state)
    await call.answer()


# ── /help ─────────────────────────────────────────────────
@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "ℹ️ <b>Pomoc MinesPredictor</b>\n\n"
        "• /start — uruchom i skonfiguruj bota\n"
        "• /help — ta wiadomość\n\n"
        "Obsługiwane platformy: Vavada, SpinBetter, SlotsGem",
        parse_mode="HTML"
    )


async def main():
    print("MinesPredictor bot uruchomiony...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
