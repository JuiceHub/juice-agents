"""MCP tool integration contracts."""

import unittest


class _FakeConn:
    def __init__(self, tools=None):
        self._tools = tools or []
        self.calls = []

    def list_tools(self):
        return list(self._tools)

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return {"name": name, "arguments": dict(arguments)}

    def close(self):
        return None


class MCPToolsTests(unittest.TestCase):
    def test_remote_tool_schema_to_inputs_required_optional(self):
        from juice_agents.core.agent.tools.builtin.mcp.mcp_tools import MCPRemoteTool

        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "a"},
                "b": {"type": "string", "description": "b"},
            },
            "required": ["a"],
        }
        conn = _FakeConn()
        tool = MCPRemoteTool(
            server_name="s",
            remote_name="add",
            description="desc",
            input_schema=schema,
            connection=conn,  # type: ignore[arg-type]
            exposed_name="s.add",
        )
        self.assertEqual(tool.inputs["a"]["required"], True)
        self.assertEqual(tool.inputs["b"]["required"], False)
        self.assertEqual(tool.inputs["a"]["type"], "integer")
        self.assertEqual(tool.inputs["b"]["type"], "string")

    def test_mcp_tools_get_tools_and_forward_namespaced(self):
        from juice_agents.core.agent.tools.builtin.mcp.mcp_tools import MCPTools

        schema = {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"],
        }
        fake = _FakeConn(
            tools=[
                {"name": "add", "description": "add two numbers", "input_schema": schema},
            ]
        )

        m = MCPTools(
            [
                {
                    "name": "calc",
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-c", "print('noop')"],
                    "namespace_tools": True,
                }
            ]
        )
        m._connections["calc"] = fake  # type: ignore[assignment]

        tools = m.get_tools(refresh=True)
        self.assertEqual(len(tools), 1)
        t = tools[0]
        self.assertEqual(t.name, "calc.add")

        out = t(x=1, y=2)
        self.assertEqual(out["name"], "add")
        self.assertEqual(out["arguments"], {"x": 1, "y": 2})
        self.assertEqual(fake.calls, [("add", {"x": 1, "y": 2})])

    def test_mcp_tools_get_tools_without_namespace(self):
        from juice_agents.core.agent.tools.builtin.mcp.mcp_tools import MCPTools

        fake = _FakeConn(
            tools=[
                {"name": "greet", "description": "hi", "input_schema": {"type": "object", "properties": {}}},
            ]
        )
        m = MCPTools(
            [
                {
                    "name": "srv",
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-c", "print('noop')"],
                    "namespace_tools": False,
                }
            ]
        )
        m._connections["srv"] = fake  # type: ignore[assignment]
        tools = m.get_tools(refresh=True)
        self.assertEqual(tools[0].name, "greet")

    def test_mcp_tools_custom_namespace_separator(self):
        from juice_agents.core.agent.tools.builtin.mcp.mcp_tools import MCPTools

        fake = _FakeConn(
            tools=[
                {"name": "add", "description": "add", "input_schema": {"type": "object", "properties": {}}},
            ]
        )
        m = MCPTools(
            [
                {
                    "name": "demo",
                    "transport": "stdio",
                    "command": "python",
                    "args": ["-c", "print('noop')"],
                    "namespace_tools": True,
                    "namespace_separator": "__",
                }
            ]
        )
        m._connections["demo"] = fake  # type: ignore[assignment]
        tools = m.get_tools(refresh=True)
        self.assertEqual(tools[0].name, "demo__add")

    def test_config_validation(self):
        from juice_agents.core.agent.tools.builtin.mcp.mcp_tools import MCPServerConfig

        with self.assertRaises(ValueError):
            MCPServerConfig(name="", command="python").validate()

        with self.assertRaises(ValueError):
            MCPServerConfig(name="x", transport="http", command="python").validate()


if __name__ == "__main__":
    unittest.main()
