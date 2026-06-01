"""
Brick Service Bot — MVP
Запуск: python bot.py
Нужен файл .env рядом:
    BOT_TOKEN=...
    ADMIN_ID=...
    MASTER_БАТУМИ=telegram_id
    MASTER_ТБИЛИСИ=telegram_id
    MASTER_ТРАПЗОН=telegram_id
    MASTER_САНКТ-ПЕТЕРБУРГ=telegram_id
"""

import asyncio, logging, os, re, aiosqlite
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

load_dotenv()

# ─────────────────────────── CONFIG ───────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID   = int(os.getenv("ADMIN_ID", "0"))
DB_PATH    = "brick.db"

CITIES   = ["Батуми", "Тбилиси", "Трапзон", "Санкт-Петербург", "Другой город"]
SERVICES = ["Ремонт", "Консультация", "Скупка техники", "Другое"]
DEVICES  = [
    "Телефон", "Планшет", "Ноутбук", "ПК",
    "Игровая приставка", "Джойстик", "Дрон",
    "Монитор / телевизор", "Dyson фен / стайлер",
    "Dyson пылесос", "Робот-пылесос", "Другое",
]
PROBLEMS = {
    "Телефон":            ["Замена дисплея","Замена стекла","Замена аккумулятора","Ремонт разъёма","Замена задней крышки","Ремонт платы","После воды","После удара","Другое"],
    "Планшет":            ["Замена дисплея","Замена стекла","Замена аккумулятора","Ремонт разъёма","Ремонт платы","После воды","После удара","Другое"],
    "Ноутбук":            ["Замена экрана","Замена клавиатуры","Замена аккумулятора","Чистка / термопаста","Ремонт разъёма питания","Ремонт платы","Не включается","После воды","Другое"],
    "ПК":                 ["Не включается","Чистка / термопаста","Замена комплектующих","Ремонт платы","Другое"],
    "Игровая приставка":  ["Не читает диски","Не включается","Замена джойстика","Ремонт разъёма","Другое"],
    "Джойстик":           ["Дрейф стика","Не заряжается","Не работают кнопки","Другое"],
    "Дрон":               ["Ремонт моторов","Замена платы","Ремонт камеры","Другое"],
    "Монитор / телевизор":["Замена матрицы","Не включается","Нет изображения","Ремонт платы","Другое"],
    "Dyson фен / стайлер":["Не включается","Слабый нагрев","Ремонт платы","Другое"],
    "Dyson пылесос":      ["Слабое всасывание","Не заряжается","Не включается","Другое"],
    "Робот-пылесос":      ["Не заряжается","Не едет","Ремонт платы","Другое"],
    "Другое":             ["Другое"],
}
INTAKE_SIDES = ["Перед", "Зад", "Верх", "Низ", "Лево", "Право"]
STATUSES = {
    "new":        "🆕 Новая",
    "processing": "🔄 В обработке",
    "accepted":   "📥 Принят в ремонт",
    "diagnostic": "🔍 Диагностика",
    "in_repair":  "🔧 В ремонте",
    "waiting":    "⏳ Ожидает запчасть",
    "ready":      "✅ Готов",
    "issued":     "📦 Выдан",
    "closed":     "🔒 Закрыт",
}
STATUS_FLOW = ["processing", "accepted", "diagnostic", "in_repair", "waiting", "ready"]


def get_master_id(city: str) -> int | None:
    key = "MASTER_" + city.upper().replace(" ", "_").replace("-", "_")
    v = os.getenv(key)
    return int(v) if v else None


