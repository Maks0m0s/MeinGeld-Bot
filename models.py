import time

import requests

from env import (
    MONOBANK_TOKEN,
    RATE_URL,
    PERSONAL_URL,
    STATEMENTS_URL,
    WEBHOOK_URL,
)

CURRENCIES = {840: "USD", 978: "EUR", 980: "UAH"}
CURRENCY_NAMES = {value: key for key, value in CURRENCIES.items()}


class MonobankAPI:
    def __init__(self, token=MONOBANK_TOKEN):
        self.token = token
        self.session = requests.Session()
        self.session.headers["X-Token"] = token
        self.last_error = ""
        self._retries = 0

    def _get(self, url, on_retry=None):
        self.last_error = ""
        try:
            response = self.session.get(url)
            if response.status_code == 429 and self._retries < 3:
                self._retries += 1
                if on_retry:
                    on_retry()
                time.sleep(1)
                return self._get(url, on_retry)
            response.raise_for_status()
            self._retries = 0
            return response.json()
        except Exception as ex:
            self.last_error = str(ex)
            return None

    def _post(self, url, payload):
        self.last_error = ""
        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as ex:
            self.last_error = str(ex)
            return False

    def _delete(self, url):
        self.last_error = ""
        try:
            response = self.session.delete(url)
            response.raise_for_status()
            return True
        except Exception as ex:
            self.last_error = str(ex)
            return False

    def get_rates(self, on_retry=None):
        rates = self._get(RATE_URL, on_retry=on_retry)
        if rates is None:
            return []
        return [
            rate
            for rate in rates
            if rate["currencyCodeA"] in CURRENCIES and rate["currencyCodeB"] in CURRENCIES
        ]

    def get_client_info(self, on_retry=None):
        return self._get(PERSONAL_URL, on_retry=on_retry)

    def get_accounts(self, on_retry=None):
        info = self.get_client_info(on_retry=on_retry)
        if not info:
            return []
        return info.get("accounts", [])

    def get_statement(self, account_id, from_ts, to_ts, on_retry=None):
        url = STATEMENTS_URL.format(account=account_id, from_ts=int(from_ts), to_ts=int(to_ts))
        return self._get(url, on_retry=on_retry) or []

    def set_webhook(self, webhook_url):
        return self._post(WEBHOOK_URL, {"webHookUrl": webhook_url})

    def delete_webhook(self):
        return self._delete(WEBHOOK_URL)

    def convert(self, rates, in_currency, out_currency, in_amount):
        if in_currency == out_currency:
            return in_amount

        in_code = CURRENCY_NAMES.get(in_currency)
        out_code = CURRENCY_NAMES.get(out_currency)
        if in_code is None or out_code is None:
            raise ValueError("Unknown currency")

        for rate in rates:
            if rate["currencyCodeA"] == in_code and rate["currencyCodeB"] == out_code:
                rate_value = rate.get("rateBuy") or rate.get("rateCross")
                if rate_value is None:
                    raise ValueError("Rate is unavailable")
                return in_amount * rate_value

        for rate in rates:
            if rate["currencyCodeA"] == out_code and rate["currencyCodeB"] == in_code:
                rate_value = rate.get("rateSell") or rate.get("rateCross")
                if rate_value is None:
                    raise ValueError("Rate is unavailable")
                return in_amount / rate_value

        raise ValueError(f"Rate not found: {in_currency} -> {out_currency}")

    def to_uah(self, rates, amount, currency):
        if currency == "UAH":
            return amount
        return self.convert(rates, currency, "UAH", amount)
