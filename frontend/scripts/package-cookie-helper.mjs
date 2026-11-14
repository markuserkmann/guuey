import { readFile, mkdir, writeFile } from "node:fs/promises";
import { zipSync } from "fflate";

const files = [
  "manifest.json",
  "popup.html",
  "popup.css",
  "popup.js",
  "export.js",
];
const archive = Object.fromEntries(
  await Promise.all(
    files.map(async (file) => [
      file,
      new Uint8Array(
        await readFile(new URL(`../cookie-helper/${file}`, import.meta.url)),
      ),
    ]),
  ),
);
await mkdir(new URL("../public/", import.meta.url), { recursive: true });
await writeFile(
  new URL("../public/cookie-helper.zip", import.meta.url),
  zipSync(archive),
);
console.log("Packaged cookie-helper.zip");
