import operator
import threading
import time
import unittest
from typing import Annotated
from typing import TypedDict

from juice_agents.core.graph import END, StateGraph
from juice_agents.core.graph.types import (
    Command,
    GraphCancelledError,
    GraphIncompleteError,
    GraphPausedError,
    InvalidConcurrentGraphUpdate,
    Send,
)


class _ListState(TypedDict):
    items: Annotated[list[int], operator.add]


class _ValueState(TypedDict):
    value: int


class _MixedState(TypedDict):
    items: Annotated[list[int], operator.add]
    value: int


class _MetaState(TypedDict):
    count: int
    meta: dict


class _LogState(TypedDict):
    logs: Annotated[list[str], operator.add]


class StateGraphTests(unittest.TestCase):
    def test_add_node_deprecated_ends_arg_removed(self):
        g = StateGraph(_ValueState)

        def a(state):
            return {"value": state.get("value", 0) + 1}

        with self.assertRaises(TypeError):
            g.add_node("A", a, ends=["B"])

    def test_linear_flow_runs_to_finish(self):
        g = StateGraph(_ValueState)

        def a(state):
            return {"value": state.get("value", 0) + 1}

        def b(state):
            return {"value": state.get("value", 0) + 10}

        g.add_node("A", a)
        g.add_node("B", b)
        g.set_entry_point("A")
        g.add_edge("A", "B")
        g.add_edge("B", END)

        app = g.compile()
        out = app.invoke({"value": 0}, config={"max_steps": 10})
        self.assertEqual(out["value"], 11)

    def test_conditional_edges_route_by_state(self):
        g = StateGraph(_ValueState)

        def router_node(state):
            return {}

        def left(state):
            return {"value": 1}

        def right(state):
            return {"value": 2}

        def choose(state):
            return "left" if state.get("value", 0) == 0 else "right"

        g.add_node("router", router_node)
        g.add_node("left", left)
        g.add_node("right", right)
        g.set_entry_point("router")
        g.add_conditional_edges("router", choose, {"left": "left", "right": "right"})
        g.add_edge("left", END)
        g.add_edge("right", END)

        app = g.compile()
        out1 = app.invoke({"value": 0})
        self.assertEqual(out1["value"], 1)
        out2 = app.invoke({"value": 1})
        self.assertEqual(out2["value"], 2)

    def test_parallel_reducer_merges_updates(self):
        g = StateGraph(_ListState)

        def start(state):
            return {}

        def b(state):
            return {"items": [1]}

        def c(state):
            return {"items": [2]}

        g.add_node("start", start)
        g.add_node("B", b)
        g.add_node("C", c)
        g.set_entry_point("start")
        g.add_edge("start", ["B", "C"])
        g.add_edge("B", END)
        g.add_edge("C", END)

        app = g.compile()
        out = app.invoke({"items": []})
        self.assertEqual(out["items"], [1, 2])

    def test_invalid_concurrent_update_raises(self):
        g = StateGraph(_ValueState)

        def start(state):
            return {}

        def b(state):
            return {"value": 1}

        def c(state):
            return {"value": 2}

        g.add_node("start", start)
        g.add_node("B", b)
        g.add_node("C", c)
        g.set_entry_point("start")
        g.add_edge("start", ["B", "C"])
        g.add_edge("B", END)
        g.add_edge("C", END)

        app = g.compile()
        with self.assertRaises(InvalidConcurrentGraphUpdate):
            app.invoke({"value": 0})

    def test_command_goto_overrides_edges(self):
        g = StateGraph(_ValueState)

        def a(state):
            return Command(update={"value": 1}, goto="C")

        def b(state):
            return {"value": 999}

        def c(state):
            return {"value": state.get("value", 0) + 1}

        g.add_node("A", a)
        g.add_node("B", b)
        g.add_node("C", c)
        g.set_entry_point("A")
        g.add_edge("A", "B")
        g.add_edge("B", END)
        g.add_edge("C", END)

        app = g.compile()
        out = app.invoke({"value": 0})
        self.assertEqual(out["value"], 2)

    def test_send_arg_applies_to_target_node_state(self):
        g = StateGraph(_ValueState)

        def a(state):
            return Command(goto=Send("B", {"value": 10}))

        def b(state):
            return {"value": state.get("value", 0) + 1}

        g.add_node("A", a)
        g.add_node("B", b)
        g.set_entry_point("A")
        g.add_edge("B", END)

        app = g.compile()
        out = app.invoke({"value": 0})
        self.assertEqual(out["value"], 11)

    def test_defer_node_merges_send_args(self):
        g = StateGraph(_ListState)
        agg_calls = {"count": 0}

        def start(state):
            return {}

        def b(state):
            return Command(goto=Send("Agg", {"items": [1]}))

        def c(state):
            return Command(goto=Send("Agg", {"items": [2]}))

        def agg(state):
            agg_calls["count"] += 1
            return {"items": state.get("items", [])}

        g.add_node("start", start)
        g.add_node("B", b)
        g.add_node("C", c)
        g.add_node("Agg", agg, defer=True)
        g.set_entry_point("start")
        g.add_edge("start", ["B", "C"])
        g.add_edge("Agg", END)

        app = g.compile()
        out = app.invoke({"items": []})
        self.assertEqual(out["items"], [1, 2])
        self.assertEqual(agg_calls["count"], 1)

    def test_send_nested_arg_can_be_deduped(self):
        g = StateGraph(_MetaState)

        def a(state):
            shared = {"meta": {"x": [1, 2]}}
            return Command(goto=[Send("B", shared), Send("B", {"meta": {"x": [1, 2]}})])

        def b(state):
            return {"count": state.get("count", 0) + 1, "meta": state.get("meta", {})}

        g.add_node("A", a)
        g.add_node("B", b)
        g.set_entry_point("A")
        g.add_edge("B", END)

        app = g.compile()
        out = app.invoke({"count": 0, "meta": {}})
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["meta"], {"x": [1, 2]})

    def test_finish_point_stops_branch_even_with_extra_edges(self):
        g = StateGraph(_ValueState)

        def a(state):
            return {"value": state.get("value", 0) + 1}

        def b(state):
            return {"value": state.get("value", 0) + 10}

        def c(state):
            return {"value": state.get("value", 0) + 100}

        g.add_node("A", a)
        g.add_node("B", b)
        g.add_node("C", c)
        g.set_entry_point("A")
        g.add_edge("A", "B")
        g.set_finish_point("B")
        g.add_edge("B", "C")
        g.add_edge("C", END)

        app = g.compile()
        out = app.invoke({"value": 0})
        self.assertEqual(out["value"], 11)

    def test_stream_events_are_emitted_in_task_idx_order(self):
        g = StateGraph(_ListState)

        def start(state):
            return {}

        def b(state):
            return {"items": [1]}

        def c(state):
            return {"items": [2]}

        g.add_node("start", start)
        g.add_node("B", b)
        g.add_node("C", c)
        g.set_entry_point("start")
        g.add_edge("start", ["B", "C"])
        g.add_edge("B", END)
        g.add_edge("C", END)

        app = g.compile()
        stream_events = [evt for evt in app.stream({"items": []}) if evt.get("node") in ("B", "C")]
        self.assertEqual([evt["node"] for evt in stream_events], ["B", "C"])

    def test_visualize_outputs_mermaid(self):
        g = StateGraph(_MixedState)

        def a(state):
            return {"value": 1}

        def b(state):
            return {"items": [1]}

        g.add_node("A", a)
        g.add_node("B", b)
        g.set_entry_point("A")
        g.add_edge("A", "B")
        g.add_edge("B", END)

        app = g.compile()
        mermaid = app.to_mermaid()
        self.assertIn("flowchart", mermaid)
        self.assertIn("A", mermaid)
        self.assertIn("B", mermaid)

    def test_defer_node_runs_after_all_pending_tasks_complete(self):
        g = StateGraph(_LogState)

        def start(state):
            return {"logs": ["start"]}

        def b(state):
            return {"logs": ["b"]}

        def c(state):
            return {"logs": ["c1"]}

        def d(state):
            return {"logs": ["c2"]}

        def agg(state):
            return {"logs": ["agg"]}

        g.add_node("start", start)
        g.add_node("B", b)
        g.add_node("C", c)
        g.add_node("D", d)
        g.add_node("Agg", agg, defer=True)
        g.set_entry_point("start")

        g.add_edge("start", ["B", "C"])
        g.add_edge("C", "D")
        g.add_edge("B", "Agg")
        g.add_edge("D", "Agg")
        g.add_edge("Agg", END)

        app = g.compile()
        out = app.invoke({"logs": []})
        self.assertEqual(out["logs"].count("agg"), 1)
        self.assertEqual(out["logs"][-1], "agg")

    def test_checkpoint_resume_does_not_repeat_completed_node(self):
        g = StateGraph(_ValueState)
        g.add_node("A", lambda state: {"value": state["value"] + 1})
        g.add_node("B", lambda state: {"value": state["value"] + 10})
        g.set_entry_point("A")
        g.add_edge("A", "B")
        g.add_edge("B", END)
        app = g.compile()

        checkpoints = []
        controls = iter(["", "pause"])
        with self.assertRaises(GraphPausedError):
            list(
                app.stream(
                    {"value": 0},
                    config={
                        "configurable": {
                            "checkpoint_callback": checkpoints.append,
                            "control_callback": lambda: next(controls, "pause"),
                        }
                    },
                )
            )

        resumed = app.invoke(
            {"value": 0},
            config={"configurable": {"checkpoint": checkpoints[-1]}},
        )
        self.assertEqual(resumed["value"], 11)

    def test_max_steps_is_an_explicit_failure(self):
        g = StateGraph(_ValueState)
        g.add_node("loop", lambda state: {"value": state["value"] + 1})
        g.set_entry_point("loop")
        g.add_edge("loop", "loop")
        with self.assertRaises(GraphIncompleteError):
            g.compile().invoke({"value": 0}, config={"max_steps": 2})

    def test_timeout_cancel_and_task_limit_are_explicit_failures(self):
        timed = StateGraph(_ValueState)

        def slow(state):
            time.sleep(0.02)
            return {"value": state["value"] + 1}

        timed.add_node("slow", slow)
        timed.set_entry_point("slow")
        timed.add_edge("slow", END)
        with self.assertRaisesRegex(GraphIncompleteError, "timeout_seconds"):
            timed.compile().invoke({"value": 0}, config={"timeout_seconds": 0.001})

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(GraphCancelledError):
            timed.compile().invoke({"value": 0}, config={"cancel_event": cancelled})

        fanout = StateGraph(_ListState)
        fanout.add_node(
            "dispatch",
            lambda state: [
                Send("work", {"items": [1]}),
                Send("work", {"items": [2]}),
                Send("work", {"items": [3]}),
            ],
        )
        fanout.add_node("work", lambda state: {})
        fanout.set_entry_point("dispatch")
        fanout.add_edge("work", END)
        with self.assertRaisesRegex(GraphIncompleteError, "max_scheduled_tasks"):
            fanout.compile().invoke({"items": []}, config={"max_scheduled_tasks": 2})


if __name__ == "__main__":
    unittest.main()
