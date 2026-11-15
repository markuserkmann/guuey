from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict
from uuid import UUID

from app.auth import require_api_key
from app.task_api import get_tasks, task_action
from app.telegram import TelegramStore


def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/telegram", dependencies=[Depends(no_cache)])
Owner = Depends(require_api_key)


def get_telegram():
    return TelegramStore(get_tasks().runs)


class ConfirmLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: UUID


@router.get("")
def status(owner: str = Owner):
    return get_telegram().status(owner)


@router.post("/link")
def start_link(owner: str = Owner):
    return task_action(get_telegram().start_link, owner)


@router.post("/confirm")
def confirm(body: ConfirmLink, owner: str = Owner):
    return task_action(get_telegram().confirm, owner, str(body.attempt_id))


@router.delete("", status_code=204)
def disconnect(owner: str = Owner):
    get_telegram().disconnect(owner)
