import React, { useMemo, useState } from "react";
import { Check, Power, RefreshCcw, Search, X } from "lucide-react";
import type { SkillConfigInfo, SkillInfo, SkillsConfigStatusResponse } from "@juice-agents/shared/gateway/types";
import { filterInstalledSkills, skillIconLabel } from "../lib/skills.js";

export function buildNextSkillToggleConfig(params: {
  skill: Pick<SkillConfigInfo, "name" | "qualified_name" | "enabled">;
  enabled: boolean;
  disabled: string[];
}): { enabled: boolean; disabled: string[] } {
  const aliases = [params.skill.name, params.skill.qualified_name]
    .map((item) => String(item || "").trim().toLowerCase())
    .filter(Boolean);
  const key = aliases[1] || aliases[0] || "";
  const nextDisabled = new Set(params.disabled.map((item) => item.trim().toLowerCase()).filter(Boolean));
  if (key) {
    if (!params.skill.enabled) {
      for (const alias of aliases) {
        nextDisabled.delete(alias);
      }
    } else {
      nextDisabled.add(key);
    }
  }
  return {
    enabled: params.enabled,
    disabled: [...nextDisabled].sort(),
  };
}

export function SkillsPage(props: {
  skills: SkillInfo[];
  config: SkillsConfigStatusResponse | null;
  loading: boolean;
  error: string;
  onRefresh: () => void;
  onSaveConfig: (next: { enabled: boolean; disabled: string[] }) => void;
}) {
  const [query, setQuery] = useState("");
  const editableSkills = useMemo(
    () => props.config?.skills || props.skills.map((skill) => ({ ...skill, enabled: true })),
    [props.config, props.skills]
  );
  const visibleSkills = useMemo(() => filterInstalledSkills(editableSkills, query), [editableSkills, query]);
  const disabled = useMemo(() => new Set(props.config?.disabled || []), [props.config?.disabled]);
  const skillsEnabled = props.config?.enabled !== false;

  function toggleAll(): void {
    props.onSaveConfig({
      enabled: !skillsEnabled,
      disabled: [...disabled],
    });
  }

  function toggleSkill(skill: SkillConfigInfo): void {
    props.onSaveConfig(buildNextSkillToggleConfig({
      skill,
      enabled: skillsEnabled,
      disabled: [...disabled],
    }));
  }

  return (
    <main className="workspace-page skills-page">
      <header className="workspace-page-toolbar">
        <button className={`skill-power ${skillsEnabled ? "enabled" : ""}`} onClick={toggleAll} disabled={props.loading}>
          <Power size={14} />
          <span>{skillsEnabled ? "Skills on" : "Skills off"}</span>
        </button>
        <button className="ghost-action" onClick={props.onRefresh} disabled={props.loading}>
          <RefreshCcw size={14} />
          <span>{props.loading ? "Refreshing" : "Refresh"}</span>
        </button>
        <label className="toolbar-search">
          <Search size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search skills"
            aria-label="Search skills"
          />
        </label>
      </header>

      <section className="workspace-page-body">
        <div className="page-heading">
          <h1>Skills</h1>
          <p>Installed skills available in this workspace.</p>
        </div>

        <div className="section-label">Installed</div>
        {props.error ? <div className="error-banner page-error">{props.error}</div> : null}
        {visibleSkills.length === 0 ? (
          <div className="page-empty">
            <strong>No installed skills found</strong>
            <span>{query ? "Try a different search." : "Refresh after installing workspace or user skills."}</span>
          </div>
        ) : (
          <div className="skills-grid">
            {visibleSkills.map((skill) => (
              <article key={skill.qualified_name || skill.name} className="skill-row">
                <div className="skill-icon" aria-hidden="true">
                  {skillIconLabel(skill)}
                </div>
                <div className="skill-copy">
                  <strong>{skill.name || skill.qualified_name}</strong>
                  <span>{skill.description || skill.qualified_name}</span>
                  <small>{[skill.category, skill.source].filter(Boolean).join(" · ")}</small>
                </div>
                <button
                  className={`skill-toggle ${skillsEnabled && skill.enabled ? "enabled" : ""}`}
                  onClick={() => toggleSkill(skill)}
                  disabled={props.loading || !skillsEnabled}
                  title={skill.enabled ? "Disable skill" : "Enable skill"}
                  aria-label={skill.enabled ? "Disable skill" : "Enable skill"}
                >
                  {skillsEnabled && skill.enabled ? <Check size={16} /> : <X size={16} />}
                </button>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