# ─────────────────────────── DATABASE ─────────────────────────────────────────

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id    INTEGER NOT NULL,
                master_id    INTEGER,
                city         TEXT NOT NULL,
                service      TEXT NOT NULL,
                device       TEXT,
                problem      TEXT,
                brand_model  TEXT,
                prev_repairs TEXT,
                description  TEXT,
                status       TEXT NOT NULL DEFAULT 'new',
                cost_repair  REAL,
                cost_parts   REAL,
                profit       REAL,
                created_at   TEXT DEFAULT (datetime('now')),
                accepted_at  TEXT,
                ready_at     TEXT,
                issued_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS photos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id   INTEGER NOT NULL,
                stage      TEXT NOT NULL,
                file_id    TEXT NOT NULL,
                side       TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id   INTEGER NOT NULL,
                from_role  TEXT NOT NULL,
                text       TEXT,
                file_id    TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        await db.commit()


async def db_create_order(client_id, city, service) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders (client_id, city, service) VALUES (?,?,?)",
            (client_id, city, service)
        )
        await db.commit()
        return cur.lastrowid


async def db_update(order_id, **kw):
    sets = ", ".join(f"{k}=?" for k in kw)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE orders SET {sets} WHERE id=?", [*kw.values(), order_id])
        await db.commit()


async def db_get(order_id) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE id=?", (order_id,)) as c:
            r = await c.fetchone()
            return dict(r) if r else None


async def db_add_photo(order_id, stage, file_id, side=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO photos (order_id, stage, file_id, side) VALUES (?,?,?,?)",
            (order_id, stage, file_id, side)
        )
        await db.commit()


async def db_photo_count(order_id, stage) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM photos WHERE order_id=? AND stage=?", (order_id, stage)
        ) as c:
            return (await c.fetchone())[0]


async def db_save_msg(order_id, role, text=None, file_id=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (order_id, from_role, text, file_id) VALUES (?,?,?,?)",
            (order_id, role, text, file_id)
        )
        await db.commit()


async def db_pending() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM orders WHERE status='new' ORDER BY id"
        ) as c:
            return [dict(r) for r in await c.fetchall()]


# ─────────────────────────── HELPERS ──────────────────────────────────────────

_CONTACT_RE = re.compile(
    r"(\+?\d[\d\s\-\(\)]{6,}\d|https?://\S+|t\.me/\S+|@[A-Za-z0-9_]{3,})"
)

def has_contact(text: str) -> bool:
    return bool(_CONTACT_RE.search(text or ""))

CONTACT_WARN = "🚫 Сообщение не доставлено — номера, ссылки и @username запрещены. Общение только через бота."


def fmt_order(o: dict, role="client") -> str:
    st = STATUSES.get(o["status"], o["status"])
    lines = [
        f"📋 <b>Заявка #{o['id']}</b>",
        f"🏙 {o['city']} · {o['service']}",
    ]
    if o.get("device"):  lines.append(f"📱 {o['device']}")
    if o.get("problem"): lines.append(f"❗ {o['problem']}")
    if o.get("brand_model"): lines.append(f"🏷 {o['brand_model']}")
    if o.get("description"):  lines.append(f"📝 {o['description']}")
    if o.get("prev_repairs"): lines.append(f"🔄 Ранее: {o['prev_repairs']}")
    lines.append(f"\n📊 <b>{st}</b>")
    if role == "master": lines.append(f"👤 Клиент ID#{o['client_id']}")
    if o.get("cost_repair") is not None:
        lines.append(f"💰 Ремонт: {o['cost_repair']} · Запчасти: {o.get('cost_parts',0)} · Прибыль: {o.get('profit',0)}")
    return "\n".join(lines)


# ─────────────────────────── KEYBOARDS ────────────────────────────────────────

def kb_list(items: list[str], prefix: str, cols=2) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for item in items:
        b.button(text=item, callback_data=f"{prefix}:{item}")
    b.adjust(cols)
    return b.as_markup()


def kb_yesno(prefix: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Да", callback_data=f"{prefix}:yes")
    b.button(text="Нет", callback_data=f"{prefix}:no")
    return b.as_markup()


def kb_done(label="✅ Готово") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=label, callback_data="done")
    return b.as_markup()


