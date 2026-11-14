import os
from typing import Literal


DEFAULT_MODEL = "gemini-3.5-flash-lite"
STAGE_VARIABLES = {
    "planning": "GEMINI_PLANNING_MODEL",
    "research": "GEMINI_RESEARCH_MODEL",
    "verification": "GEMINI_VERIFICATION_MODEL",
}


def base_model_name() -> str:
    return os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


def stage_model_name(stage: Literal["planning", "research", "verification"]) -> str:
    return os.getenv(STAGE_VARIABLES[stage], "").strip() or base_model_name()
