import { apiFetch, getRun, latestRun, type ChatSource } from "./chatApi";

export type TaskInput = {
  title: string;
  prompt: string;
  notification_condition: string;
  interval_minutes: number;
  timezone: string;
  enabled: boolean;
  telegram_enabled: boolean;
};

export type RecurringTask = TaskInput & {
  id: string;
  chat_id: string;
  next_run_at: string;
  last_run_at: string | null;
  last_success_at: string | null;
  last_status: string | null;
  last_error: string | null;
};

export type TaskTemplate = Omit<
  TaskInput,
  "timezone" | "enabled" | "telegram_enabled"
> & {
  id: string;
};
export type TaskCheck = {
  run_id: string;
  status: string;
  outcome: string;
  note: string | null;
  created_at: string;
};
export type TaskNotification = {
  id: string;
  task_id: string;
  chat_id: string;
  title: string;
  content: string;
  sources: ChatSource[];
  created_at: string;
  read_at: string | null;
};
export type TaskInbox = { unread_count: number; items: TaskNotification[] };

async function request<T>(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const response = await apiFetch(path, token, init);
  return response.status === 204 ? (undefined as T) : response.json();
}

export const listTasks = (token: string, signal?: AbortSignal) =>
  request<RecurringTask[]>("/api/tasks", token, { signal });
export const taskTemplates = (token: string, signal?: AbortSignal) =>
  request<TaskTemplate[]>("/api/task-templates", token, { signal });
export const taskInbox = (token: string, signal?: AbortSignal) =>
  request<TaskInbox>("/api/task-notifications", token, { signal });
export const saveTask = (token: string, input: TaskInput, id?: string) =>
  request<RecurringTask>(
    id ? `/api/tasks/${encodeURIComponent(id)}` : "/api/tasks",
    token,
    {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(input),
    },
  );
export const enableTask = (token: string, id: string, enabled: boolean) =>
  request<RecurringTask>(`/api/tasks/${encodeURIComponent(id)}`, token, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
export const deleteTask = (token: string, id: string) =>
  request<void>(`/api/tasks/${encodeURIComponent(id)}`, token, {
    method: "DELETE",
  });
export const checkTask = (token: string, id: string) =>
  request<{ run_id: string }>(
    `/api/tasks/${encodeURIComponent(id)}/run`,
    token,
    { method: "POST" },
  );
export const taskHistory = (token: string, id: string, signal?: AbortSignal) =>
  request<TaskCheck[]>(`/api/tasks/${encodeURIComponent(id)}/runs`, token, {
    signal,
  });

export async function latestTaskRun(
  token: string,
  chatId: string,
  taskId: string,
) {
  const [conversationRun, checks] = await Promise.all([
    latestRun(token, chatId),
    taskHistory(token, taskId),
  ]);
  const check = checks[0];
  if (
    !check ||
    (conversationRun &&
      Date.parse(conversationRun.created_at) >= Date.parse(check.created_at))
  )
    return conversationRun;
  return getRun(token, check.run_id);
}
export const readTaskUpdate = (token: string, id: string) =>
  request<void>(
    `/api/task-notifications/${encodeURIComponent(id)}/read`,
    token,
    { method: "POST" },
  );
export const readAllTaskUpdates = (token: string) =>
  request<void>("/api/task-notifications/read-all", token, { method: "POST" });

export function taskFrequency(minutes: number) {
  if (minutes === 1) return "Every minute";
  if (minutes === 60) return "Every hour";
  if (minutes === 1440) return "Every day";
  if (minutes === 10080) return "Every week";
  return minutes % 60 === 0
    ? `Every ${minutes / 60} hours`
    : `Every ${minutes} minutes`;
}

export function taskOutcome(value: string | null) {
  return (
    (
      {
        queued: "Queued",
        running: "Checking",
        notified: "New update",
        no_match: "No new matches",
        failed: "Check failed",
        cancelled: "Cancelled",
      } as Record<string, string>
    )[value ?? ""] ?? "Not checked yet"
  );
}
