import asyncio
import logging
import re
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
)

import os
from dotenv import load_dotenv

load_dotenv()


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0)) 

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

CATEGORIES = {
    "kosmetika": "💄 Kosmetika va Parvarish",
    "uy_rozgor": "🏡 Uy-ro'zg'or Eko-mahsulotlari",
    "salomatlik": "🌿 Salomatlik va Vitaminlar"
}

# --- BAZA BILAN ISHLASH (SQLite) ---

def init_db():
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price INTEGER NOT NULL,
            photo_id TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cart (
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            PRIMARY KEY (user_id, product_id)
        )
    ''')
    conn.commit()
    conn.close()

def add_product_db(category, name, description, price, photo_id):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO products (category, name, description, price, photo_id) VALUES (?, ?, ?, ?, ?)",
        (category, name, description, price, photo_id)
    )
    conn.commit()
    conn.close()

# MAHSULOTNI O'CHIRISH FUNKSIYASI
def delete_product_db(product_id):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    cursor.execute("DELETE FROM cart WHERE product_id = ?", (product_id,))  # Savatlardan ham o'chirish
    conn.commit()
    conn.close()

def get_products_by_category(category):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, description, price, photo_id FROM products WHERE category = ?", (category,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_product_by_id(product_id):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, description, price, photo_id FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()
    conn.close()
    return row

# --- SAVAT BAZA AMALLARI ---

def db_add_to_cart(user_id, product_id):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO cart (user_id, product_id, quantity)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, product_id) DO UPDATE SET quantity = quantity + 1
    ''', (user_id, product_id))
    conn.commit()
    conn.close()

def db_update_cart_qty(user_id, product_id, delta):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    row = cursor.fetchone()
    if row:
        new_qty = row[0] + delta
        if new_qty <= 0:
            cursor.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
        else:
            cursor.execute("UPDATE cart SET quantity = ? WHERE user_id = ? AND product_id = ?", (new_qty, user_id, product_id))
        conn.commit()
    conn.close()

def db_delete_from_cart(user_id, product_id):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    conn.commit()
    conn.close()

def db_clear_cart(user_id):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def db_get_cart(user_id):
    conn = sqlite3.connect("greenleaf.db")
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.id, p.name, p.price, c.quantity
        FROM cart c
        JOIN products p ON c.product_id = p.id
        WHERE c.user_id = ?
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- HOLATLAR (FSM) ---

class OrderState(StatesGroup):
    waiting_for_extra_phone = State()
    waiting_for_address = State()

class AdminAddProduct(StatesGroup):
    waiting_for_category = State()
    waiting_for_photo = State()
    waiting_for_name = State()
    waiting_for_desc = State()
    waiting_for_price = State()

# --- KLAVIATURALAR ---

