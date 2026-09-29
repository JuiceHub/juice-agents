import assert from "node:assert/strict";
import test from "node:test";

import { resolveWorkspaceDir } from "./workspace.js";

test("workspace resolver prefers JUICE_WORKSPACE_DIR over npm INIT_CWD and cwd", () => {
  const workspace = resolveWorkspaceDir({
    env: {
      JUICE_WORKSPACE_DIR: "/tmp/user-project",
      INIT_CWD: "/tmp/npm-started-here",
    },
    cwd: "/tmp/frontend/cli",
  });

  assert.equal(workspace, "/tmp/user-project");
});

test("workspace resolver falls back through INIT_CWD to cwd", () => {
  assert.equal(
    resolveWorkspaceDir({
      env: {
        JUICE_WORKSPACE_DIR: "   ",
        INIT_CWD: "/tmp/npm-project",
      },
      cwd: "/tmp/frontend/cli",
    }),
    "/tmp/npm-project"
  );

  assert.equal(
    resolveWorkspaceDir({
      env: {},
      cwd: "/tmp/frontend/cli",
    }),
    "/tmp/frontend/cli"
  );
});

test("workspace resolver returns an absolute normalized path", () => {
  const workspace = resolveWorkspaceDir({
    env: {
      JUICE_WORKSPACE_DIR: "../project",
    },
    cwd: "/tmp/frontend/cli",
  });

  assert.equal(workspace, "/tmp/frontend/project");
});
