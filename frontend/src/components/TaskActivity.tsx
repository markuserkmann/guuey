import { Icon } from "./Icon";

export function TaskActivity({ label }: { label: string }) {
  return (
    <span className="task-activity" role="status" aria-label={label}>
      <Icon name="repeat" />
    </span>
  );
}
