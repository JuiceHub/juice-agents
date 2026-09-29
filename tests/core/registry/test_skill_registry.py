import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from juice_agents.core.registry.skills import SkillRegistry


def write_skill(path: Path, name: str, description: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""
            ---
            name: {name}
            description: {description}
            {extra}
            ---

            # {name}

            Body for {name}.
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


class SkillRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)
        self.local = self.base / "workspace" / ".juice" / "skills"
        self.builtin = self.base / "builtin"
        self.external = self.base / "external"
        self.home = self.base / "home"
        self.config = self.base / "config.yaml"
        self.config.write_text(
            textwrap.dedent(
                f"""
                skills:
                  enabled: true
                  external_dirs:
                    - $JUICE_TEST_SKILLS/external
                  disabled: []
                  platform_disabled:
                    cli: []
                  template_vars: true
                  config:
                    local-skill:
                      tone: short
                """
            ).strip(),
            encoding="utf-8",
        )
        os.environ["JUICE_TEST_SKILLS"] = str(self.base)
        self.home_patch = patch.object(Path, "home", return_value=self.home)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.tmp_dir.cleanup()
        os.environ.pop("JUICE_TEST_SKILLS", None)

    def registry(self) -> SkillRegistry:
        return SkillRegistry(
            local_dir=self.local,
            builtin_dir=self.builtin,
            config_path=self.config,
        )

    def test_scans_local_builtin_and_external_with_precedence(self):
        write_skill(self.external / "external-skill" / "SKILL.md", "external-skill", "external desc")
        write_skill(self.builtin / "examples" / "shared" / "SKILL.md", "shared", "builtin desc")
        write_skill(self.local / "shared" / "SKILL.md", "shared", "local desc")

        skills = self.registry().list()
        by_name = {skill.name: skill for skill in skills}

        self.assertEqual(by_name["shared"].description, "local desc")
        self.assertEqual(by_name["shared"].source, "local")
        self.assertEqual(by_name["external-skill"].source, "external")

    def test_includes_project_and_home_agents_skills_as_default_external_dirs(self):
        project_external = self.base / "workspace" / ".agents" / "skills"
        home_external = self.home / ".agents" / "skills"
        write_skill(project_external / "project-skill" / "SKILL.md", "project-skill", "project desc")
        write_skill(home_external / "home-skill" / "SKILL.md", "home-skill", "home desc")

        registry = self.registry()

        self.assertEqual(
            registry.external_dirs,
            [
                project_external.resolve(),
                home_external.resolve(),
                self.external.resolve(),
            ],
        )
        self.assertEqual(
            [skill.name for skill in registry.list()],
            ["home-skill", "project-skill"],
        )

    def test_project_external_cannot_override_builtin_precedence(self):
        project_external = self.base / "workspace" / ".agents" / "skills"
        write_skill(self.builtin / "shared" / "SKILL.md", "shared", "builtin desc")
        write_skill(project_external / "shared" / "SKILL.md", "shared", "project external desc")

        registry = self.registry()

        shared = registry.get("shared")

        self.assertEqual(shared.source, "builtin")
        self.assertEqual(shared.description, "builtin desc")

    def test_frontmatter_fallbacks_and_body_description(self):
        skill_path = self.builtin / "body-desc" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("# Title\n\nUse this body paragraph.", encoding="utf-8")

        meta = self.registry().get("body-desc")

        self.assertEqual(meta.name, "body-desc")
        self.assertEqual(meta.description, "Use this body paragraph.")

    def test_platform_and_disabled_filters(self):
        self.config.write_text(
            textwrap.dedent(
                f"""
                skills:
                  enabled: true
                  external_dirs: []
                  disabled: [disabled-skill]
                  platform_disabled:
                    cli: [cli-disabled]
                """
            ).strip(),
            encoding="utf-8",
        )
        write_skill(self.builtin / "disabled-skill" / "SKILL.md", "disabled-skill", "disabled")
        write_skill(self.builtin / "cli-disabled" / "SKILL.md", "cli-disabled", "cli disabled")
        write_skill(self.builtin / "other-platform" / "SKILL.md", "other-platform", "other", "platforms: [web]")
        write_skill(self.builtin / "visible" / "SKILL.md", "visible", "visible")

        self.assertEqual([skill.name for skill in self.registry().list()], ["visible"])

    def test_workspace_overlay_can_disable_all_skills(self):
        workspace_config = self.base / "workspace" / ".juice" / "config.yaml"
        workspace_config.parent.mkdir(parents=True, exist_ok=True)
        workspace_config.write_text("skills:\n  enabled: false\n", encoding="utf-8")
        write_skill(self.builtin / "visible" / "SKILL.md", "visible", "visible")

        registry = SkillRegistry(
            local_dir=self.local,
            builtin_dir=self.builtin,
            config_path=self.config,
            workspace_dir=self.base / "workspace",
        )

        self.assertEqual(registry.list(), [])
        self.assertFalse(registry.config_status()["enabled"])
        self.assertEqual(registry.config_status()["skills"][0]["name"], "visible")
        self.assertFalse(registry.config_status()["skills"][0]["enabled"])

    def test_workspace_overlay_disabled_skill_still_appears_in_config_status(self):
        workspace_config = self.base / "workspace" / ".juice" / "config.yaml"
        workspace_config.parent.mkdir(parents=True, exist_ok=True)
        workspace_config.write_text("skills:\n  disabled: [hidden]\n", encoding="utf-8")
        write_skill(self.builtin / "hidden" / "SKILL.md", "hidden", "hidden")
        write_skill(self.builtin / "visible" / "SKILL.md", "visible", "visible")

        registry = SkillRegistry(
            local_dir=self.local,
            builtin_dir=self.builtin,
            config_path=self.config,
            workspace_dir=self.base / "workspace",
        )
        status = registry.config_status()

        self.assertEqual([skill.name for skill in registry.list()], ["visible"])
        self.assertEqual(
            {item["name"]: item["enabled"] for item in status["skills"]},
            {"hidden": False, "visible": True},
        )

    def test_category_description_and_lookup_forms(self):
        write_skill(self.local / "examples" / "local-skill" / "SKILL.md", "local-skill", "local desc")
        (self.local / "examples" / "DESCRIPTION.md").write_text(
            "# Examples\n\nExample category skills.",
            encoding="utf-8",
        )
        registry = self.registry()

        self.assertEqual(registry.get("local-skill").category, "examples")
        self.assertEqual(registry.get("examples/local-skill").name, "local-skill")
        self.assertIn("examples", registry.category_descriptions())

    def test_skill_view_rejects_path_traversal_and_reports_files(self):
        write_skill(self.local / "local-skill" / "SKILL.md", "local-skill", "local desc")
        (self.local / "local-skill" / "references").mkdir()
        (self.local / "local-skill" / "references" / "api.md").write_text("api", encoding="utf-8")
        registry = self.registry()

        view = registry.view("local-skill")
        missing = registry.view("local-skill", file_path="references/missing.md")

        self.assertIn("references/api.md", view["available_files"])
        self.assertFalse(missing["success"])
        with self.assertRaises(ValueError):
            registry.view("local-skill", file_path="../secret.txt")

    def test_filters_skills_by_required_tools(self):
        write_skill(
            self.builtin / "shell-skill" / "SKILL.md",
            "shell-skill",
            "shell desc",
            "requires_tools: [shell]\n",
        )
        write_skill(
            self.builtin / "browser-skill" / "SKILL.md",
            "browser-skill",
            "browser desc",
            "requires_tools: [browser_open_url]\n",
        )
        write_skill(self.builtin / "plain-skill" / "SKILL.md", "plain-skill", "plain desc")

        registry = SkillRegistry(
            local_dir=self.local,
            builtin_dir=self.builtin,
            config_path=self.config,
            available_tools=["shell", "python"],
        )
        missing_shell = SkillRegistry(
            local_dir=self.local,
            builtin_dir=self.builtin,
            config_path=self.config,
            available_tools=["python"],
        )

        self.assertEqual([skill.name for skill in registry.list()], ["plain-skill", "shell-skill"])
        self.assertEqual([skill.name for skill in missing_shell.list()], ["plain-skill"])

    def test_filters_fallback_skills_when_primary_capability_is_available(self):
        write_skill(
            self.builtin / "web-fallback" / "SKILL.md",
            "web-fallback",
            "fallback desc",
            "fallback_for_tools: [api_web_search]\n",
        )
        write_skill(self.builtin / "always-visible" / "SKILL.md", "always-visible", "always desc")

        with_web = SkillRegistry(
            local_dir=self.local,
            builtin_dir=self.builtin,
            config_path=self.config,
            available_tools=["api_web_search"],
        )
        without_web = SkillRegistry(
            local_dir=self.local,
            builtin_dir=self.builtin,
            config_path=self.config,
            available_tools=["shell"],
        )

        self.assertEqual([skill.name for skill in with_web.list()], ["always-visible"])
        self.assertEqual([skill.name for skill in without_web.list()], ["always-visible", "web-fallback"])


if __name__ == "__main__":
    unittest.main()
