export type TextAttachment = { name: string; content: string };

export const FILE_ACCEPT =
  ".txt,.md,.csv,.json,.log,.yaml,.yml,.py,.js,.ts,.tsx,.jsx,.html,.css,.xml,.sql,.toml,.ini";
export const FILE_HELP =
  "Text, Markdown, CSV, JSON, or code · 3 files · 50 KB each, 100 KB total";
export const attachmentSize = (file: TextAttachment) =>
  new TextEncoder().encode(file.content).length;
export const formatFileSize = (bytes: number) =>
  bytes < 1024 ? `${bytes} B` : `${Math.ceil(bytes / 1024)} KB`;

export async function readAttachments(
  files: File[],
  existing: TextAttachment[],
): Promise<TextAttachment[]> {
  const next = [...existing];
  for (const file of files) {
    if (next.length >= 3) throw new Error("Attach up to 3 files per message.");
    const extension = `.${file.name.split(".").pop()?.toLowerCase()}`;
    if (!FILE_ACCEPT.split(",").includes(extension))
      throw new Error(
        `${file.name}: use a text, Markdown, CSV, JSON, or code file. Images and PDFs aren't supported yet.`,
      );
    if (!file.size || file.size > 50 * 1024)
      throw new Error(`${file.name}: choose a non-empty file under 50 KB.`);
    if (next.some((item) => item.name === file.name))
      throw new Error(
        `${file.name} is already attached. Remove it before adding a replacement.`,
      );
    let content: string;
    try {
      content = new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
    } catch {
      throw new Error(`${file.name}: couldn't read this file as UTF-8 text.`);
    }
    if (!content.trim() || /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(content))
      throw new Error(
        `${file.name}: the file is empty or contains binary data.`,
      );
    next.push({ name: file.name, content });
    if (
      next.reduce((total, item) => total + attachmentSize(item), 0) >
      100 * 1024
    )
      throw new Error("Attached files must total 100 KB or less.");
  }
  return next;
}
