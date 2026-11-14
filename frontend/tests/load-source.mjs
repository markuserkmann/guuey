import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";

export async function loadSource(entries, serverRender = false) {
  const directory = await mkdtemp(join(tmpdir(), "guuey-ui-test-"));
  const outfile = join(directory, "test.cjs");
  try {
    await build({
      stdin: {
        contents:
          entries
            .map((entry) => `export * from ${JSON.stringify(entry)};`)
            .join("\n") +
          (serverRender
            ? '\nexport { renderToStaticMarkup } from "react-dom/server";\nexport { createElement } from "react";'
            : ""),
        resolveDir: process.cwd(),
        loader: "tsx",
      },
      bundle: true,
      platform: "node",
      format: "cjs",
      jsx: "automatic",
      define: { "import.meta.env.DEV": "false" },
      outfile,
      logLevel: "silent",
    });
    return createRequire(import.meta.url)(outfile);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}
