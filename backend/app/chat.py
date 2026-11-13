import os

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from app.schemas import ChatTurn

DEFAULT_MODEL = "gemini-3.5-flash-lite"


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def model_name() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL)


def generate_reply(message: str, history: list[ChatTurn]) -> str:
    key = api_key()
    if key is None:
        return f"Local test reply. I received: “{message}”"

    transcript = "\n".join(f"{turn.role}: {turn.content}" for turn in history[-20:])
    prompt = f"{transcript}\nuser: {message}" if transcript else message
    agent = Agent(
        GoogleModel(model_name(), provider=GoogleProvider(api_key=key)),
        instructions="You are Guuey, a helpful conversational assistant. Be clear and concise.",
    )
    result = agent.run_sync(prompt, model_settings={"max_tokens": 2048})
    return str(result.output)
