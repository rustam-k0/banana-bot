from aiogram.fsm.state import State, StatesGroup


class BotStates(StatesGroup):
    language = State()
    chat = State()
    complex_task = State()
    image_prompt = State()
    photo_to_edit = State()
    edit_prompt = State()
    file_analysis = State()
    translation = State()
    settings = State()
