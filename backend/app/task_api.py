from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_api_key
from app.runs import RunStore
from app.tasks import CVE_TEMPLATE, TaskEnabled, TaskInput, TaskStore

router = APIRouter(prefix="/api")
Owner = Depends(require_api_key)


@lru_cache(maxsize=1)
def get_tasks():
    return TaskStore(RunStore())


def task_action(action, *args):
    try:
        return action(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/tasks")
def list_tasks(owner: str = Owner):
    return get_tasks().list(owner)


@router.get("/task-templates")
def task_templates(owner: str = Owner):
    return [{"id": "critical-cves", **CVE_TEMPLATE}]


@router.post("/tasks", status_code=201)
def create_task(body: TaskInput, owner: str = Owner):
    return task_action(get_tasks().save, owner, body)


@router.put("/tasks/{task_id}")
def update_task(task_id: str, body: TaskInput, owner: str = Owner):
    return task_action(get_tasks().save, owner, body, task_id)


@router.patch("/tasks/{task_id}")
def enable_task(task_id: str, body: TaskEnabled, owner: str = Owner):
    return task_action(get_tasks().set_enabled, owner, task_id, body.enabled)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, owner: str = Owner):
    task_action(get_tasks().delete, owner, task_id)


@router.post("/tasks/{task_id}/run", status_code=202)
def run_task(task_id: str, owner: str = Owner):
    return task_action(get_tasks().run_now, owner, task_id)


@router.get("/tasks/{task_id}/runs")
def task_runs(task_id: str, owner: str = Owner):
    return task_action(get_tasks().history, owner, task_id)


@router.get("/task-notifications")
def notifications(owner: str = Owner):
    return get_tasks().notifications(owner)


@router.post("/task-notifications/{notification_id}/read", status_code=204)
def read_notification(notification_id: str, owner: str = Owner):
    task_action(get_tasks().mark_read, owner, notification_id)


@router.post("/task-notifications/read-all", status_code=204)
def read_all_notifications(owner: str = Owner):
    get_tasks().mark_all_read(owner)
