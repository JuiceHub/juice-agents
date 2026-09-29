import subprocess
import sys


def test_prompt_preview_cli_defaults_to_complete_template() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "juice_agents.core.agent.prompts.preview",
            "--agent-type",
            "react",
            "--language",
            "en",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "{%- if memory_enabled %}" in result.stdout
    assert "{%- if skills_enabled and skills_metadata %}" in result.stdout
    assert "# Workspace Memory" in result.stdout
    assert "{{ memory_dir }}" in result.stdout
    assert "{{ memory_index }}" in result.stdout


def test_prompt_preview_cli_rendered_mode_keeps_runtime_preview() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "juice_agents.core.agent.prompts.preview",
            "--agent-type",
            "react",
            "--mode",
            "rendered",
            "--instructions",
            "保持答案简洁",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "# Engineering Execution" in result.stdout
    assert "保持答案简洁" in result.stdout
    assert "{{" not in result.stdout


def test_prompt_preview_cli_template_sections_include_memory() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "juice_agents.core.agent.prompts.preview",
            "--agent-type",
            "codeact",
            "--show-sections",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "[dynamic] memory" in result.stdout
    assert "# Workspace Memory" in result.stdout
