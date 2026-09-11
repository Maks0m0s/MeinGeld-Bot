import time
import requests
from env import RATE_URL, PERSONAL_URL, TOKEN

api_headers = {"X-Token": TOKEN}

class BankModel:
    CURRENCIES = {840: "USD", 978: "EUR", 980: "UAH"}
    def __init__(self):
        self._get_rates_tries = 0
        self.last_error = ""

class CurrencyModel(BankModel):
    def get_rates(self, on_retry=None):
        self.last_error = ""
        try:
            response = requests.get(RATE_URL)
            response.raise_for_status()
            rates = response.json()

            result = []
            for rate in rates:
                code_a = rate["currencyCodeA"]
                code_b = rate["currencyCodeB"]
                if code_a in self.CURRENCIES and code_b in self.CURRENCIES:
                    result.append(rate)

            self._get_rates_tries = 0
            return result
        except Exception as ex:
            if str(ex).startswith("429 Client Error: Too Many Requests") and self._get_rates_tries < 3:
                if on_retry:
                    on_retry()
                time.sleep(1)
                self._get_rates_tries += 1
                return self.get_rates(on_retry)
            else:
                self.last_error = str(ex)
                return []

    def convert(self, rates, in_currency, out_currency, in_amount):
        if in_currency == out_currency:
            return in_amount

        in_code = None
        out_code = None
        for code, currency in self.CURRENCIES.items():
            if currency == in_currency:
                in_code = code
            if currency == out_currency:
                out_code = code

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

class AccountModel(BankModel):
    def get_user(self, on_retry=None):
        try:
            response = requests.get(PERSONAL_URL, headers=api_headers)
            if response.ok:
                data = response.json()
                self._get_rates_tries = 0
                return data.get("name")
        except Exception as ex:
            if str(ex).startswith("429 Client Error: Too Many Requests") and self._get_rates_tries < 3:
                if on_retry:
                    on_retry()
                time.sleep(1)
                self._get_rates_tries += 1
                return self.get_user(on_retry)
            else:
                self.last_error = str(ex)
                return

    def get_all_cards(self, on_retry=None):
        try:
            response = requests.get(PERSONAL_URL, headers=api_headers)
            if response.ok:
                data = response.json()
                cards = data.get("accounts", [])
                self._get_rates_tries = 0
                return cards
        except Exception as ex:
            if str(ex).startswith("429 Client Error: Too Many Requests") and self._get_rates_tries < 3:
                if on_retry:
                    on_retry()
                time.sleep(1)
                self._get_rates_tries += 1
                return self.get_user(on_retry)
            else:
                self.last_error = str(ex)
                return