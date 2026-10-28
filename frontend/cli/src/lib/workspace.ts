import path from "node:path";
import { statSync } from "node:fs";

export interface ResolveWorkspaceDirParams {
  env: NodeJS.ProcessEnv;
  cwd: string;
  workspace?: string;
  validate?: boolean;
}

const WORKSPACE_ENV_KEYS = ["JUICE_WORKSPACE_DIR", "INIT_CWD"] as const;

/** Resolve explicit CLI directory before the environment and launch cwd. */
export function resolveWorkspaceDir({ env, cwd, workspace, validate = false }: ResolveWorkspaceDirParams): string {
  const configured = workspace ?? WORKSPACE_ENV_KEYS.map((key) => env[key]?.trim()).find(Boolean) ?? cwd;
  const resolved = path.resolve(cwd, configured);
  if (validate) {
    try {
      if (!statSync(resolved).isDirectory()) throw new Error("not a directory");
    } catch {
      throw new Error(`Workspace is missing or is not a directory: ${resolved}`);
    }
  }
  return resolved;
}
