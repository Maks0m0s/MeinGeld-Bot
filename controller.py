from models import CurrencyModel, AccountModel
from view import ConsoleView

class AppController:
    SKILLS = [
        "Spends control",
        "Transactions control",
        "Setting goals",
        "Actual rate presentation",
        "Currency converter",
        "Counter of all finances (pending/cash/card) in 3 currencies",
    ]

    def __init__(self):
        #Models
        self.currency_model = CurrencyModel()
        self.account_model = AccountModel()
        #View
        self.view = ConsoleView()
        #Commands
        self.commands = ["Rates", "Account", "Get command list"]
        self.rates_commands = ["Get current rates", "Convert currencies", "Get command list"]
        self.account_commands = ["Get the user of account", "Get all cards", "Get command list"]
        #Handlers
        self._path_handlers = {
            1: self.run_rates_path,
            2: self.run_account_path,
            3: lambda: self._handle_command_list(self.commands),
        }
        self._rates_command_handlers = {
            1: self._handle_get_rates,
            2: self._handle_convert_currencies,
            3: lambda: self._handle_command_list(self.rates_commands),
        }
        self._account_command_handlers = {
            1: self._handle_get_account_user,
            2: self._handle_get_all_cards,
            3: lambda: self._handle_command_list(self.account_commands),
        }

    def run(self):
        self.view.print_welcome(self.SKILLS)
        self._handle_command_list(self.commands)
        while True:
            try:
                try:
                    action = self.view.get_command_input()
                    if action == "stop":
                        break
                    action = int(action)
                except ValueError:
                    self.view.print_invalid_command(len(self.commands))
                    continue

                run_path = self._path_handlers.get(action)
                if run_path is None:
                    self.view.print_invalid_command(len(self.commands))
                    continue

                run_path()
            except Exception as ex:
                if str(ex) != "stop":
                    self.view.print_system_error(str(ex))
                else:
                    break
        self.view.print_finished()

    #  --- Rates block commands ---
    def _handle_get_rates(self):
        rates = self.currency_model.get_rates(on_retry=self.view.print_loading)
        if not rates:
            self.view.print_error(self.currency_model.last_error)
            return
        self.view.print_rates(rates)

    def _handle_convert_currencies(self):
        try:
            in_currency = self.view.get_currency_input("Input currency")
            out_currency = self.view.get_currency_input("Output currency")
            in_amount = float(self.view.get_amount_input())

            if in_amount < 0:
                raise Exception()

            valid = set(self.currency_model.CURRENCIES.values())
            if in_currency not in valid or out_currency not in valid:
                raise Exception()

            rates = self.currency_model.get_rates()
            if not rates:
                if self.currency_model.last_error:
                    self.view.print_error(self.currency_model.last_error)
                raise ValueError("Rate API Error | Retry later")

            result = self.currency_model.convert(rates, in_currency, out_currency, in_amount)
            self.view.print_conversion_result(in_amount, in_currency, result, out_currency)
        except ValueError as error:
            self.view.print_error(str(error))
        except Exception:
            self.view.print_wrong_input()
            self._handle_convert_currencies()

    def _handle_command_list(self, commands):
        if commands is self.commands:
            self.view.print_blocks(commands)
        else:
            self.view.print_commands(commands, True)

    #  --- Account block commands ---
    def _handle_get_account_user(self):
        user = self.account_model.get_user(on_retry=self.view.print_loading)
        if not user:
            self.view.print_error(self.account_model.last_error)
            return
        print(user)

    def _handle_get_all_cards(self):
        cards = self.account_model.get_all_cards(on_retry=self.view.print_loading)
        if not cards:
            self.view.print_error(self.account_model.last_error)
            return
        self.view.print_cards(cards)

    #  --- Block paths ---
    def run_rates_path(self):
        self.view.print_block_to_manage("Rates")
        self._handle_command_list(self.rates_commands)
        while True:
            try:
                try:
                    action = self.view.get_command_input("rates")
                    if action == "stop":
                        break
                    elif action == "exit":
                        self.view.print_line()
                        self.view.print_blocks(self.commands)
                        return
                    action = int(action)
                except ValueError:
                    self.view.print_invalid_command(len(self.rates_commands))
                    continue

                handler = self._rates_command_handlers.get(action)
                if handler is None:
                    self.view.print_invalid_command(len(self.rates_commands))
                    continue

                handler()
            except Exception as ex:
                self.view.print_system_error(str(ex))

        raise Exception("stop")

    def run_account_path(self):
        self.view.print_block_to_manage("Account")
        self._handle_command_list(self.account_commands)
        while True:
            try:
                try:
                    action = self.view.get_command_input("account")
                    if action == "stop":
                        break
                    elif action == "exit":
                        self.view.print_line()
                        self.view.print_blocks(self.commands)
                        return
                    action = int(action)
                except ValueError:
                    self.view.print_invalid_command(len(self.account_commands))
                    continue

                handler = self._account_command_handlers.get(action)
                if handler is None:
                    self.view.print_invalid_command(len(self.account_commands))
                    continue

                handler()
            except Exception as ex:
                self.view.print_system_error(str(ex))

        raise Exception("stop")
