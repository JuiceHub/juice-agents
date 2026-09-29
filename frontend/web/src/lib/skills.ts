import type { SkillInfo } from "@juice-agents/shared/gateway/types";

export function collectSkillAliases(
  skills: Array<Pick<SkillInfo, "name" | "qualified_name">>
): string[] {
  const aliases: string[] = [];
  const seen = new Set<string>();

  for (const skill of skills) {
    for (const rawAlias of [skill.name, skill.qualified_name]) {
      const alias = String(rawAlias || "").trim();
      if (!alias || seen.has(alias)) continue;
      seen.add(alias);
      aliases.push(alias);
    }
  }

  return aliases;
}

export function filterInstalledSkills<T extends SkillInfo>(skills: T[], query: string): T[] {
  const normalized = query.trim().toLowerCase();
  const ordered = [...skills].sort((left, right) =>
    (left.name || left.qualified_name).localeCompare(right.name || right.qualified_name)
  );
  if (!normalized) return ordered;
  return ordered.filter((skill) =>
    [
      skill.name,
      skill.qualified_name,
      skill.description,
      skill.category || "",
      skill.source,
    ]
      .join("\n")
      .toLowerCase()
      .includes(normalized)
  );
}

export function skillIconLabel(skill: SkillInfo): string {
  const name = (skill.name || skill.qualified_name || "S").trim();
  return name.slice(0, 1).toUpperCase();
}
