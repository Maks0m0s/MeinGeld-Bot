class ConsoleView:
    CURRENCY_SYMBOLS = {840: "\U0001f1fa\U0001f1f8USD", 978: "\U0001f1ea\U0001f1faEUR", 980: "\U0001f1fa\U0001f1e6UAH"}

    def print_welcome(self, skills):
        print(
            "Welcome to MeinGeld !!!\n"
            " MeinGeld - it's a mini fin-tech that helps to it's users "
            "to do the next activities easier, faster, and with more precision :"
        )
        for skill in skills:
            print(f"- {skill}")
        print("-" * 50)

    def print_block_to_manage(self, block):
        print("-"*15)
        print(f"{block} Management Block")
        print("-" * 15)

    def print_line(self):
        print("-"*15)

    def print_blocks(self, commands):
        print("Block to Manage :")
        for i, command in enumerate(commands):
            print(f"{i + 1}. {command}")
        print('(To finish program type "stop")')

    def print_commands(self, commands, exit_on=None):
        print("Commands list :")
        for i, command in enumerate(commands):
            print(f"{i + 1}. {command}")
        if exit_on:
            print('(To select other block type "exit")')
        print('(To finish program type "stop")')


    def get_command_input(self, prefix=None):
        if prefix:
            return input(f"{prefix} >> ")
        return input(">> ")

    def print_rates(self, rates):
        for rate in rates:
            code_a = rate["currencyCodeA"]
            code_b = rate["currencyCodeB"]
            print(
                self.CURRENCY_SYMBOLS[code_a],
                "->",
                self.CURRENCY_SYMBOLS[code_b],
                "buy:", rate.get("rateBuy"),
                "sell:", rate.get("rateSell"),
            )

    def print_loading(self):
        print("Loading...")

    def print_error(self, msg):
        print(f"### {msg} ###")

    def print_wrong_input(self):
        print("### Wrong input ###")

    def print_invalid_command(self, max_n):
        print(f"### Use integers 1-{max_n} to select the action ###")

    def print_system_error(self, msg):
        print(f"SYSTEM ERROR : {msg}")

    def get_currency_input(self, label):
        return input(f"{label} {{USD, EUR, UAH}} : ")

    def get_amount_input(self):
        return input("Input amount : ")

    def print_conversion_result(self, amount, in_currency, result, out_currency):
        print(f"{amount} {in_currency} ---> {result:.2f} {out_currency}")

    def print_finished(self):
        print("Program finished.\nBye ;)")

    def print_cards(self, cards):
        print("-" * 15)
        for card in cards:
            currency_code = card.get("currencyCode")
            if currency_code in self.CURRENCY_SYMBOLS:
                currency_code = self.CURRENCY_SYMBOLS[currency_code]
            print("Account ID:", card.get("id"))
            print("Balance:", int(card.get("balance"))/100)
            print("Currency:", currency_code)
            print("IBAN:", card.get("iban"))
            print("Card:", card.get("maskedPan"))
            print("-"*15)