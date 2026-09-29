// 所有 backend 的 effort 合集（用于类型定义和通用校验）
export const MODEL_EFFORTS = ["disabled", "low", "medium", "high", "xhigh", "max", "auto"] as const;

export type ModelEffort = (typeof MODEL_EFFORTS)[number];

export function isModelEffort(value: string | undefined): value is ModelEffort {
  return MODEL_EFFORTS.includes(value as ModelEffort);
}

export function normalizeModelEffort(value: string | undefined): ModelEffort {
  const normalized = String(value || "").trim().toLowerCase();
  return isModelEffort(normalized) ? normalized : "disabled";
}

export interface ModelEffortSource {
  supported_efforts?: readonly string[];
}

export function getSupportedModelEfforts(source?: ModelEffortSource | null): ModelEffort[] {
  if (source === undefined || source === null) {
    return [...MODEL_EFFORTS];
  }
  const values = source?.supported_efforts || [];
  const efforts = values
    .map((value) => String(value || "").trim().toLowerCase())
    .filter(isModelEffort);
  const unique = [...new Set(efforts)];
  return unique.length > 0 ? unique : ["disabled"];
}

export function normalizeEffortForModel(
  value: string | undefined,
  source?: ModelEffortSource | null
): ModelEffort {
  const normalized = normalizeModelEffort(value);
  const efforts = getSupportedModelEfforts(source);
  return efforts.includes(normalized) ? normalized : efforts[0] || "disabled";
}

export function cycleModelEffort(
  current: ModelEffort | string | undefined,
  direction: "left" | "right",
  source?: ModelEffortSource | null
): ModelEffort {
  const efforts = getSupportedModelEfforts(source);
  const normalized = normalizeModelEffort(current);
  // 当前值不在该模型列表中时，回到该模型的第一个候选。
  const index = efforts.indexOf(normalized);
  const safeIndex = index >= 0 ? index : 0;
  const nextIndex =
    direction === "right"
      ? (safeIndex + 1) % efforts.length
      : (safeIndex - 1 + efforts.length) % efforts.length;
  return efforts[nextIndex]!;
}
