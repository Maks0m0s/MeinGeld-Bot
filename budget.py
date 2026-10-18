import time

from models import MonobankAPI
from storage import Storage

DEFAULT_CATEGORIES = ["Aliments", "Pocket Money"]


class BudgetService:
    def __init__(self, storage: Storage, api: MonobankAPI):
        self.storage = storage
        self.api = api

    def register_user(self, chat_id):
        self.storage.add_user(chat_id)
        existing = {c["name"] for c in self.storage.get_categories()}
        for name in DEFAULT_CATEGORIES:
            if name not in existing:
                self.storage.add_category(name)

    def add_category(self, name):
        return self.storage.add_category(name)

    def delete_category(self, category_id):
        self.storage.delete_category(category_id)

    def categories(self):
        return self.storage.get_categories()

    def balances(self):
        return self.storage.get_category_balances()

    def annotate_transaction(self, tx_key, category_id, rates):
        tx = self.storage.get_transaction(tx_key)
        if tx is None:
            return None, "Transaction not found"
        if tx["category_id"] is not None:
            return None, "Transaction is already annotated"

        try:
            uah_kop = self._to_uah_kop(rates, tx["amount"], tx["currency"])
        except Exception as ex:
            return None, f"Cannot convert {tx['currency']} to UAH: {ex}"
        self.storage.add_operation(
            category_id, uah_kop,
            note=tx["description"], ref_tx_key=tx_key,
        )
        self.storage.annotate_transaction(tx_key, category_id)
        category = self.storage.get_category(category_id)
        return category, None

    def annotate_external(self, category_id, delta_uah, note=""):
        self.storage.add_operation(
            category_id, int(round(delta_uah * 100)), note=note,
        )
        return self.storage.get_category(category_id)

    def unannotate_last_for_category(self, category_id):
        return self.storage.unannotate_last_for_category(category_id)

    def recent_transactions(self, limit=10):
        return self.storage.get_recent_transactions(limit)

    def unannotated(self):
        return self.storage.get_unannotated_transactions()

    def _to_uah_kop(self, rates, amount_kop, currency):
        if currency == "UAH":
            return amount_kop
        if not rates:
            raise ValueError("rates unavailable")
        uah = self.api.to_uah(rates, amount_kop / 100.0, currency)
        return int(round(uah * 100))

    def process_statement_transactions(self, account_id, statements, account_currency="UAH"):
        new_txs = []
        for item in statements:
            tx_id = item.get("id")
            if not tx_id:
                continue
            tx_key = f"{account_id}:{tx_id}"
            if not self.storage.is_transaction_new(tx_key):
                continue
            self.storage.save_transaction(
                tx_key=tx_key,
                account_id=account_id,
                amount=item.get("amount", 0),
                currency=account_currency,
                description=item.get("merchantDescription") or item.get("description") or "Transaction",
                tx_time=item.get("time", int(time.time())),
            )
            new_txs.append({
                "tx_key": tx_key,
                "amount": item.get("amount", 0),
                "currency": account_currency,
                "description": item.get("merchantDescription") or item.get("description") or "Transaction",
                "time": item.get("time", int(time.time())),
            })
        return new_txs
