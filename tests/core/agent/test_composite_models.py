import threading
import unittest

from juice_agents.core.models import CompositeModel


class FakeModel:
    def __init__(self, events, *, model_name="fake"):
        self.events = list(events)
        self.model_name = model_name
        self.calls = []

    def generate(self, messages, stop_sequence=None, *, cancel_event=None):
        self.calls.append(
            {
                "messages": messages,
                "stop_sequence": stop_sequence,
                "cancel_event": cancel_event,
            }
        )
        if not self.events:
            raise AssertionError(f"{self.model_name} events exhausted")
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        if isinstance(event, dict):
            return {
                "role": "assistant",
                "content": event.get("content", ""),
                "reasoning_content": event.get("reasoning_content", ""),
                "usage": event.get("usage"),
            }
        return {"role": "assistant", "content": event, "reasoning_content": ""}


class CompositeModelTests(unittest.TestCase):
    def test_judge_select_returns_judge_winner_and_skips_failed_candidate(self):
        cancel_event = threading.Event()
        bad = FakeModel([RuntimeError("down")], model_name="bad")
        first = FakeModel(["first answer"], model_name="first")
        second = FakeModel(["second answer"], model_name="second")
        judge = FakeModel(['{"winner":"candidate_3","scores":[{"id":"candidate_3","score":9}]}'], model_name="judge")
        model = CompositeModel(
            strategy="judge_select",
            candidates=[
                {"name": "bad", "model": bad},
                {"name": "first", "model": first},
                {"name": "second", "model": second},
            ],
            judge_model=judge,
        )

        result = model.generate(
            [{"role": "user", "content": "task"}],
            stop_sequence=["stop"],
            cancel_event=cancel_event,
        )

        self.assertEqual(result["content"], "second answer")
        self.assertEqual(first.calls[0]["stop_sequence"], ["stop"])
        self.assertIs(first.calls[0]["cancel_event"], cancel_event)
        judge_payload = judge.calls[0]["messages"][1]["content"]
        self.assertIn("candidate_2", judge_payload)
        self.assertIn("candidate_3", judge_payload)
        self.assertNotIn("candidate_1", judge_payload)

    def test_judge_select_raises_when_all_candidates_fail(self):
        model = CompositeModel(
            strategy="judge_select",
            candidates=[{"name": "a", "model": FakeModel([RuntimeError("a failed")])}],
            judge_model=FakeModel(['{"winner":"candidate_1"}']),
        )

        with self.assertRaisesRegex(RuntimeError, "没有成功候选输出"):
            model.generate([{"role": "user", "content": "task"}])

    def test_synthesize_passes_all_successful_candidates_to_final_model(self):
        first = FakeModel(["first answer"], model_name="first")
        second = FakeModel(["second answer"], model_name="second")
        final = FakeModel(["final answer"], model_name="final")
        model = CompositeModel(
            strategy="synthesize",
            candidates=[
                {"name": "first", "model": first},
                {"name": "second", "model": second},
            ],
            synthesis_model=final,
        )

        result = model.generate([{"role": "user", "content": "task"}], stop_sequence=["done"])

        self.assertEqual(result["content"], "final answer")
        final_payload = final.calls[0]["messages"][1]["content"]
        self.assertIn("first answer", final_payload)
        self.assertIn("second answer", final_payload)
        self.assertEqual(final.calls[0]["stop_sequence"], ["done"])

    def test_synthesize_aggregates_usage_from_candidates_and_final_model(self):
        first = FakeModel(
            [{"content": "first answer", "usage": {"input_tokens": 1, "output_tokens": 2}}],
            model_name="first",
        )
        second = FakeModel(
            [{"content": "second answer", "usage": {"prompt_tokens": 3, "completion_tokens": 4}}],
            model_name="second",
        )
        final = FakeModel(
            [{"content": "final answer", "usage": {"input_tokens": 5, "output_tokens": 6, "total_tokens": 12}}],
            model_name="final",
        )
        model = CompositeModel(
            strategy="synthesize",
            candidates=[
                {"name": "first", "model": first},
                {"name": "second", "model": second},
            ],
            synthesis_model=final,
        )

        result = model.generate([{"role": "user", "content": "task"}])

        self.assertEqual(result["content"], "final answer")
        self.assertEqual(result["usage"]["input_tokens"], 9)
        self.assertEqual(result["usage"]["output_tokens"], 12)
        self.assertEqual(result["usage"]["total_tokens"], 22)
        self.assertEqual(
            [(call["phase"], call["name"]) for call in result["usage"]["calls"]],
            [("candidate", "first"), ("candidate", "second"), ("synthesis", "final")],
        )

    def test_iterative_refine_revises_after_reviewer_rejects(self):
        generator = FakeModel(["draft", "fixed"], model_name="generator")
        reviewer = FakeModel(
            ['{"approved":false,"critique":"include result"}', '{"approved":true,"critique":"ok"}'],
            model_name="reviewer",
        )
        model = CompositeModel(
            strategy="iterative_refine",
            generator_model=generator,
            reviewer_models=[{"name": "reviewer", "model": reviewer}],
            max_refine_rounds=1,
        )

        result = model.generate([{"role": "user", "content": "task"}])

        self.assertEqual(result["content"], "fixed")
        self.assertEqual(len(generator.calls), 2)
        self.assertIn("include result", generator.calls[1]["messages"][1]["content"])

    def test_iterative_refine_raises_after_max_refine_rounds(self):
        generator = FakeModel(["draft"], model_name="generator")
        reviewer = FakeModel(['{"approved":false,"critique":"still wrong"}'], model_name="reviewer")
        model = CompositeModel(
            strategy="iterative_refine",
            generator_model=generator,
            reviewer_models=[{"name": "reviewer", "model": reviewer}],
            max_refine_rounds=0,
        )

        with self.assertRaisesRegex(RuntimeError, "超过最大轮次"):
            model.generate([{"role": "user", "content": "task"}])


if __name__ == "__main__":
    unittest.main()
