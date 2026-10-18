import asyncio
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from budget import BudgetService
from env import (
    BOT_PASSWORD,
    POLL_INTERVAL_SECONDS,
    TELEGRAM_BOT_TOKEN,
    WEBHOOK_HOST,
    WEBHOOK_PORT,
    WEBHOOK_PUBLIC_URL,
)
from models import CURRENCIES, MonobankAPI
from storage import Storage

logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("mono-bot")

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["✏️ Annotate", "💰 Budget"],
        ["🧾 Transactions", "💱 Currency"],
        ["⚙️ Settings"],
    ],
    resize_keyboard=True,
)

CANCEL_KB = ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)

CURRENCY_FLAGS = {"USD": "🇺🇸", "EUR": "🇪🇺", "UAH": "🇺🇦"}
ANNOTATE_CURRENCIES = ["UAH", "USD", "EUR"]
QUICK_AMOUNTS = [50, 100, 200, 500, 1000]


def flag(cur):
    return CURRENCY_FLAGS.get(cur, "💱")


def home_btn():
    return InlineKeyboardButton("🏠 Main menu", callback_data="home")


def back_btn(callback_data):
    return InlineKeyboardButton("🔙 Back", callback_data=callback_data)


def with_nav(rows):
    rows.append([home_btn()])
    return InlineKeyboardMarkup(rows)


def fmt_amount(amount_kop, currency):
    sign = "+" if amount_kop >= 0 else "−"
    return f"{sign}{abs(amount_kop) / 100:.2f} {currency}"


def fmt_uah(kop):
    sign = "+" if kop >= 0 else "−"
    return f"{sign}{abs(kop) / 100:.2f} ₴"