def get_main_menu(user_id):
    keyboard = [
        [KeyboardButton(text="🛍 Katalog"), KeyboardButton(text="🛒 Savat")],
        [KeyboardButton(text="📞 Biz bilan bog'lanish")]
    ]
    if int(user_id) == int(ADMIN_ID):
        keyboard.append([KeyboardButton(text="➕ Mahsulot qo'shish (Admin)")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_category_keyboard():
    buttons = []
    for cat_key, cat_name in CATEGORIES.items():
        buttons.append([InlineKeyboardButton(text=cat_name, callback_data=f"cat_{cat_key}")])
    buttons.append([InlineKeyboardButton(text="❌ Yopish", callback_data="close_catalog")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- BOT HANDLERLARI ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        "Xush kelibsiz! Greenleaf botidan foydalanish uchun asosiy telefon raqamingizni yuboring:",
        reply_markup=kb
    )

@dp.message(F.contact)
async def get_contact(message: types.Message, state: FSMContext):
    main_phone = message.contact.phone_number
    await state.update_data(main_phone=main_phone)
    await message.answer(
        f"✅ Raqamingiz qabul qilindi: {main_phone}",
        reply_markup=get_main_menu(message.from_user.id)
    )

# --- BIZ BILAN BOG'LANISH ---

@dp.message(F.text == "📞 Biz bilan bog'lanish")
async def contact_us(message: types.Message):
    text = (
        "<b>📞 Biz bilan bog'lanish:</b>\n\n"
        "🏢 <b>Murojaat uchun:</b> +998501022876\n"
        "💬 <b>Telegram admin:</b> @Murodjon_06_11\n"
        "📍 <b>Manzil:</b> Farg'ona viloyati, Toshloq tumani, Yakkatut\n\n"
        "Har qanday savol va takliflar bo'lsa, bemalol murojaat qilishingiz mumkin!"
    )
    await message.answer(text, parse_mode="HTML")

# --- KATALOG, O'CHIRISH VA ORQAGA QAYTISH ---

@dp.message(F.text == "🛍 Katalog")
async def show_categories(message: types.Message):
    await message.answer("Boshlang'ich kategoriyani tanlang:", reply_markup=get_category_keyboard())

@dp.callback_query(F.data == "close_catalog")
async def close_catalog(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "back_to_categories")
async def back_to_categories(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("Boshlang'ich kategoriyani tanlang:", reply_markup=get_category_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("cat_"))
async def show_products(callback: CallbackQuery):
    cat_key = callback.data.split("_")[1]
    products = get_products_by_category(cat_key)

    if not products:
        await callback.answer("Hozircha bu kategoriyada mahsulot yo'q.", show_alert=True)
        return

    await callback.message.delete()

    for p_id, name, desc, price, photo_id in products:
        caption = f"<b>📦 {name}</b>\n\n{desc}\n\n<b>💰 Narxi:</b> {price:,} so'm"
        
        btn_list = [InlineKeyboardButton(text="➕ Savatga qo'shish", callback_data=f"add_{p_id}")]
        
        # Agar so'rov yuborgan foydalanuvchi Admin bo'lsa, "O'chirish" tugmasini qo'shish
        if int(callback.from_user.id) == int(ADMIN_ID):
            btn_list.append(InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"delprod_{p_id}"))

        kb = InlineKeyboardMarkup(inline_keyboard=[btn_list])
        await callback.message.answer_photo(photo=photo_id, caption=caption, reply_markup=kb, parse_mode="HTML")

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Kategoriyalarga qaytish", callback_data="back_to_categories")]
    ])
    await callback.message.answer("Boshqa kategoriyalarga o'tish uchun:", reply_markup=back_kb)
    await callback.answer()

# ADMIN UCHUN MAHSULOTNI O'CHIRISH HANDLERI
@dp.callback_query(F.data.startswith("delprod_"))
async def admin_delete_product(callback: CallbackQuery):
    if int(callback.from_user.id) != int(ADMIN_ID):
        await callback.answer("⚠️ Bu funksiya faqat admin uchun!", show_alert=True)
        return

    p_id = int(callback.data.split("_")[1])
    delete_product_db(p_id)
    
    await callback.message.delete()
    await callback.answer("❌ Mahsulot katalogdan o'chirildi!", show_alert=True)

# --- INTERAKTIV SAVAT BO'LIMI ---

@dp.callback_query(F.data.startswith("add_"))
async def add_to_cart(callback: CallbackQuery):
    user_id = callback.from_user.id
    product_id = int(callback.data.split("_")[1])
    db_add_to_cart(user_id, product_id)
    await callback.answer("✅ Mahsulot savatga qo'shildi!", show_alert=True)

async def render_cart(message_or_callback, user_id, edit=False):
    cart_items = db_get_cart(user_id)

    if not cart_items:
        text = "🛒 Sizning savatingiz bo'sh."
        if edit and isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.message.edit_text(text)
        else:
            await message_or_callback.answer(text)
        return

    text = "<b>🛒 Sizning savatingiz:</b>\n\n"
    total_price = 0
    inline_keyboard = []

    for idx, (p_id, name, price, qty) in enumerate(cart_items, start=1):
        sum_price = price * qty
        total_price += sum_price
        text += f"{idx}. <b>{name}</b>\n   └ {qty} ta x {price:,} = <b>{sum_price:,} so'm</b>\n\n"
        
        inline_keyboard.append([
            InlineKeyboardButton(text="➖", callback_data=f"cart_dec_{p_id}"),
            InlineKeyboardButton(text=f"{qty} ta", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"cart_inc_{p_id}"),
            InlineKeyboardButton(text="🗑", callback_data=f"cart_del_{p_id}")
        ])

    text += f"<b>💵 Jami summa:</b> {total_price:,} so'm"

    inline_keyboard.append([InlineKeyboardButton(text="✅ Buyurtmani tasdiqlash", callback_data="checkout")])
    inline_keyboard.append([InlineKeyboardButton(text="🗑 Savatni tozalash", callback_data="clear_cart")])

    kb = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

    if edit and isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message_or_callback.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "🛒 Savat")
