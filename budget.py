import time

from models import MonobankAPI, CURRENCIES
from storage import Storage

DEFAULT_CATEGORIES = ["Aliments", "Pocket Money"]


class BudgetService:
    def __init__(self, storage: Storage, api: MonobankAPI):
        self.storage = storage
        self.api = api

    def register_user(self, chat_id):
        self.storage.add_user(chat_id)
        existing = {c["name"] for c in self.storage.get_categories(chat_id)}
        for name in DEFAULT_CATEGORIES:
            if name not in existing:
                self.storage.add_category(chat_id, name)

    def add_category(self, chat_id, name):
        return self.storage.add_category(chat_id, name)

    def delete_category(self, chat_id, category_id):
        self.storage.delete_category(chat_id, category_id)

    def categories(self, chat_id):
        return self.storage.get_categories(chat_id)

    def balances(self, chat_id):
        return self.storage.get_category_balances(chat_id)

    def annotate_transaction(self, tx_key, chat_id, category_id, rates):
        tx = self.storage.get_transaction(tx_key, chat_id)
        if tx is None:
            return None, "Transaction not found"
        if tx["category_id"] is not None:
            return None, "Transaction is already annotated"

        amount = tx["amount"]
        uah_kop = self._to_uah_kop(rates, amount, tx["currency"])
        self.storage.add_operation(
            chat_id, category_id, uah_kop,
            note=tx["description"], ref_tx_key=tx_key,
        )
        self.storage.annotate_transaction(tx_key, chat_id, category_id)
        category = self.storage.get_category(chat_id, category_id)
        return category, None

    def annotate_external(self, chat_id, category_id, delta_uah, note=""):
        self.storage.add_operation(
            chat_id, category_id, int(round(delta_uah * 100)), note=note,
        )
        return self.storage.get_category(chat_id, category_id)

    def unannotate_last_for_category(self, chat_id, category_id):
        return self.storage.unannotate_last_for_category(chat_id, category_id)

    def recent_transactions(self, chat_id, limit=10):
        return self.storage.get_recent_transactions(chat_id, limit)

    def unannotated(self, chat_id):
        return self.storage.get_unannotated_transactions(chat_id)

    def _to_uah_kop(self, rates, amount_kop, currency):
        if currency == "UAH":
            return amount_kop
        try:
            uah = self.api.to_uah(rates, amount_kop / 100.0, currency)
            return int(round(uah * 100))
        except Exception:
            return amount_kop

    def process_statement_transactions(self, chat_id, account_id, statements, rates):
        new_txs = []
        for item in statements:
            tx_id = item.get("id")
            if not tx_id:
                continue
            tx_key = f"{account_id}:{tx_id}"
            if not self.storage.is_transaction_new(tx_key, chat_id):
                continue
            currency = CURRENCIES.get(item.get("currencyCode"), "UAH")
            self.storage.save_transaction(
                tx_key=tx_key,
                chat_id=chat_id,
                account_id=account_id,
                amount=item.get("amount", 0),
                currency=currency,
                description=item.get("merchantDescription") or item.get("description") or "Transaction",
                tx_time=item.get("time", int(time.time())),
            )
            new_txs.append({
                "tx_key": tx_key,
                "amount": item.get("amount", 0),
                "currency": currency,
                "description": item.get("merchantDescription") or item.get("description") or "Transaction",
                "time": item.get("time", int(time.time())),
            })
        return new_txs
