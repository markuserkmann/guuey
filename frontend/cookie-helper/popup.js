import { exportCookies } from "./export.js";

const button = document.querySelector("#export");
const status = document.querySelector("#status");
button.addEventListener("click", async () => {
  if (button.disabled) return;
  button.disabled = true;
  status.textContent = "Requesting access to this site…";
  try {
    const { count, site } = await exportCookies(chrome, navigator.clipboard);
    status.textContent = `Exported ${count} ${count === 1 ? "cookie" : "cookies"} for ${site}. Paste into Guuey’s Saved logins.`;
  } catch (error) {
    status.textContent =
      error instanceof Error
        ? error.message
        : "Could not export cookies. Try again.";
  } finally {
    button.disabled = false;
  }
});