def kb_client(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💬 Написать мастеру", callback_data=f"cchat:{order_id}")
    b.button(text="📋 Мой статус",       callback_data=f"cstatus:{order_id}")
    b.adjust(1)
    return b.as_markup()


def kb_master(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Принять",          callback_data=f"maccept:{order_id}")
    b.button(text="❌ Отказаться",        callback_data=f"mdecline:{order_id}")
    b.button(text="💬 Написать клиенту", callback_data=f"mchat:{order_id}")
    b.button(text="📊 Изменить статус",  callback_data=f"mstatus:{order_id}")
    b.button(text="📸 Фото",            callback_data=f"mphoto:{order_id}")
    b.button(text="💰 Стоимость",       callback_data=f"mcost:{order_id}")
    b.button(text="📦 Клиент забрал",   callback_data=f"missued:{order_id}")
    b.adjust(2)
    return b.as_markup()


def kb_statuses(order_id: int, current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in STATUS_FLOW:
        label = ("› " if s == current else "") + STATUSES[s]
        b.button(text=label, callback_data=f"msetstatus:{order_id}:{s}")
    b.button(text="◀ Назад", callback_data=f"mback:{order_id}")
    b.adjust(1)
    return b.as_markup()


def kb_photo_stage(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📸 Приём (6 сторон)",   callback_data=f"mps:{order_id}:intake")
    b.button(text="🔧 Процесс ремонта",    callback_data=f"mps:{order_id}:process")
    b.button(text="✅ Готовое устройство", callback_data=f"mps:{order_id}:done")
    b.button(text="◀ Назад",              callback_data=f"mback:{order_id}")
    b.adjust(1)
    return b.as_markup()


# ─────────────────────────── FSM STATES ───────────────────────────────────────

class Order(StatesGroup):
    city        = State()
    service     = State()
    device      = State()
    problem     = State()
    brand       = State()
    prev        = State()
    prev_detail = State()
    desc        = State()
    photos      = State()

class Chat(StatesGroup):
    waiting = State()   # data: order_id, role

class Cost(StatesGroup):
    repair = State()
    parts  = State()

class Photo(StatesGroup):
    intake  = State()   # data: order_id, idx
    process = State()
    done    = State()


# ─────────────────────────── ROUTER ───────────────────────────────────────────

router = Router()


# ── /start ─────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("👋 Добро пожаловать в <b>Brick Service</b>!\n\nВыберите город:",
                     reply_markup=kb_list(CITIES, "city"))
    await state.set_state(Order.city)


# ── Заявка: город → услуга → устройство → проблема ────────────────────────────

@router.callback_query(Order.city, F.data.startswith("city:"))
async def step_city(cb: CallbackQuery, state: FSMContext):
    city = cb.data.split(":",1)[1]
    await state.update_data(city=city)
    await cb.message.edit_text(f"🏙 <b>{city}</b>\n\nЧто нужно?", reply_markup=kb_list(SERVICES, "svc"))
    await state.set_state(Order.service)
    await cb.answer()


@router.callback_query(Order.service, F.data.startswith("svc:"))
async def step_service(cb: CallbackQuery, state: FSMContext):
    svc = cb.data.split(":",1)[1]
    await state.update_data(service=svc)
    if svc == "Ремонт":
        await cb.message.edit_text("📱 Выберите устройство:", reply_markup=kb_list(DEVICES, "dev"))
        await state.set_state(Order.device)
    else:
        await cb.message.edit_text("📝 Опишите запрос:")
        await state.set_state(Order.desc)
    await cb.answer()


@router.callback_query(Order.device, F.data.startswith("dev:"))
async def step_device(cb: CallbackQuery, state: FSMContext):
    dev = cb.data.split(":",1)[1]
    await state.update_data(device=dev)
    probs = PROBLEMS.get(dev, ["Другое"])
    await cb.message.edit_text(f"📱 <b>{dev}</b>\n\nВыберите проблему:", reply_markup=kb_list(probs, "prob"))
    await state.set_state(Order.problem)
    await cb.answer()


@router.callback_query(Order.problem, F.data.startswith("prob:"))
async def step_problem(cb: CallbackQuery, state: FSMContext):
    await state.update_data(problem=cb.data.split(":",1)[1])
    await cb.message.edit_text("🏷 Марка и модель (например: iPhone 14 Pro):")
    await state.set_state(Order.brand)
    await cb.answer()


@router.message(Order.brand)
async def step_brand(msg: Message, state: FSMContext):
    await state.update_data(brand_model=msg.text)
    await msg.answer("🔄 Устройство уже было в ремонте ранее?", reply_markup=kb_yesno("prev"))
    await state.set_state(Order.prev)


@router.callback_query(Order.prev, F.data.startswith("prev:"))
async def step_prev(cb: CallbackQuery, state: FSMContext):
    if cb.data.endswith(":yes"):
        await cb.message.edit_text("📝 Опишите, какие ремонты были ранее:")
        await state.set_state(Order.prev_detail)
    else:
        await cb.message.edit_text("📝 Опишите проблему подробнее (или /skip):")
        await state.set_state(Order.desc)
    await cb.answer()


@router.message(Order.prev_detail)
async def step_prev_detail(msg: Message, state: FSMContext):
    await state.update_data(prev_repairs=msg.text)
    await msg.answer("📝 Опишите проблему подробнее (или /skip):")
    await state.set_state(Order.desc)


@router.message(Order.desc)
async def step_desc(msg: Message, state: FSMContext):
    if msg.text != "/skip":
        await state.update_data(description=msg.text)
    await _ask_photos(msg, state)


@router.message(Command("skip"), Order.desc)
async def step_skip(msg: Message, state: FSMContext):
    await _ask_photos(msg, state)


async def _ask_photos(msg: Message, state: FSMContext):
    data = await state.get_data()
    oid  = await db_create_order(msg.from_user.id, data["city"], data["service"])
    await db_update(oid,
        device=data.get("device"), problem=data.get("problem"),
        brand_model=data.get("brand_model"), prev_repairs=data.get("prev_repairs"),
        description=data.get("description"),
    )
    await state.update_data(order_id=oid, photo_count=0)
    await msg.answer(
        "📸 Прикрепите фото/видео устройства. Когда закончите — нажмите <b>Готово</b>.",
        reply_markup=kb_done()
    )
    await state.set_state(Order.photos)


@router.message(Order.photos, F.photo | F.video | F.document)
async def collect_photo(msg: Message, state: FSMContext):
    data = await state.get_data()
    fid  = (msg.photo[-1] if msg.photo else msg.video or msg.document).file_id
    await db_add_photo(data["order_id"], "client", fid)
    n = data.get("photo_count", 0) + 1
    await state.update_data(photo_count=n)
    await msg.answer(f"✅ Фото {n} сохранено.", reply_markup=kb_done())


@router.callback_query(Order.photos, F.data == "done")
async def order_done(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data  = await state.get_data()
    oid   = data["order_id"]
    order = await db_get(oid)
    await state.clear()

    await cb.message.edit_text(fmt_order(order), parse_mode="HTML", reply_markup=kb_client(oid))

    master_id = get_master_id(order["city"])
    if master_id:
        await db_update(oid, master_id=master_id)
        await bot.send_message(master_id,
            f"🔔 <b>Новая заявка!</b>\n\n{fmt_order(order,'master')}",
            reply_markup=kb_master(oid))
    else:
        await bot.send_message(ADMIN_ID,
            f"⚠️ Заявка #{oid} ({order['city']}) — мастер не назначен!\n\n{fmt_order(order,'master')}")
    await cb.answer("Заявка отправлена!")


# ── Клиент: статус и чат ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cstatus:"))
async def client_status(cb: CallbackQuery):
    order = await db_get(int(cb.data.split(":")[1]))
    await cb.message.answer(fmt_order(order), reply_markup=kb_client(order["id"]))
    await cb.answer()


@router.callback_query(F.data.startswith("cchat:"))
async def client_chat_start(cb: CallbackQuery, state: FSMContext):
    oid = int(cb.data.split(":")[1])
    await state.set_state(Chat.waiting)
    await state.update_data(order_id=oid, role="client")
    await cb.message.answer("✏️ Пишите — сообщение уйдёт мастеру. /cancel для отмены.")
    await cb.answer()


# ── Мастер: принять / отказ ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("maccept:"))
async def master_accept(cb: CallbackQuery, bot: Bot):
    oid   = int(cb.data.split(":")[1])
    now   = datetime.now().isoformat(timespec="seconds")
    await db_update(oid, status="processing", master_id=cb.from_user.id, accepted_at=now)
    order = await db_get(oid)
    await cb.message.edit_text(fmt_order(order,"master"), reply_markup=kb_master(oid))
    await bot.send_message(order["client_id"],
        f"🔄 Мастер принял заявку #{oid}. Статус: <b>В обработке</b>",
        reply_markup=kb_client(oid))
    await cb.answer("Принято!")


@router.callback_query(F.data.startswith("mdecline:"))
async def master_decline(cb: CallbackQuery, bot: Bot):
    oid   = int(cb.data.split(":")[1])
    order = await db_get(oid)
    await db_update(oid, status="new", master_id=None)
    await cb.message.edit_text(f"❌ Заявка #{oid} отклонена.")
    await bot.send_message(order["client_id"], f"ℹ️ Мастер временно недоступен. Заявка #{oid} будет переназначена.")
    await bot.send_message(ADMIN_ID, f"⚠️ Мастер отклонил заявку #{oid}. Нужно переназначить.")
    await cb.answer()


# ── Мастер: статус ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mstatus:"))
async def master_status_menu(cb: CallbackQuery):
    oid   = int(cb.data.split(":")[1])
    order = await db_get(oid)
    await cb.message.edit_text(f"📊 Статус заявки #{oid}:", reply_markup=kb_statuses(oid, order["status"]))
    await cb.answer()


@router.callback_query(F.data.startswith("msetstatus:"))
async def master_set_status(cb: CallbackQuery, bot: Bot):
    _, oid_s, new_st = cb.data.split(":")
    oid = int(oid_s)

    if new_st == "ready":
        count = await db_photo_count(oid, "process")
        order = await db_get(oid)
        req = 6 if any(w in (order.get("problem") or "") for w in ["дисплей","корпус"]) else 3
        if count < req:
            await cb.answer(f"❗ Нужно минимум {req} фото процесса. Сейчас: {count}", show_alert=True)
            return

    extras = {"ready_at": datetime.now().isoformat(timespec="seconds")} if new_st == "ready" else {}
    await db_update(oid, status=new_st, **extras)
    order = await db_get(oid)
    await cb.message.edit_text(fmt_order(order,"master"), reply_markup=kb_master(oid))
    await bot.send_message(order["client_id"],
        f"🔄 Статус заявки #{oid}: <b>{STATUSES.get(new_st, new_st)}</b>",
        reply_markup=kb_client(oid))
    await cb.answer()


# ── Мастер: фото ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mphoto:"))
async def master_photo_menu(cb: CallbackQuery):
    oid = int(cb.data.split(":")[1])
    await cb.message.edit_text(f"📸 Фото для заявки #{oid}:", reply_markup=kb_photo_stage(oid))
    await cb.answer()


@router.callback_query(F.data.startswith("mps:"))
async def master_photo_stage(cb: CallbackQuery, state: FSMContext):
    _, oid_s, stage = cb.data.split(":")
    oid = int(oid_s)
    await state.update_data(order_id=oid, stage=stage, intake_idx=0)
    if stage == "intake":
        await cb.message.edit_text(f"📸 Отправьте фото — <b>{INTAKE_SIDES[0]}</b> (1/6)")
        await state.set_state(Photo.intake)
    else:
        n = await db_photo_count(oid, stage)
        await cb.message.edit_text(f"📸 Уже загружено: {n}. Отправляйте фото, затем нажмите <b>Готово</b>.",
                                   reply_markup=kb_done("✅ Закончить"))
        await state.set_state(Photo.process if stage == "process" else Photo.done)
    await cb.answer()


@router.message(Photo.intake, F.photo | F.document)
async def photo_intake(msg: Message, state: FSMContext):
    data = await state.get_data()
    oid, idx = data["order_id"], data.get("intake_idx", 0)
    fid  = (msg.photo[-1] if msg.photo else msg.document).file_id
    await db_add_photo(oid, "intake", fid, side=INTAKE_SIDES[idx])
    idx += 1
    await state.update_data(intake_idx=idx)
    if idx < 6:
        await msg.answer(f"✅ {INTAKE_SIDES[idx-1]}\n📸 Теперь — <b>{INTAKE_SIDES[idx]}</b> ({idx+1}/6)")
    else:
        await state.clear()
        await msg.answer("✅ Все 6 фото приёма сохранены!", reply_markup=kb_master(oid))


@router.message(Photo.process | Photo.done, F.photo | F.video | F.document)
async def photo_free(msg: Message, state: FSMContext):
    data  = await state.get_data()
    oid   = data["order_id"]
    stage = data["stage"]
    fid   = (msg.photo[-1] if msg.photo else msg.video or msg.document).file_id
    await db_add_photo(oid, stage, fid)
    n = await db_photo_count(oid, stage)
    await msg.answer(f"✅ Фото сохранено. Всего: {n}.", reply_markup=kb_done("✅ Закончить"))


@router.callback_query(Photo.process | Photo.done, F.data == "done")
async def photo_free_done(cb: CallbackQuery, state: FSMContext):
    oid = (await state.get_data())["order_id"]
    await state.clear()
    await cb.message.edit_text("✅ Фото сохранены.", reply_markup=kb_master(oid))
    await cb.answer()


# ── Мастер: стоимость ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mcost:"))
async def master_cost_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Cost.repair)
    await state.update_data(order_id=int(cb.data.split(":")[1]))
    await cb.message.answer("💰 Стоимость ремонта (число):")
    await cb.answer()


@router.message(Cost.repair)
async def cost_repair(msg: Message, state: FSMContext):
    try:
        v = float(msg.text.replace(",","."))
    except ValueError:
        await msg.answer("⚠️ Введите число")
        return
    await state.update_data(cost_repair=v)
    await msg.answer("🔩 Стоимость запчастей (0 если нет):")
    await state.set_state(Cost.parts)


@router.message(Cost.parts)
async def cost_parts(msg: Message, state: FSMContext, bot: Bot):
    try:
        parts = float(msg.text.replace(",","."))
    except ValueError:
        await msg.answer("⚠️ Введите число")
        return
    data   = await state.get_data()
    oid    = data["order_id"]
    repair = data["cost_repair"]
    profit = repair - parts
    await db_update(oid, cost_repair=repair, cost_parts=parts, profit=profit)
    await state.clear()
    order = await db_get(oid)
    await msg.answer(f"✅ Сохранено: ремонт {repair} · запчасти {parts} · прибыль {profit}",
                     reply_markup=kb_master(oid))
    await bot.send_message(order["client_id"],
        f"💰 Стоимость по заявке #{oid}: ремонт <b>{repair} ₽</b>, запчасти <b>{parts} ₽</b>",
        reply_markup=kb_client(oid))


# ── Мастер: выдача ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("missued:"))
async def master_issued(cb: CallbackQuery, bot: Bot):
    oid   = int(cb.data.split(":")[1])
    order = await db_get(oid)
    if order["status"] != "ready":
        await cb.answer("⚠️ Сначала переведите в статус 'Готов'", show_alert=True)
        return
    now = datetime.now().isoformat(timespec="seconds")
    await db_update(oid, status="issued", issued_at=now)
    await cb.message.edit_text(f"📦 Заявка #{oid} — устройство выдано.")
    await bot.send_message(order["client_id"],
        f"✅ Устройство выдано! Спасибо что выбрали Brick Service 🙏\n"
        f"Скоро попросим оставить отзыв.",
        reply_markup=kb_client(oid))
    await cb.answer("Выдача зафиксирована!")


# ── Назад ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("mback:"))
async def master_back(cb: CallbackQuery, state: FSMContext):
    oid   = int(cb.data.split(":")[1])
    order = await db_get(oid)
    await state.clear()
    await cb.message.edit_text(fmt_order(order,"master"), reply_markup=kb_master(oid))
    await cb.answer()


# ── Чат (общий для клиента и мастера) ─────────────────────────────────────────

@router.callback_query(F.data.startswith("mchat:"))
async def master_chat_start(cb: CallbackQuery, state: FSMContext):
    oid = int(cb.data.split(":")[1])
    await state.set_state(Chat.waiting)
    await state.update_data(order_id=oid, role="master")
    await cb.message.answer("✏️ Пишите — сообщение уйдёт клиенту. /cancel для отмены.")
    await cb.answer()


@router.message(Chat.waiting)
async def relay(msg: Message, state: FSMContext, bot: Bot):
    data  = await state.get_data()
    oid   = data["order_id"]
    role  = data["role"]
    text  = msg.text or msg.caption or ""

    if has_contact(text):
        await msg.answer(CONTACT_WARN)
        return

    order = await db_get(oid)
    fid   = (msg.photo[-1].file_id if msg.photo else
             msg.video.file_id     if msg.video else None)
    await db_save_msg(oid, role, text, fid)

    target = order["master_id"] if role == "client" else order["client_id"]
    label  = f"📩 {'Клиент' if role=='client' else 'Мастер'} (#{oid}):\n{text}"
    if not target:
        await msg.answer("⚠️ Собеседник ещё не назначен.")
        return
    if fid and msg.photo:
        await bot.send_photo(target, fid, caption=label)
    elif fid and msg.video:
        await bot.send_video(target, fid, caption=label)
    else:
        await bot.send_message(target, label)
    await msg.answer("✅ Доставлено.")


# ── Админ ──────────────────────────────────────────────────────────────────────

@router.message(Command("pending"))
async def cmd_pending(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    orders = await db_pending()
    if not orders:
        await msg.answer("Нет новых заявок без мастера.")
        return
    for o in orders:
        await msg.answer(fmt_order(o,"master"), reply_markup=kb_master(o["id"]))


@router.message(Command("assign"))
async def cmd_assign(msg: Message, bot: Bot):
    if msg.from_user.id != ADMIN_ID: return
    parts = msg.text.split()
    if len(parts) != 3:
        await msg.answer("Использование: /assign <order_id> <master_tg_id>")
        return
    oid, mid = int(parts[1]), int(parts[2])
    order = await db_get(oid)
    if not order:
        await msg.answer(f"Заявка #{oid} не найдена")
        return
    await db_update(oid, master_id=mid)
    await msg.answer(f"✅ Заявка #{oid} → мастер {mid}")
    await bot.send_message(mid, f"🔔 <b>Новая заявка!</b>\n\n{fmt_order(order,'master')}",
                           reply_markup=kb_master(oid))


# ── /cancel ────────────────────────────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отменено. /start — новая заявка.")


# ─────────────────────────── MAIN ─────────────────────────────────────────────

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not BOT_TOKEN:
        raise RuntimeError("Укажите BOT_TOKEN в .env")
    await db_init()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
