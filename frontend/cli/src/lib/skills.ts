import type { SkillInfo } from "@juice-agents/shared/gateway/types";

/**
 * Normalize skill aliases at the gateway boundary before UI code consumes them.
 *
 * Builtin skills often expose the same identifier as both `name` and
 * `qualified_name`. We keep distinct aliases such as `writer` and
 * `team/writer`, but collapse identical entries into a single stable list.
 */
export function collectSkillAliases(
  skills: Array<Pick<SkillInfo, "name" | "qualified_name">>
): string[] {
  const aliases: string[] = [];
  const seen = new Set<string>();

  for (const skill of skills) {
    for (const rawAlias of [skill.name, skill.qualified_name]) {
      const alias = String(rawAlias || "").trim();
      if (!alias || seen.has(alias)) {
        continue;
      }
      seen.add(alias);
      aliases.push(alias);
    }
  }

  return aliases;
}
