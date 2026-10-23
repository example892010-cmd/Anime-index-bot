from aiogram.fsm.state import State, StatesGroup
class AddAnimeStates(StatesGroup):
    title = State()
class AdminStates(StatesGroup):
    user_id = State()
    welcome = State()
    setting = State()
    delete_id = State()
    edit_id = State()
    edit_field = State()
    link_anime_id = State()
    link_title = State()
    link_url = State()
    alias_anime_id = State()
    alias_value = State()
    poster_anime_id = State()
    restore_upload = State()
    shared_poster = State()
