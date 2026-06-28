"""FSM-состояния регистрации клиента Scenti."""
from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    waiting_policy = State()      # показ политики: Принимаю / Отказываюсь
    waiting_name = State()        # ввод имени
    waiting_business = State()    # ввод названия бизнеса
    waiting_phone = State()       # отправка контакта
    waiting_region = State()      # выбор региона (inline)
    waiting_district = State()    # выбор района (inline)