async def show_cart(message: types.Message):
    await render_cart(message, message.from_user.id, edit=False)

@dp.callback_query(F.data.startswith("cart_inc_"))
async def cart_inc(callback: CallbackQuery):
    p_id = int(callback.data.split("_")[2])
    db_update_cart_qty(callback.from_user.id, p_id, 1)
    await render_cart(callback, callback.from_user.id, edit=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("cart_dec_"))
async def cart_dec(callback: CallbackQuery):
    p_id = int(callback.data.split("_")[2])
    db_update_cart_qty(callback.from_user.id, p_id, -1)
    await render_cart(callback, callback.from_user.id, edit=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("cart_del_"))
async def cart_del(callback: CallbackQuery):
    p_id = int(callback.data.split("_")[2])
    db_delete_from_cart(callback.from_user.id, p_id)
    await render_cart(callback, callback.from_user.id, edit=True)
    await callback.answer("Mahsulot o'chirildi")

@dp.callback_query(F.data == "clear_cart")
async def clear_cart(callback: CallbackQuery):
    db_clear_cart(callback.from_user.id)
    await callback.message.edit_text("🛒 Savatingiz tozalandi.")
    await callback.answer()

# --- BUYURTMA RASMIYLASHTIRISH ---

@dp.callback_query(F.data == "checkout")
async def checkout(callback: CallbackQuery, state: FSMContext):
    cart_items = db_get_cart(callback.from_user.id)
    if not cart_items:
        await callback.answer("Savatingiz bo'sh!", show_alert=True)
        return

    await callback.message.answer(
        "Aloqa uchun qo'shimcha telefon raqamingizni kiriting:\n\n💡 <i>Masalan:</i> +998901234567",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(OrderState.waiting_for_extra_phone)
    await callback.answer()

@dp.message(OrderState.waiting_for_extra_phone)
async def get_extra_phone(message: types.Message, state: FSMContext):
    phone_text = message.text.strip()
    clean_phone = re.sub(r'[^\d]', '', phone_text)
    
    if not (clean_phone.isdigit() and 9 <= len(clean_phone) <= 12):
        await message.answer("⚠️ <b>Raqam noto'g'ri!</b> Masalan: +998901234567", parse_mode="HTML")
        return

    await state.update_data(extra_phone=phone_text)
    await message.answer("📍 <b>Yetkazib berish manzilini kiriting:</b>\n\n💡 <i>Misol:</i> Toshloq tumani, Yakkatut", parse_mode="HTML")
    await state.set_state(OrderState.waiting_for_address)

@dp.message(OrderState.waiting_for_address)
async def get_address(message: types.Message, state: FSMContext):
    address_text = message.text.strip()
    if not any(c.isalpha() for c in address_text) or len(address_text) < 5:
        await message.answer("⚠️ <b>Manzil xato!</b> Matn ko'rinishida kiriting.", parse_mode="HTML")
        return

    user_data = await state.get_data()
    main_phone = user_data.get("main_phone", "Ko'rsatilmadi")
    extra_phone = user_data.get("extra_phone")
    user_id = message.from_user.id
    
    cart_items = db_get_cart(user_id)

    items_text = ""
    total_price = 0
    for p_id, name, price, qty in cart_items:
        sum_price = price * qty
        total_price += sum_price
        items_text += f"• {name} ({qty} ta) - {sum_price:,} so'm\n"

    admin_text = (
        "📥 <b>Yangi buyurtma keldi!</b>\n\n"
        f"👤 <b>Mijoz:</b> {message.from_user.full_name} (@{message.from_user.username or 'yo_q'})\n"
        f"📱 <b>Asosiy raqami:</b> {main_phone}\n"
        f"📞 <b>Qo'shimcha raqami:</b> {extra_phone}\n"
        f"📍 <b>Manzil:</b> {address_text}\n\n"
        f"🛒 <b>Mahsulotlar:</b>\n{items_text}\n"
        f"💵 <b>Jami summa:</b> {total_price:,} so'm"
    )

    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="HTML")
        db_clear_cart(user_id)
        await message.answer("✅ Rahmat! Buyurtmangiz qabul qilindi.", reply_markup=get_main_menu(user_id))
    except Exception:
        await message.answer("❌ Buyurtma yuborishda xatolik yuz berdi.", reply_markup=get_main_menu(user_id))
    
    await state.clear()

