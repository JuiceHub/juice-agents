import path from "node:path";

export interface ResolveWorkspaceDirParams {
  env: NodeJS.ProcessEnv;
  cwd: string;
}

const WORKSPACE_ENV_KEYS = ["JUICE_WORKSPACE_DIR", "INIT_CWD"] as const;

/**
 * Resolve the user's workspace separately from the CLI package runtime cwd.
 *
 * The `juice` wrapper enters `frontend/cli` so npm can run the TypeScript Ink
 * package, but backend sessions, tools, preferences, and `.juice` data must
 * stay rooted at the directory where the user launched the command.
 */
export function resolveWorkspaceDir({ env, cwd }: ResolveWorkspaceDirParams): string {
  for (const key of WORKSPACE_ENV_KEYS) {
    const value = String(env[key] || "").trim();
    if (value) {
      return path.resolve(cwd, value);
    }
  }

  return path.resolve(cwd);
}
