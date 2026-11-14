from enum import StrEnum


class TurnIntent(StrEnum):
    CHAT = "chat"
    SEARCH = "search"


def classify_turn(*, search_requested: bool) -> TurnIntent:
    """Honor the user's explicit search choice for this first routing stage."""
    return TurnIntent.SEARCH if search_requested else TurnIntent.CHAT
