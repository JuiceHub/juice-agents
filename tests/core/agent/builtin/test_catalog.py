import unittest

from juice_agents.core.agent.builtin.configs import (
    build_builtin_agent_config,
    get_explore_config,
    get_general_config,
    get_general_subagent_config,
)


class BuiltinAgentConfigTests(unittest.TestCase):
    """The built-in declarations intentionally exclude a dedicated plan agent."""

    def test_default_root_manages_only_general_and_explore(self) -> None:
        root = get_general_config()

        self.assertEqual(root.managed_agent_names, ("general", "explore"))
        self.assertNotIn("plan", root.managed_agent_names)

    def test_builtin_templates_do_not_reserve_plan_name(self) -> None:
        self.assertEqual(get_explore_config().name, "explore")
        self.assertEqual(get_general_subagent_config().allowed_modes, ("agent", "plan"))
        self.assertEqual(get_explore_config().allowed_modes, ("agent", "plan"))
        with self.assertRaisesRegex(ValueError, "未知内置 Agent 模板"):
            build_builtin_agent_config("plan")


if __name__ == "__main__":
    unittest.main()
