from env import TOKEN, PERSONAL_URL, RATE_URL
from methods import get_rates, get_command_list, convert_currencies

mg_skills = [
    "Spends control",
    "Transactions control",
    "Setting goals",
    "Actual rate presentation",
    "Currency converter",
    "Counter of all finances (pending/cash/card) in 3 currencies"
]

mg_commands = ["Get current rates", "Convert currencies", "Get command list"]
methods_list = [get_rates, convert_currencies, lambda: get_command_list(mg_commands)]

headers = {
    "X-Token": TOKEN
}

active = True

def start():
    print(
        "Welcome to MeinGeld !!!\n MeinGeld - it's a mini fin-tech that helps to it's users to do the next activities easier, faster, and with more precision :")
    for skill in mg_skills:
        print(f"- {skill}")
    print("-" * 50)
    get_command_list(mg_commands)

def main():
    global active
    start()
    while active:
        try:
            try:
                action = input(">> ")
                if action == "stop":
                    active = False
                    break
                action = int(action)-1
                if action >= 0 and action <= len(methods_list):
                    method = methods_list[action]
                else:
                    raise Exception()
            except:
                print(f"### Use integers 1-{len(mg_commands)} to select the action ###")
                continue
            method()
        except Exception as ex:
            print(f"ERROR in main(): {ex}")
    print("Program finished.\nBye ;)")

main()