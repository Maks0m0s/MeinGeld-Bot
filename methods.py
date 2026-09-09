from env import RATE_URL, PERSONAL_URL
import requests

needed_rates = {840:"🇺🇸USD", 978:"🇪🇺EUR", 980:"🇺🇦UAH"}
currencies = {840:"USD", 978:"EUR", 980:"UAH"}

def get_command_list(mg_commands):
    print("Commands list :")
    for cm, method in enumerate(mg_commands):
        print("{}. {}".format(cm + 1, method))

def get_rates():
    try:
        url = RATE_URL

        response = requests.get(url)
        response.raise_for_status()

        rates = response.json()

        needed_rates = {840:"🇺🇸USD", 978:"🇪🇺EUR", 980:"🇺🇦UAH"}
        result = []

        for rate in rates:
            if rate["currencyCodeA"] in needed_rates.keys() and rate["currencyCodeB"] in needed_rates.keys():
                print(
                    needed_rates[rate["currencyCodeA"]],
                    "->",
                    needed_rates[rate["currencyCodeB"]],
                    "buy:", rate.get("rateBuy"),
                    "sell:", rate.get("rateSell"),
                )
                result.append(rate)
        return result
    except Exception as ex:
        print(f"ERROR: {ex}")
        return []

def convert_currencies():
    try:
        in_currency = input("Input currency {USD, EUR, UAH} : ")
        out_currency = input("Output currency {USD, EUR, UAH} : ")
        in_amount = float(input("Input amount : "))
        if in_amount >= 0 and in_currency in currencies.values() and out_currency in currencies.values():
            rates = get_rates()
            if len(rates) == 0: raise ValueError("Rate API Error | Retry later")
            result = c_converter(rates, in_currency, out_currency, in_amount)
            print(f"{in_amount} {in_currency} ---> {result:.2f} {out_currency}")
        else:
            raise Exception()
    except ValueError as error:
        print(f"### {error} ###")
    except Exception:
        print("### Wrong input ###")
        convert_currencies()

def c_converter(rates, in_currency, out_currency, in_amount):
    # Same currency
    if in_currency == out_currency:
        return in_amount

    # Convert names to numeric Monobank codes
    in_code = None
    out_code = None

    for code, currency in currencies.items():
        if currency == in_currency:
            in_code = code
        if currency == out_currency:
            out_code = code

    if in_code is None or out_code is None:
        raise ValueError("Unknown currency")

    # Search for direct rate
    for rate in rates:
        code_a = rate["currencyCodeA"]
        code_b = rate["currencyCodeB"]

        if code_a == in_code and code_b == out_code:
            rate_value = rate.get("rateBuy") or rate.get("rateCross")

            if rate_value is None:
                raise ValueError("Rate is unavailable")

            print(rate_value)

            return in_amount * rate_value

    # Search for reverse rate
    for rate in rates:
        code_a = rate["currencyCodeA"]
        code_b = rate["currencyCodeB"]

        if code_a == out_code and code_b == in_code:
            rate_value = rate.get("rateSell") or rate.get("rateCross")

            if rate_value is None:
                raise ValueError("Rate is unavailable")

            print(rate_value)

            return in_amount / rate_value

    raise ValueError(
        f"Rate not found: {in_currency} -> {out_currency}"
    )