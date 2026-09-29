/**
 * Session state management hook.
 */

import { useState, useCallback } from "react";
import type { GatewayClient } from "../gateway/client.js";
import type { SessionStatus } from "@juice-agents/shared/gateway/types";

export function useSession(client: GatewayClient) {
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startSession = useCallback(
    async (params: {
      base_dir: string;
      permission_mode?: string;
      agent_mode?: string;
      agent_type?: string;
      runtime_config_path?: string;
      model_name?: string;
      model_effort?: string;
      worktree?: string;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const result = await client.startSession(params);
        setStatus(result);
        return result;
      } catch (e: any) {
        setError(e.message);
        throw e;
      } finally {
        setLoading(false);
      }
    },
    [client]
  );

  const resumeSession = useCallback(
    async (params: {
      runner_id: string;
      base_dir: string;
      runtime_config_path?: string;
      model_name?: string;
      model_effort?: string;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const result = await client.resumeSession(params);
        setStatus(result);
        return result;
      } catch (e: any) {
        setError(e.message);
        throw e;
      } finally {
        setLoading(false);
      }
    },
    [client]
  );

  const refreshStatus = useCallback(async () => {
    try {
      const result = await client.describeSession();
      setStatus(result);
      return result;
    } catch {
      // Session may not be started yet
    }
  }, [client]);

  return {
    status,
    setStatus,
    loading,
    error,
    startSession,
    resumeSession,
    refreshStatus,
  };
}