class MonoBot:
    def __init__(self):
        self.storage = Storage()
        self.api = MonobankAPI()
        self.budget = BudgetService(self.storage, self.api)
        self.app = None
        self._loop = None
        self._short_ids = {}
        self._states = {}

    # ---------- state helpers ----------

    def set_state(self, chat_id, state, **data):
        self._states[chat_id] = {"state": state, **data}

    def pop_state(self, chat_id):
        return self._states.pop(chat_id, None)

    def get_state(self, chat_id):
        return self._states.get(chat_id)

    def _short_tx_id(self, tx_key):
        for short, key in self._short_ids.items():
            if key == tx_key:
                return short
        short = f"{len(self._short_ids):04x}"
        self._short_ids[short] = tx_key
        return short

    # ---------- menus (every one ends with 🏠) ----------

    def budget_menu(self):
        return with_nav([
            [InlineKeyboardButton("💵 Balance", callback_data="bud:balance"),
             InlineKeyboardButton("🏷 Categories", callback_data="bud:cats")],
            [InlineKeyboardButton("➕ Add category", callback_data="bud:addcat"),
             InlineKeyboardButton("🗑 Delete category", callback_data="bud:delcat")],
        ])

    def tx_menu(self):
        return with_nav([
            [InlineKeyboardButton("📥 Unannotated", callback_data="tx:pending"),
             InlineKeyboardButton("📜 History", callback_data="tx:history")],
        ])

    def currency_menu(self):
        return with_nav([
            [InlineKeyboardButton("📈 Rates", callback_data="cur:rates"),
             InlineKeyboardButton("🔄 Converter", callback_data="cur:conv")],
        ])

    def settings_menu(self):
        return with_nav([
            [InlineKeyboardButton("🏦 Accounts", callback_data="set:accounts")],
            [InlineKeyboardButton("🔔 Set webhook", callback_data="set:wh_on"),
             InlineKeyboardButton("🔕 Remove webhook", callback_data="set:wh_off")],
            [InlineKeyboardButton("❔ Help", callback_data="set:help")],
        ])

    def annotate_kind_menu(self):
        return with_nav([
            [InlineKeyboardButton("📈 Income", callback_data="ann:in"),
             InlineKeyboardButton("📉 Spend", callback_data="ann:out")],
            [back_btn("home")],
        ])

    # ---------- auth ----------

    async def is_authorized(self, chat_id):
        return await asyncio.to_thread(self.storage.is_authorized, chat_id)

    def require_auth(self, handler):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat_id = update.effective_chat.id
            if not await self.is_authorized(chat_id):
                await asyncio.to_thread(self.budget.register_user, chat_id)
                await self.ask_password(update, chat_id)
                return
            return await handler(update, context)
        return wrapper

    async def ask_password(self, update, chat_id, wrong=False):
        self.set_state(chat_id, "login")
        prefix = "❌ *Wrong password.*\n\n" if wrong else ""
        await update.message.reply_text(
            f"{prefix}🔒 *MeinGeld Bot is locked*\n\n"
            "Please send the password to continue.",
            parse_mode="Markdown",
        )

    async def welcome(self, update):
        await update.message.reply_text(
            "👋 *MeinGeld Bot*\n\n"
            "Track where every 💰 goes — no notes, just taps.\n\n"
            "👉 *✏️ Annotate* — record a spend or income in 4 taps\n"
            "👉 *💰 Budget* — categories & balances\n"
            "👉 *🧾 Transactions* — Monobank history\n"
            "👉 *💱 Currency* — rates & converter\n\n"
            "Pick a button below 👇",
            parse_mode="Markdown",
            reply_markup=MAIN_MENU,
        )

    # ---------- entry points ----------

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        await asyncio.to_thread(self.budget.register_user, chat_id)
        self.pop_state(chat_id)
        if await self.is_authorized(chat_id):
            await self.welcome(update)
        else:
            await self.ask_password(update, chat_id)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *MeinGeld Bot*\n\n"
            "✏️ *Annotate* — spend/income → category → currency → amount\n"
            "💰 *Budget* — balances, add/delete categories\n"
            "🧾 *Transactions* — annotate bank spends/incomes, history\n"
            "💱 *Currency* — Monobank rates & converter\n"
            "⚙️ *Settings* — accounts, webhook\n\n"
            "🏠 *Main menu* — always available\n"
            "❌ *Cancel* — aborts any input\n\n"
            "Fast: `/ext +500 Aliments` · `/convert 100 USD UAH`",
            parse_mode="Markdown",
            reply_markup=MAIN_MENU,
        )

    # ---------- main menu router ----------

    async def on_menu_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text.strip()

        if not await self.is_authorized(chat_id):
            await asyncio.to_thread(self.budget.register_user, chat_id)
            if text == BOT_PASSWORD:
                await asyncio.to_thread(self.storage.set_authorized, chat_id, 1)
                self.pop_state(chat_id)
                await update.message.reply_text("✅ *Access granted!*", parse_mode="Markdown")
                await self.welcome(update)
            else:
                await self.ask_password(update, chat_id, wrong=True)
            return

        if text in ("❌ Cancel", "Cancel", "/cancel"):
            self.pop_state(chat_id)
            await update.message.reply_text("❌ Cancelled", reply_markup=MAIN_MENU)
            return

        menu_labels = ("✏️ Annotate", "💰 Budget", "🧾 Transactions", "💱 Currency", "⚙️ Settings")
        if text in menu_labels:
            self.pop_state(chat_id)

        state = self.get_state(chat_id)
        if state:
            handled = await self.handle_state_input(update, context, state)
            if handled:
                return

        if text in ("✏️ Annotate", "Annotate", "/annotate"):
            await self.start_annotate(update.message, chat_id)
        elif text in ("💰 Budget", "Budget", "/balance"):
            await update.message.reply_text("💰 *Budget*", parse_mode="Markdown", reply_markup=self.budget_menu())
        elif text in ("🧾 Transactions", "Transactions"):
            await update.message.reply_text(
                "🧾 *Transactions*", parse_mode="Markdown", reply_markup=self.tx_menu()
            )
        elif text in ("currency", "Currency", "💱 Currency", "/rates", "/convert"):
            await update.message.reply_text("💱 *Currency*", parse_mode="Markdown", reply_markup=self.currency_menu())
        elif text in ("⚙️ Settings", "Settings"):
            await update.message.reply_text("⚙️ *Settings*", parse_mode="Markdown", reply_markup=self.settings_menu())
        else:
            await update.message.reply_text(
                "🤔 Type *❌ Cancel* to reset, or use the buttons 👇",
                parse_mode="Markdown",
                reply_markup=MAIN_MENU if not state else CANCEL_KB,
            )

    # ---------- universal home ----------

    async def go_home(self, query):
        chat_id = query.message.chat_id
        self.pop_state(chat_id)
        try:
            await query.edit_message_text("🏠 Main menu")
        except Exception:
            pass
        await query.message.reply_text("🏠 *Main menu*", parse_mode="Markdown", reply_markup=MAIN_MENU)

    # ---------- annotate flow (spend/income → category → currency → amount) ----------

    async def start_annotate(self, message, chat_id):
        cats = await asyncio.to_thread(self.budget.categories, chat_id)
        if not cats:
            await message.reply_text(
                "🚫 No categories yet.\n\n➕ Create one in 💰 Budget → ➕ Add category",
                reply_markup=MAIN_MENU,
            )
            return
        self.pop_state(chat_id)
        await message.reply_text(
            "✏️ *Annotate money*\n\nWhat is it?",
            parse_mode="Markdown",
            reply_markup=self.annotate_kind_menu(),
        )

    def annotate_category_menu(self, kind, chat_id, back="ann:start"):
        cats = self.storage.get_categories(chat_id)
        rows = [
            [InlineKeyboardButton(f"🏷 {c['name']}", callback_data=f"anncat:{kind}:{c['id']}")]
            for c in cats
        ]
        rows.append([back_btn(back), home_btn()])
        return InlineKeyboardMarkup(rows)

    def annotate_currency_menu(self, kind, cat_id):
        rows = [
            [InlineKeyboardButton(f"{flag(c)} {c}", callback_data=f"anncur:{kind}:{cat_id}:{c}")]
            for c in ANNOTATE_CURRENCIES
        ]
        rows.append([back_btn(f"ann:{kind}"), home_btn()])
        return InlineKeyboardMarkup(rows)

    def annotate_amount_menu(self, kind, cat_id, currency):
        rows = [
            [InlineKeyboardButton(f"{q:,} {currency}", callback_data=f"annamt:{kind}:{cat_id}:{currency}:{q}")]
            for q in QUICK_AMOUNTS
        ]
        rows.append([InlineKeyboardButton("✏️ Other amount", callback_data=f"annother:{kind}:{cat_id}:{currency}")])
        rows.append([back_btn(f"anncat:{kind}:{cat_id}"), home_btn()])
        return InlineKeyboardMarkup(rows)

    async def handle_annotate_cb(self, query, chat_id, data):
        if data[0] == "ann":
            step = data[1] if len(data) > 1 else "start"
            rest = data[2:]
        else:
            step = data[0]
            rest = data[1:]

        if step == "start":
            await query.edit_message_text(
                "✏️ *Annotate money*\n\nWhat is it?",
                parse_mode="Markdown",
                reply_markup=self.annotate_kind_menu(),
            )
            return

        if step in ("in", "out"):
            kind = step
            kind_label = "📈 Income" if kind == "in" else "📉 Spend"
            cats = await asyncio.to_thread(self.budget.categories, chat_id)
            if not cats:
                await query.edit_message_text("🚫 No categories yet", reply_markup=self.annotate_kind_menu())
                return
            await query.edit_message_text(
                f"✏️ {kind_label}\n\n🏷 Where?",
                parse_mode="Markdown",
                reply_markup=self.annotate_category_menu(kind, chat_id),
            )
            return

        kind = rest[0]
        kind_label = "📈 Income" if kind == "in" else "📉 Spend"

        if step == "anncat":
            cat_id = int(rest[1])
            cat = await asyncio.to_thread(self.storage.get_category, chat_id, cat_id)
            await query.edit_message_text(
                f"✏️ {kind_label} → _{cat['name']}_\n\n💱 Currency?",
                parse_mode="Markdown",
                reply_markup=self.annotate_currency_menu(kind, cat_id),
            )

        elif step == "anncur":
            cat_id = int(rest[1])
            currency = rest[2]
            await query.edit_message_text(
                f"✏️ {kind_label}\n\n💰 Amount in {flag(currency)} {currency}?",
                parse_mode="Markdown",
                reply_markup=self.annotate_amount_menu(kind, cat_id, currency),
            )

        elif step == "annamt":
            cat_id = int(rest[1])
            currency = rest[2]
            amount = float(rest[3])
            await self.finish_annotate(query, chat_id, kind, cat_id, currency, amount)

        elif step == "annother":
            cat_id = int(rest[1])
            currency = rest[2]
            self.set_state(chat_id, "annotate_amount", kind=kind, category_id=cat_id, currency=currency)
            await query.edit_message_text(
                f"✏️ {kind_label}\n\n🔢 Send the amount in {flag(currency)} {currency}:",
                parse_mode="Markdown",
            )
            await query.message.reply_text("✏️ Type the amount:", reply_markup=CANCEL_KB)

    async def finish_annotate(self, query, chat_id, kind, cat_id, currency, amount):
        self.pop_state(chat_id)
        uah_amount = amount
        if currency != "UAH":
            rates = await asyncio.to_thread(self.api.get_rates)
            if not rates:
                await query.edit_message_text(f"⚠️ Rates unavailable: {self.api.last_error}")
                return
            try:
                uah_amount = self.api.convert(rates, currency, "UAH", amount)
            except ValueError as ex:
                await query.edit_message_text(f"⚠️ {ex}")
                return
        sign = 1 if kind == "in" else -1
        delta = sign * uah_amount
        cat = await asyncio.to_thread(self.storage.get_category, chat_id, cat_id)
        await asyncio.to_thread(self.budget.annotate_external, chat_id, cat_id, delta)
        icon = "📈" if kind == "in" else "📉"
        kind_label = "Income" if kind == "in" else "Spend"
        note = "" if currency == "UAH" else f" ({flag(currency)} {amount:g} {currency})"
        await query.edit_message_text(
            f"{icon} *{kind_label}* {flag(currency)} *{amount:g} {currency}*{note}\n"
            f"🏷 _{cat['name']}_\n"
            f"✅ Saved: *{fmt_uah(int(round(delta * 100)))}*",
            parse_mode="Markdown",
        )
        await query.message.reply_text("🏠 Done! More actions 👇", reply_markup=MAIN_MENU)

    # ---------- state machine for text input ----------

    async def handle_state_input(self, update, context, state):
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        kind = state["state"]

        if kind == "addcat":
            self.pop_state(chat_id)
            cat_id = await asyncio.to_thread(self.budget.add_category, chat_id, text)
            if cat_id is None:
                await update.message.reply_text("⚠️ Category already exists", reply_markup=self.budget_menu())
            else:
                await update.message.reply_text(
                    f"✅ Category *{text}* created!", parse_mode="Markdown", reply_markup=self.budget_menu()
                )
            return True

        if kind == "annotate_amount":
            try:
                amount = float(text.replace(",", "."))
            except ValueError:
                await update.message.reply_text("⚠️ Send a number, e.g. `250`", parse_mode="Markdown")
                return True
            await self.finish_annotate_amount_state(update, state, amount)
            return True

        if kind == "convert_amount":
            self.pop_state(chat_id)
            try:
                amount = float(text.replace(",", "."))
            except ValueError:
                await update.message.reply_text("⚠️ Send a number, e.g. `100.50`", parse_mode="Markdown")
                return True
            rates = await asyncio.to_thread(self.api.get_rates)
            if not rates:
                await update.message.reply_text(f"⚠️ Rates unavailable: {self.api.last_error}", reply_markup=MAIN_MENU)
                return True
            try:
                result = self.api.convert(rates, state["from_cur"], state["to_cur"], amount)
            except ValueError as ex:
                await update.message.reply_text(f"⚠️ {ex}", reply_markup=MAIN_MENU)
                return True
            await update.message.reply_text(
                f"🔄 {flag(state['from_cur'])} *{amount:g} {state['from_cur']}* "
                f"= {flag(state['to_cur'])} *{result:.2f} {state['to_cur']}*",
                parse_mode="Markdown",
                reply_markup=MAIN_MENU,
            )
            return True

        if kind == "setwebhook":
            self.pop_state(chat_id)
            ok = await asyncio.to_thread(self.api.set_webhook, text)
            if ok:
                await update.message.reply_text(
                    f"🔔 Webhook set to:\n`{text}`", parse_mode="Markdown", reply_markup=self.settings_menu()
                )
            else:
                await update.message.reply_text(f"⚠️ Failed: {self.api.last_error}", reply_markup=self.settings_menu())
            return True

        return False

    async def finish_annotate_amount_state(self, update, state, amount):
        chat_id = update.effective_chat.id
        kind = state["kind"]
        cat_id = state["category_id"]
        currency = state["currency"]
        self.pop_state(chat_id)
        uah_amount = amount
        if currency != "UAH":
            rates = await asyncio.to_thread(self.api.get_rates)
            if not rates:
                await update.message.reply_text(f"⚠️ Rates unavailable: {self.api.last_error}", reply_markup=MAIN_MENU)
                return
            try:
                uah_amount = self.api.convert(rates, currency, "UAH", amount)
            except ValueError as ex:
                await update.message.reply_text(f"⚠️ {ex}", reply_markup=MAIN_MENU)
                return
        sign = 1 if kind == "in" else -1
        delta = sign * uah_amount
        cat = await asyncio.to_thread(self.storage.get_category, chat_id, cat_id)
        await asyncio.to_thread(self.budget.annotate_external, chat_id, cat_id, delta)
        icon = "📈" if kind == "in" else "📉"
        kind_label = "Income" if kind == "in" else "Spend"
        note = "" if currency == "UAH" else f" ({flag(currency)} {amount:g} {currency})"
        await update.message.reply_text(
            f"{icon} *{kind_label}* {flag(currency)} *{amount:g} {currency}*{note}\n"
            f"🏷 _{cat['name']}_\n"
            f"✅ Saved: *{fmt_uah(int(round(delta * 100)))}*",
            parse_mode="Markdown",
            reply_markup=MAIN_MENU,
        )

    # ---------- callback router ----------

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data.split(":")
        section = data[0]
        chat_id = query.message.chat_id

        if not await self.is_authorized(chat_id):
            await query.answer("🔒 Please send the password first", show_alert=True)
            return

        if section == "home":
            await self.go_home(query)
        elif section in ("ann", "anncat", "anncur", "annamt", "annother"):
            await self.handle_annotate_cb(query, chat_id, data)
        elif section == "sec":
            self.pop_state(chat_id)
            await self.show_section(query, data[1])
        elif section == "bud":
            await self.handle_budget_cb(query, chat_id, data[1:])
        elif section == "tx":
            await self.handle_tx_cb(query, chat_id, data[1:])
        elif section == "cur":
            await self.handle_currency_cb(query, chat_id, data[1:])
        elif section == "set":
            await self.handle_settings_cb(query, chat_id, data[1:])
        elif section == "cat":
            await self.handle_tx_pick(query, chat_id, data)
        elif section == "skip":
            await self.handle_tx_skip(query, chat_id, data[1])
        elif section == "delcat":
            await self.handle_delcat_pick(query, chat_id, int(data[1]))
        elif section == "convfrom":
            buttons = [
                [InlineKeyboardButton(f"{flag(c)} {c}", callback_data=f"convto:{data[1]}:{c}")]
                for c in ANNOTATE_CURRENCIES
                if c != data[1]
            ]
            buttons.append([back_btn("cur:conv"), home_btn()])
            await query.edit_message_text(
                f"🔄 Convert *{flag(data[1])} {data[1]}* → to?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        elif section == "convto":
            from_cur, to_cur = data[1], data[2]
            self.set_state(chat_id, "convert_amount", from_cur=from_cur, to_cur=to_cur)
            await query.edit_message_text(
                f"🔄 *{flag(from_cur)} {from_cur}* → *{flag(to_cur)} {to_cur}*\n\n💰 Amount?",
                parse_mode="Markdown",
            )
            await query.message.reply_text("✏️ Type the amount:", reply_markup=CANCEL_KB)
        else:
            await query.edit_message_text("🤔 Unknown action. 🏠 Main menu",
                                          reply_markup=InlineKeyboardMarkup([[home_btn()]]))

    async def show_section(self, query, section):
        if section == "budget":
            await query.edit_message_text("💰 *Budget*", parse_mode="Markdown", reply_markup=self.budget_menu())
        elif section == "tx":
            await query.edit_message_text("🧾 *Transactions*", parse_mode="Markdown", reply_markup=self.tx_menu())
        elif section == "currency":
            await query.edit_message_text("💱 *Currency*", parse_mode="Markdown", reply_markup=self.currency_menu())
        elif section == "settings":
            await query.edit_message_text("⚙️ *Settings*", parse_mode="Markdown", reply_markup=self.settings_menu())

    # ---------- budget section ----------

    async def handle_budget_cb(self, query, chat_id, args):
        action = args[0]

        if action == "balance":
            balances = await asyncio.to_thread(self.budget.balances, chat_id)
            if not balances:
                await query.edit_message_text("🚫 No categories yet", reply_markup=self.budget_menu())
                return
            lines = []
            for row in balances:
                emoji = "🟢" if row["balance"] >= 0 else "🔴"
                lines.append(f"{emoji} *{row['name']}*: {fmt_uah(row['balance'])}")
            total = sum(row["balance"] for row in balances)
            lines.append(f"\n{'🟢' if total >= 0 else '🔴'} *Total: {fmt_uah(total)}*")
            await query.edit_message_text("💰 *Budget balances*\n\n" + "\n".join(lines),
                                          parse_mode="Markdown", reply_markup=self.budget_menu())

        elif action == "cats":
            cats = await asyncio.to_thread(self.budget.categories, chat_id)
            if not cats:
                await query.edit_message_text("🚫 No categories yet", reply_markup=self.budget_menu())
                return
            lines = [f"🏷 *{c['name']}*" for c in cats]
            await query.edit_message_text("🏷 *Your categories*\n\n" + "\n".join(lines),
                                          parse_mode="Markdown", reply_markup=self.budget_menu())

        elif action == "addcat":
            self.set_state(chat_id, "addcat")
            await query.edit_message_text("➕ *New category*", parse_mode="Markdown")
            await query.message.reply_text("✏️ Send the category name:", reply_markup=CANCEL_KB)

        elif action == "delcat":
            cats = await asyncio.to_thread(self.budget.categories, chat_id)
            if not cats:
                await query.edit_message_text("🚫 No categories to delete", reply_markup=self.budget_menu())
                return
            buttons = [
                [InlineKeyboardButton(f"🗑 {c['name']}", callback_data=f"delcat:{c['id']}")]
                for c in cats
            ]
            buttons.append([back_btn("sec:budget"), home_btn()])
            await query.edit_message_text(
                "🗑 *Delete category*\n\n⚠️ Tap to delete:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    async def handle_delcat_pick(self, query, chat_id, category_id):
        cat = await asyncio.to_thread(self.storage.get_category, chat_id, category_id)
        if cat is None:
            await query.edit_message_text("⚠️ Category not found", reply_markup=self.budget_menu())
            return
        await asyncio.to_thread(self.budget.delete_category, chat_id, category_id)
        await query.edit_message_text(
            f"🗑 Category *{cat['name']}* deleted", parse_mode="Markdown", reply_markup=self.budget_menu()
        )

    # ---------- transactions section ----------

    def _category_keyboard(self, tx_key, chat_id):
        short = self._short_tx_id(tx_key)
        cats = self.storage.get_categories(chat_id)
        buttons = [
            [InlineKeyboardButton(f"🏷 {c['name']}", callback_data=f"cat:{short}:{c['id']}")]
            for c in cats
        ]
        buttons.append([InlineKeyboardButton("⏭ Skip", callback_data=f"skip:{short}"), home_btn()])
        return InlineKeyboardMarkup(buttons)

    async def prompt_category(self, chat_id, tx):
        tx_key = tx["tx_key"]
        kind = "📈 Income" if tx["amount"] >= 0 else "📉 Spend"
        text = (
            f"{kind}: *{fmt_amount(tx['amount'], tx['currency'])}*\n"
            f"_{tx['description']}_\n"
            f"🕐 {time.strftime('%d.%m %Y %H:%M', time.localtime(tx['time']))}\n\n"
            f"🏷 Choose a category:"
        )
        keyboard = self._category_keyboard(tx_key, chat_id)
        try:
            await self.app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=keyboard
            )
        except Exception as ex:
            log.error("Failed to send prompt to %s: %s", chat_id, ex)

    async def handle_tx_cb(self, query, chat_id, args):
        action = args[0]

        if action == "pending":
            txs = await asyncio.to_thread(self.budget.unannotated, chat_id)
            if not txs:
                await query.edit_message_text("✅ All transactions annotated!", reply_markup=self.tx_menu())
                return
            await query.edit_message_text(
                f"📥 *{len(txs)}* unannotated. Sending prompts 👇",
                parse_mode="Markdown",
                reply_markup=self.tx_menu(),
            )
            for tx in txs:
                await self.prompt_category(chat_id, tx)

        elif action == "history":
            txs = await asyncio.to_thread(self.budget.recent_transactions, chat_id, 10)
            if not txs:
                await query.edit_message_text("🚫 No transactions yet", reply_markup=self.tx_menu())
                return
            lines = []
            for tx in txs:
                icon = "📈" if tx["amount"] >= 0 else "📉"
                cat = ""
                if tx["category_id"]:
                    c = self.storage.get_category(chat_id, tx["category_id"])
                    cat = f" 🏷_{c['name']}_" if c else ""
                lines.append(
                    f"{icon} {time.strftime('%d.%m %H:%M', time.localtime(tx['tx_time']))} "
                    f"*{fmt_amount(tx['amount'], tx['currency'])}* _{tx['description']}_"
                    f"{cat}"
                )
            await query.edit_message_text(
                "📜 *Last transactions*\n\n" + "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=self.tx_menu(),
            )

    async def handle_tx_pick(self, query, chat_id, data):
        short = data[1]
        tx_key = self._short_ids.get(short)
        if tx_key is None:
            await query.edit_message_text(
                "⌛ Prompt expired",
                reply_markup=with_nav([[InlineKeyboardButton("📥 Unannotated", callback_data="tx:pending")]]),
            )
            return
        category_id = int(data[-1])
        rates = await asyncio.to_thread(self.api.get_rates)
        category, error = await asyncio.to_thread(
            self.budget.annotate_transaction, tx_key, chat_id, category_id, rates
        )
        if error:
            await query.edit_message_text(query.message.text + f"\n\n⚠️ {error}")
            return
        await query.edit_message_text(query.message.text + f"\n\n✅ → *{category['name']}*", parse_mode="Markdown")

    async def handle_tx_skip(self, query, chat_id, short):
        tx_key = self._short_ids.get(short)
        if tx_key is None:
            await query.edit_message_text("⌛ Prompt expired")
            return
        await query.edit_message_text(query.message.text + "\n\n⏭ Skipped")

    # ---------- currency section ----------

    async def handle_currency_cb(self, query, chat_id, args):
        action = args[0]

        if action == "rates":
            await query.edit_message_text("⏳ Loading rates…")
            rates = await asyncio.to_thread(self.api.get_rates)
            if not rates:
                await query.edit_message_text(f"⚠️ Rates unavailable: {self.api.last_error}",
                                               reply_markup=self.currency_menu())
                return
            lines = []
            for rate in rates:
                a = CURRENCIES.get(rate["currencyCodeA"], "?")
                b = CURRENCIES.get(rate["currencyCodeB"], "?")
                if rate.get("rateBuy") is not None:
                    lines.append(
                        f"{flag(a)} {a}/{b} {flag(b)}\n"
                        f"  🛒 buy *{rate['rateBuy']}*  |  🏪 sell *{rate['rateSell']}*"
                    )
                elif rate.get("rateCross") is not None:
                    lines.append(f"{flag(a)} {a}/{b} {flag(b)}\n  💠 {rate['rateCross']}")
            await query.edit_message_text(
                "📈 *Monobank rates*\n\n" + "\n\n".join(lines),
                parse_mode="Markdown",
                reply_markup=self.currency_menu(),
            )

        elif action == "conv":
            self.pop_state(chat_id)
            buttons = [
                [InlineKeyboardButton(f"{flag(c)} {c}", callback_data=f"convfrom:{c}")]
                for c in ANNOTATE_CURRENCIES
            ]
            buttons.append([back_btn("sec:currency"), home_btn()])
            await query.edit_message_text(
                "🔄 *Converter*\n\n💱 From?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    # ---------- settings section ----------

    async def handle_settings_cb(self, query, chat_id, args):
        action = args[0]

        if action == "accounts":
            await query.edit_message_text("⏳ Loading accounts…")
            accounts = await asyncio.to_thread(self.api.get_accounts)
            if not accounts:
                await query.edit_message_text(f"⚠️ Cannot get accounts: {self.api.last_error}",
                                               reply_markup=self.settings_menu())
                return
            lines = []
            for acc in accounts:
                currency = CURRENCIES.get(acc.get("currencyCode"), "?")
                balance = acc.get("balance", 0) / 100
                card = ", ".join(acc.get("maskedPan", [])) or acc.get("id", "?")
                acc_type = acc.get("type", "")
                type_icon = {"black": "🖤", "white": "🤍", "platinum": "🩶",
                             "iron": "⚙️", "fop": "👤"}.get(acc_type, "💳")
                lines.append(
                    f"{type_icon} *{card}*\n   💰 {balance:,.2f} {flag(currency)} {currency}\n   📋 _{acc_type}_"
                )
            await query.edit_message_text(
                "🏦 *Your accounts*\n\n" + "\n\n".join(lines),
                parse_mode="Markdown",
                reply_markup=self.settings_menu(),
            )

        elif action == "wh_on":
            self.set_state(chat_id, "setwebhook")
            await query.edit_message_text("🔔 *Set Monobank webhook*", parse_mode="Markdown")
            await query.message.reply_text(
                "✏️ Send the public HTTPS URL:", reply_markup=CANCEL_KB
            )

        elif action == "wh_off":
            ok = await asyncio.to_thread(self.api.delete_webhook)
            if ok:
                await query.edit_message_text("🔕 Webhook removed", reply_markup=self.settings_menu())
            else:
                await query.edit_message_text(f"⚠️ Failed: {self.api.last_error}", reply_markup=self.settings_menu())

        elif action == "help":
            await query.edit_message_text(
                "🤖 *MeinGeld Bot*\n\n"
                "✏️ *Annotate* — spend/income → category → currency → amount\n"
                "💰 *Budget* — balances, add/delete categories\n"
                "🧾 *Transactions* — annotate bank txs, history\n"
                "💱 *Currency* — rates & converter\n"
                "⚙️ *Settings* — accounts, webhook\n\n"
                "Fast: `/ext +500 Aliments` · `/convert 100 USD UAH`",
                parse_mode="Markdown",
                reply_markup=self.settings_menu(),
            )

    # ---------- slash commands (shortcuts) ----------

    async def cmd_rates(self, update, context):
        await update.message.reply_text("📈 Loading rates…")
        rates = await asyncio.to_thread(self.api.get_rates)
        if not rates:
            await update.message.reply_text(f"⚠️ Rates unavailable: {self.api.last_error}", reply_markup=MAIN_MENU)
            return
        lines = []
        for rate in rates:
            a = CURRENCIES.get(rate["currencyCodeA"], "?")
            b = CURRENCIES.get(rate["currencyCodeB"], "?")
            if rate.get("rateBuy") is not None:
                lines.append(f"{flag(a)} {a}/{b}: 🛒 {rate['rateBuy']} / 🏪 {rate['rateSell']}")
            elif rate.get("rateCross") is not None:
                lines.append(f"{flag(a)} {a}/{b}: 💠 {rate['rateCross']}")
        await update.message.reply_text("📈 *Monobank rates*\n\n" + "\n".join(lines),
                                        parse_mode="Markdown", reply_markup=MAIN_MENU)

    async def cmd_convert(self, update, context):
        if len(context.args) != 3:
            await update.message.reply_text("Usage: /convert 100 USD UAH", reply_markup=MAIN_MENU)
            return
        try:
            amount = float(context.args[0])
            in_cur = context.args[1].upper()
            out_cur = context.args[2].upper()
        except ValueError:
            await update.message.reply_text("⚠️ Wrong amount", reply_markup=MAIN_MENU)
            return
        rates = await asyncio.to_thread(self.api.get_rates)
        if not rates:
            await update.message.reply_text(f"⚠️ Rates unavailable: {self.api.last_error}", reply_markup=MAIN_MENU)
            return
        try:
            result = self.api.convert(rates, in_cur, out_cur, amount)
        except ValueError as ex:
            await update.message.reply_text(f"⚠️ {ex}", reply_markup=MAIN_MENU)
            return
        await update.message.reply_text(
            f"🔄 {flag(in_cur)} *{amount:g} {in_cur}* = {flag(out_cur)} *{result:.2f} {out_cur}*",
            parse_mode="Markdown", reply_markup=MAIN_MENU,
        )

    async def cmd_balance(self, update, context):
        chat_id = update.effective_chat.id
        balances = await asyncio.to_thread(self.budget.balances, chat_id)
        if not balances:
            await update.message.reply_text("🚫 No categories yet", reply_markup=MAIN_MENU)
            return
        lines = []
        for row in balances:
            emoji = "🟢" if row["balance"] >= 0 else "🔴"
            lines.append(f"{emoji} *{row['name']}*: {fmt_uah(row['balance'])}")
        total = sum(row["balance"] for row in balances)
        lines.append(f"\n{'🟢' if total >= 0 else '🔴'} *Total: {fmt_uah(total)}*")
        await update.message.reply_text("💰 *Budget*\n\n" + "\n".join(lines),
                                        parse_mode="Markdown", reply_markup=MAIN_MENU)

    async def cmd_ext(self, update, context):
        chat_id = update.effective_chat.id
        if len(context.args) >= 2:
            try:
                delta = float(context.args[0].replace(",", "."))
            except ValueError:
                await update.message.reply_text("⚠️ Usage: /ext +500 Aliments", reply_markup=MAIN_MENU)
                return
            name = " ".join(context.args[1:])
            cat = await asyncio.to_thread(self.storage.get_category_by_name, chat_id, name)
            if cat is None:
                cats = await asyncio.to_thread(self.budget.categories, chat_id)
                names = ", ".join(c["name"] for c in cats)
                await update.message.reply_text(f"⚠️ Category not found. Existing: {names}", reply_markup=MAIN_MENU)
                return
            await asyncio.to_thread(self.budget.annotate_external, chat_id, cat["id"], delta)
            emoji = "📈" if delta >= 0 else "📉"
            await update.message.reply_text(
                f"{emoji} *{fmt_uah(int(round(delta * 100)))}* → _{cat['name']}_",
                parse_mode="Markdown", reply_markup=MAIN_MENU,
            )
            return
        await update.message.reply_text(
            "✏️ Use the *✏️ Annotate* button — 4 taps and done!\n\n"
            "Fast: `/ext +500 Aliments` · `/ext -200 Pocket Money`",
            parse_mode="Markdown", reply_markup=MAIN_MENU,
        )

    async def cmd_pending(self, update, context):
        chat_id = update.effective_chat.id
        txs = await asyncio.to_thread(self.budget.unannotated, chat_id)
        if not txs:
            await update.message.reply_text("✅ All transactions annotated!", reply_markup=MAIN_MENU)
            return
        for tx in txs:
            await self.prompt_category(chat_id, tx)

    async def cmd_accounts(self, update, context):
        accounts = await asyncio.to_thread(self.api.get_accounts)
        if not accounts:
            await update.message.reply_text(f"⚠️ Cannot get accounts: {self.api.last_error}", reply_markup=MAIN_MENU)
            return
        lines = []
        for acc in accounts:
            currency = CURRENCIES.get(acc.get("currencyCode"), "?")
            balance = acc.get("balance", 0) / 100
            card = ", ".join(acc.get("maskedPan", [])) or acc.get("id", "?")
            lines.append(f"💳 *{card}*: {balance:,.2f} {flag(currency)} {currency}")
        await update.message.reply_text("🏦 *Accounts*\n\n" + "\n".join(lines),
                                        parse_mode="Markdown", reply_markup=MAIN_MENU)

    async def cmd_setwebhook(self, update, context):
        if not context.args:
            self.set_state(update.effective_chat.id, "setwebhook")
            await update.message.reply_text("✏️ Send the public webhook URL:", reply_markup=CANCEL_KB)
            return
        ok = await asyncio.to_thread(self.api.set_webhook, context.args[0])
        if ok:
            await update.message.reply_text(f"🔔 Webhook set to {context.args[0]}", reply_markup=MAIN_MENU)
        else:
            await update.message.reply_text(f"⚠️ Failed: {self.api.last_error}", reply_markup=MAIN_MENU)

    async def cmd_delwebhook(self, update, context):
        ok = await asyncio.to_thread(self.api.delete_webhook)
        await update.message.reply_text(
            "🔕 Webhook removed" if ok else f"⚠️ Failed: {self.api.last_error}", reply_markup=MAIN_MENU
        )

    # ---------- transaction ingestion ----------

    async def ingest_transactions(self, chat_id, account_id, statements):
        rates = await asyncio.to_thread(self.api.get_rates)
        new_txs = await asyncio.to_thread(
            self.budget.process_statement_transactions, chat_id, account_id, statements, rates
        )
        for tx in new_txs:
            await self.prompt_category(chat_id, tx)

    async def poll_once(self):
        for chat_id in await asyncio.to_thread(self.storage.get_users):
            accounts = await asyncio.to_thread(self.api.get_accounts)
            if not accounts:
                log.warning("No accounts for chat %s: %s", chat_id, self.api.last_error)
                continue
            now = int(time.time())
            for acc in accounts:
                acc_id = acc["id"]
                last_ts = await asyncio.to_thread(self.storage.get_account_last_ts, acc_id, chat_id)
                if last_ts is None:
                    last_ts = now - 24 * 3600
                statements = await asyncio.to_thread(self.api.get_statement, acc_id, last_ts, now)
                if statements is None:
                    log.warning("Statement error for %s: %s", acc_id, self.api.last_error)
                    continue
                max_ts = max((item.get("time", 0) for item in statements), default=last_ts)
                await asyncio.to_thread(self.storage.set_account_last_ts, acc_id, chat_id, max(last_ts, max_ts))
                if statements:
                    await self.ingest_transactions(chat_id, acc_id, statements)
            await asyncio.sleep(1)

    async def poll_job(self, context: ContextTypes.DEFAULT_TYPE):
        try:
            await self.poll_once()
        except Exception:
            log.exception("polling failed")

    # ---------- incoming monobank webhook ----------

    async def handle_webhook_payload(self, payload):
        if isinstance(payload, dict):
            payload = [payload]
        chat_ids = await asyncio.to_thread(self.storage.get_users)
        for item in payload:
            account_id = item.get("accountId", "")
            for chat_id in chat_ids:
                await self.ingest_transactions(chat_id, account_id, [item])

    def start_webhook_server(self):
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                if bot._loop and bot._loop.is_running():
                    asyncio.run_coroutine_threadsafe(bot.handle_webhook_payload(payload), bot._loop)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format, *args):
                log.info("webhook: " + format, *args)

        server = ThreadingHTTPServer((WEBHOOK_HOST, WEBHOOK_PORT), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        log.info("Webhook server on %s:%s", WEBHOOK_HOST, WEBHOOK_PORT)
        return server

    # ---------- run ----------

    def run(self):
        if not TELEGRAM_BOT_TOKEN:
            raise SystemExit("TELEGRAM_BOT_TOKEN is not set (use .env or environment)")

        async def on_startup(app: Application):
            self._loop = asyncio.get_running_loop()
            await self.set_bot_commands(app)

        app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(on_startup).build()
        self.app = app

        for cmd, handler in [
            ("start", self.cmd_start),
            ("help", self.cmd_help),
            ("rates", self.cmd_rates),
            ("convert", self.cmd_convert),
            ("balance", self.cmd_balance),
            ("annotate", self.cmd_annotate_shortcut),
            ("ext", self.cmd_ext),
            ("pending", self.cmd_pending),
            ("accounts", self.cmd_accounts),
            ("setwebhook", self.cmd_setwebhook),
            ("delwebhook", self.cmd_delwebhook),
            ("cancel", self.cmd_cancel),
        ]:
            if cmd in ("start", "help", "cancel"):
                app.add_handler(CommandHandler(cmd, handler))
            else:
                app.add_handler(CommandHandler(cmd, self.require_auth(handler)))

        app.add_handler(CallbackQueryHandler(self.on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_menu_text))

        if WEBHOOK_PUBLIC_URL:
            ok = self.api.set_webhook(f"{WEBHOOK_PUBLIC_URL.rstrip('/')}/")
            log.info("Monobank webhook registration: %s (%s)", ok, self.api.last_error)
        self.start_webhook_server()
        app.job_queue.run_repeating(self.poll_job, interval=POLL_INTERVAL_SECONDS, first=5)
        log.info("Starting bot polling...")
        app.run_polling()

    async def set_bot_commands(self, app: Application):
        from telegram import BotCommand
        commands = [
            BotCommand("start", "🏠 Start / main menu"),
            BotCommand("annotate", "✏️ Add spend or income"),
            BotCommand("balance", "💰 Category balances"),
            BotCommand("pending", "📥 Unannotated transactions"),
            BotCommand("rates", "💱 Currency rates"),
            BotCommand("convert", "🔄 Convert currencies"),
            BotCommand("accounts", "🏦 Monobank accounts"),
            BotCommand("ext", "💸 Fast external money"),
            BotCommand("setwebhook", "🔔 Set Monobank webhook"),
            BotCommand("delwebhook", "🔕 Remove webhook"),
            BotCommand("cancel", "❌ Cancel current input"),
            BotCommand("help", "❔ Help"),
        ]
        try:
            await app.bot.set_my_commands(commands)
            log.info("Bot command menu registered")
        except Exception as ex:
            log.warning("Failed to set bot commands: %s", ex)

    async def cmd_annotate_shortcut(self, update, context):
        await self.start_annotate(update.message, update.effective_chat.id)

    async def cmd_cancel(self, update, context):
        self.pop_state(update.effective_chat.id)
        await update.message.reply_text("❌ Cancelled", reply_markup=MAIN_MENU)
