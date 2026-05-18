from aiogram.fsm.state import State, StatesGroup


class StudyStates(StatesGroup):
    """Shared FSM for both vocabulary and verb sessions."""
    showing_cards = State()   # walking through the N cards
    in_quiz       = State()   # answering mini-test questions
    idle          = State()
