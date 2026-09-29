import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

// `check-cli-dependencies.mjs` 是无类型的构建期脚本（不在 tsconfig 的 include 内），
// 这里显式标注它的签名，避免为一个内部工具脚本额外维护 .d.ts。
// @ts-expect-error -- 无类型声明的 .mjs 工具脚本
import { findCliDependencyIssues as untypedFindCliDependencyIssues } from "../../check-cli-dependencies.mjs";

const findCliDependencyIssues = untypedFindCliDependencyIssues as (
  root: string,
) => string[];

// `@juice-agents/shared` 包含 React Hook。若它和 Ink 从不同 node_modules
// 加载 React，Hook dispatcher 会为空并在 CLI 首屏渲染时报 Invalid hook call。
test("shared conversation hooks resolve the same React instance as Ink", () => {
  const cliRequire = createRequire(import.meta.url);
  const sharedHookUrl = new URL(
    "../../shared/src/conversation/useConversationStream.ts",
    import.meta.url,
  );
  const sharedRequire = createRequire(fileURLToPath(sharedHookUrl));

  assert.equal(
    realpathSync(cliRequire.resolve("react")),
    realpathSync(sharedRequire.resolve("react")),
  );
});

function writeJson(path: string, value: unknown): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(value), "utf8");
}

test("dependency check rejects an installed version that differs from the lockfile", () => {
  const fixture = mkdtempSync(join(tmpdir(), "cli-dependency-check-"));

  try {
    writeJson(join(fixture, "package-lock.json"), {
      packages: {
        cli: { dependencies: { ink: "^5.0.1" } },
        "cli/node_modules/ink": { version: "5.2.1" },
      },
    });
    writeJson(join(fixture, "cli/node_modules/ink/package.json"), {
      name: "ink",
      version: "5.0.1",
    });

    assert.deepEqual(findCliDependencyIssues(fixture), [
      "ink: installed 5.0.1, lockfile requires 5.2.1",
    ]);

    writeJson(join(fixture, "cli/node_modules/ink/package.json"), {
      name: "ink",
      version: "5.2.1",
    });
    assert.deepEqual(findCliDependencyIssues(fixture), []);
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});