# --- ADMIN: MAHSULOT QO'SHISH SHABLONI (FSM) ---

@dp.message(F.text == "➕ Mahsulot qo'shish (Admin)")
async def admin_start_add(message: types.Message, state: FSMContext):
    if int(message.from_user.id) != int(ADMIN_ID):
        return

    buttons = []
    for cat_key, cat_name in CATEGORIES.items():
        buttons.append([InlineKeyboardButton(text=cat_name, callback_data=f"admcat_{cat_key}")])
    buttons.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_admin")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Qaysi kategoriyaga mahsulot qo'shmoqchisiz?", reply_markup=kb)
    await state.set_state(AdminAddProduct.waiting_for_category)

@dp.callback_query(F.data == "cancel_admin")
async def cancel_admin(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Jarayon bekor qilindi.", reply_markup=get_main_menu(callback.from_user.id))
    await callback.answer()

@dp.callback_query(AdminAddProduct.waiting_for_category, F.data.startswith("admcat_"))
async def admin_select_category(callback: CallbackQuery, state: FSMContext):
    cat_key = callback.data.split("_")[1]
    await state.update_data(admin_category=cat_key)
    
    await callback.message.delete()
    await callback.message.answer("🖼 Mahsulot rasmini yuboring:")
    await state.set_state(AdminAddProduct.waiting_for_photo)
    await callback.answer()

@dp.message(AdminAddProduct.waiting_for_photo, F.photo)
async def admin_get_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(admin_photo_id=photo_id)
    
    await message.answer("✏️ Mahsulot nomini kiriting:")
    await state.set_state(AdminAddProduct.waiting_for_name)

@dp.message(AdminAddProduct.waiting_for_name)
async def admin_get_name(message: types.Message, state: FSMContext):
    await state.update_data(admin_name=message.text)
    await message.answer("📝 Mahsulot haqida qisqacha izoh/tavsif kiriting:")
    await state.set_state(AdminAddProduct.waiting_for_desc)

@dp.message(AdminAddProduct.waiting_for_desc)
async def admin_get_desc(message: types.Message, state: FSMContext):
    await state.update_data(admin_desc=message.text)
    await message.answer("💰 Mahsulot narxini kiriting (faqat raqamda, masalan: 120000):")
    await state.set_state(AdminAddProduct.waiting_for_price)

@dp.message(AdminAddProduct.waiting_for_price)
async def admin_get_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Iltimos, narxni faqat raqamlarda kiriting (masalan: 120000):")
        return

    price = int(message.text)
    data = await state.get_data()

    add_product_db(
        category=data["admin_category"],
        name=data["admin_name"],
        description=data["admin_desc"],
        price=price,
        photo_id=data["admin_photo_id"]
    )

    await message.answer(
        f"✅ <b>Mahsulot bazaga muvaffaqiyatli qo'shildi!</b>\n\n"
        f"📌 Nomi: {data['admin_name']}\n"
        f"💰 Narxi: {price:,} so'm",
        reply_markup=get_main_menu(message.from_user.id),
        parse_mode="HTML"
    )
    await state.clear()

# --- BOTNI ISHGA TUSHIRISH ---
async def main():
    init_db()
    print("Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())