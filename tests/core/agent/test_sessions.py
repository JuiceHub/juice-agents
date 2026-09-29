import base64
import os
import unittest

from juice_agents.core.agent.agent_type import ObservationImage
from juice_agents.core.agent.attachments import serialize_runtime_attachments_text
from juice_agents.core.agent.sessions import (
    ActionStep,
    AgentError,
    AgentSession,
    CompressibleAgentSession,
    CompactCompressStrategy,
    DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER,
    NoCompressStrategy,
    RemainCompressStrategy,
    SummaryStep,
    TaskStep,
)

SAMPLE_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5W9r8AAAAASUVORK5CYII="
)


def _join_text_blocks(message: dict[str, object]) -> str:
    content = message.get("content", [])
    if not isinstance(content, list):
        return ""
    return "".join(str(block.get("text") or "") for block in content if isinstance(block, dict))


class SessionTests(unittest.TestCase):
    def test_action_step_normalizes_display_fields_without_model_output_mirror(self):
        step = ActionStep(
            step_num=1,
            model_output="<thought>raw</thought>",
            thought="plan",
            tool_calls=[{"name": "read", "args": {"path": "demo.py"}}, "bad"],
            code_action="print('ok')",
        )

        self.assertEqual(step.model_output, "<thought>raw</thought>")
        self.assertEqual(step.thought, "plan")
        self.assertEqual(step.tool_calls, [{"name": "read", "args": {"path": "demo.py"}}])
        self.assertEqual(step.code_action, "print('ok')")

    def test_action_step_model_output_assignment_does_not_derive_display_fields(self):
        step = ActionStep(step_num=1, model_output="", thought="kept")

        step.model_output = "<thought>updated</thought><code>print('ok')</code>"

        self.assertEqual(step.model_output, "<thought>updated</thought><code>print('ok')</code>")
        self.assertEqual(step.thought, "kept")

    def test_observation_image_cache_dir(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            img = ObservationImage.from_image(image=SAMPLE_PNG_BYTES, description="x", cache_dir=d)
            self.assertTrue(img.image_url)
            self.assertTrue(os.path.exists(img.image_url))
            self.assertEqual(img.ensure_image_url(), img.image_url)
            self.assertTrue(img.to_bytes())
            try:
                from PIL import Image as _PILImage  # type: ignore
            except Exception:
                _PILImage = None
            if _PILImage is not None:
                self.assertIsNotNone(img.raw_image)

            with self.assertRaises(ValueError):
                ObservationImage.from_image(image=SAMPLE_PNG_BYTES, description="y", cache_dir=None)

    def test_observation_image_default_cache_dir_is_workspace_juice(self):
        import tempfile

        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                img = ObservationImage.from_image(image=SAMPLE_PNG_BYTES, description="default")
            finally:
                os.chdir(old_cwd)

            self.assertIn(".juice/observation_images", img.image_url)
            self.assertTrue(os.path.exists(os.path.join(d, img.image_url)))

    def test_observation_image_rejects_base64_string(self):
        b64 = base64.b64encode(SAMPLE_PNG_BYTES).decode("utf-8")
        with self.assertRaises(ValueError):
            ObservationImage.from_image_url(image_url=b64, description="bad")

    def test_task_step_to_messages(self):
        step = TaskStep(task="solve", task_images=[SAMPLE_PNG_BYTES])
        msgs = step.to_messages()
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"][0]["text"], "<task>solve</task>")
        # task image 被 XML 标签包裹：起始标签 -> image block -> 结束标签
        self.assertEqual(msgs[0]["content"][1]["text"], "<task_image_1>")
        self.assertEqual(msgs[0]["content"][2]["type"], "image_url")
        task_image_url = str(msgs[0]["content"][2]["image_url"]["url"])
        self.assertFalse(task_image_url.startswith("data:image"))
        self.assertTrue(task_image_url.startswith("http") or os.path.exists(task_image_url))
        self.assertEqual(msgs[0]["content"][3]["text"], "</task_image_1>")

    def test_task_step_to_messages_with_attachments(self):
        step = TaskStep(
            task="solve",
            attachments=[
                {
                    "attachment_type": "inbox_message",
                    "created_at": 1.0,
                    "payload": {
                        "from": "teamlead",
                        "summary": "assigned task_1",
                        "text": "Please take task_1",
                        "timestamp": "2026-04-08T00:00:00Z",
                    },
                }
            ],
        )

        msgs = step.to_messages()

        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        joined = _join_text_blocks(msgs[0])
        self.assertIn("<task>solve</task>", joined)
        self.assertIn("<attachments>", joined)
        self.assertIn("<inbox_message", joined)
        self.assertIn("assigned task_1", joined)

    def test_action_step_to_messages(self):
        raw = "<thought>thought</thought><actions>[]</actions>"
        step = ActionStep(
            step_num=1,
            model_output=raw,
            observations=["obs"],
            observations_images=[ObservationImage.from_image(image=SAMPLE_PNG_BYTES, description="desc")],
            round_outcome="submitted",
            output="obs",
        )
        msgs = step.to_messages()
        self.assertEqual(msgs[0]["role"], "assistant")
        self.assertEqual(msgs[0]["content"][0]["text"], raw)
        self.assertEqual(msgs[1]["role"], "user")
        joined = _join_text_blocks(msgs[1])
        self.assertIn("\n<observations>\n", joined)
        self.assertIn("<result_of_action_0>\nobs\n</result_of_action_0>", joined)
        self.assertIn("<observation_image_1>\n<description>desc</description>\n", joined)
        image_block_index = next(idx for idx, block in enumerate(msgs[1]["content"]) if block.get("type") == "image_url")
        self.assertEqual(msgs[1]["content"][image_block_index]["type"], "image_url")
        self.assertEqual(set(msgs[1]["content"][image_block_index].keys()), {"type", "image_url"})
        self.assertIn("\n</observation_image_1>\n", joined)
        self.assertIn("\n</observations>\n", joined)

    def test_action_step_preserves_observation_text_verbatim(self):
        step = ActionStep(
            step_num=1,
            model_output="",
            observations=["<result_of_action_7>\nlegacy\n</result_of_action_7>"],
        )

        joined = _join_text_blocks(step.to_messages()[0])

        self.assertIn(
            "<result_of_action_0>\n<result_of_action_7>\nlegacy\n</result_of_action_7>\n</result_of_action_0>",
            joined,
        )

    def test_action_step_to_messages_order_and_error(self):
        step = ActionStep(
            step_num=2,
            model_output="m",
            observations=["obs2"],
            observations_images=[ObservationImage.from_image(image=SAMPLE_PNG_BYTES, description="desc")],
            error=AgentError("boom"),
        )
        msgs = step.to_messages()
        self.assertEqual([m["role"] for m in msgs], ["assistant", "user"])
        joined = _join_text_blocks(msgs[1])
        self.assertIn("\n<observations>\n", joined)
        self.assertIn("obs2", joined)
        self.assertIn("\n<error>\nboom\n</error>\n", joined)
        self.assertLess(joined.index("\n<observations>\n"), joined.index("\n<error>\nboom\n</error>\n"))

    def test_action_step_to_messages_with_attachments(self):
        step = ActionStep(
            step_num=4,
            model_output="m",
            observations=["obs"],
            attachments=[
                {
                    "attachment_type": "async_task_notification",
                    "created_at": 1.0,
                    "payload": {
                        "async_task_id": "job_1",
                        "status": "completed",
                        "summary": "dispatch done",
                        "result": {"answer": 42},
                        "output_dir": "/tmp/job_1",
                    },
                }
            ],
        )
        msgs = step.to_messages()
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["role"], "user")
        joined = _join_text_blocks(msgs[1])
        self.assertIn("\n<observations>\n", joined)
        self.assertIn("<attachments>", joined)
        self.assertIn("async_task_notification", joined)
        self.assertIn("job_1", joined)
        self.assertIn("dispatch done", joined)
        self.assertIn("{&quot;answer&quot;: 42}", joined)
        self.assertLess(joined.index("\n<observations>\n"), joined.index("<attachments>"))

    def test_action_step_to_messages_error_only(self):
        step = ActionStep(step_num=3, model_output="", error=AgentError("fail"))
        msgs = step.to_messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"][0]["text"], "\n<error>\nfail\n</error>\n")

    def test_action_step_to_messages_merges_all_feedback_blocks_into_one_user_message(self):
        step = ActionStep(
            step_num=5,
            model_output="m",
            observations=["obs"],
            attachments=[
                {
                    "attachment_type": "async_task_notification",
                    "created_at": 2.0,
                    "payload": {
                        "async_task_id": "job_2",
                        "status": "completed",
                        "summary": "shell done",
                    },
                }
            ],
            error=AgentError("boom"),
        )

        msgs = step.to_messages()

        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["role"], "user")
        joined = _join_text_blocks(msgs[1])
        self.assertLess(joined.index("\n<observations>\n"), joined.index("<attachments>"))
        self.assertLess(joined.index("<attachments>"), joined.index("\n<error>\nboom\n</error>\n"))

    def test_agent_session_to_messages(self):
        session = AgentSession(system_prompt="system")
        session.append_step(TaskStep(task="task"))
        msgs = session.to_messages()
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")

    def test_action_step_to_messages_truncation(self):
        long_text = "x" * 80
        step = ActionStep(
            step_num=1,
            model_output="m",
            observations=[long_text],
            observation_limits=[70],
        )
        msgs = step.to_messages()
        truncated = next(
            block["text"]
            for block in msgs[1]["content"]
            if block.get("type") == "text"
            and "[truncated original_length=" in str(block.get("text") or "")
        )
        self.assertIn("original_length=80", truncated)
        self.assertIn("omitted_length=", truncated)
        self.assertLessEqual(len(truncated), 70)
        self.assertEqual(step.observations, [long_text])

    def test_agent_session_uses_each_observation_effective_limit(self):
        long_text = "y" * 80
        session = AgentSession(system_prompt="system")
        session.append_step(
            ActionStep(
                step_num=1,
                model_output="m",
                observations=[long_text],
                observation_limits=[70],
            )
        )
        msgs = session.to_messages()
        text_blocks = [
            block["text"]
            for msg in msgs
            for block in msg.get("content", [])
            if block.get("type") == "text"
        ]
        combined = "\n".join(text_blocks)
        self.assertIn("[truncated original_length=80", combined)

    def test_large_raw_observation_stays_complete_while_model_context_is_bounded(self):
        raw = "z" * 400_000
        step = ActionStep(step_num=1, model_output="m", observations=[raw])
        session = AgentSession(system_prompt="system")
        session.append_step(step)

        messages = session.to_messages()
        projected = "\n".join(
            str(block.get("text") or "")
            for message in messages
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )

        self.assertEqual(len(step.observations[0]), 400_000)
        self.assertLessEqual(projected.count("z"), 12_000)
        self.assertIn("original_length=400000", projected)
        self.assertIn("omitted_length=", projected)

    def test_observation_projections_have_no_shared_session_budget(self):
        raw_observations = [f"observation-{idx:02d}:" + "x" * 4_000 for idx in range(29)]
        session = AgentSession(system_prompt="system")
        for idx, raw in enumerate(raw_observations, start=1):
            session.append_step(
                ActionStep(
                    step_num=idx,
                    model_output=f"model-{idx}",
                    observations=[raw],
                    observation_limits=[len(raw)],
                )
            )

        messages = session.to_messages()
        projected = "\n".join(
            str(block.get("text") or "")
            for message in messages
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )

        # 29 条 observation 的投影合计远超旧版 64K 总预算，但彼此不再抢占；
        # 每条都只受自身有效限额约束，且不会产生空 text block。
        for raw in raw_observations:
            self.assertIn(raw, projected)
        self.assertGreater(projected.count("x"), 64_000)
        self.assertTrue(
            all(
                str(block.get("text") or "").strip()
                for message in messages
                for block in message.get("content", [])
                if isinstance(block, dict) and block.get("type") == "text"
            )
        )
        self.assertEqual(
            [step.observations[0] for step in session.steps if isinstance(step, ActionStep)],
            raw_observations,
        )

    def test_compressible_session_remain_strategy(self):
        session = AgentSession(system_prompt="system")
        session.append_step(TaskStep(task="task"))
        step1 = ActionStep(
            step_num=1,
            model_output="m1",
            observations=["obs1"],
            observations_images=[ObservationImage.from_image(image=SAMPLE_PNG_BYTES, description="desc1")],
            attachments=[
                {
                    "attachment_type": "async_task_notification",
                    "created_at": 1.0,
                    "payload": {
                        "async_task_id": "job_1",
                        "status": "completed",
                        "summary": "dispatch done",
                        "result": {"answer": 42},
                        "output_dir": "/tmp/job_1",
                    },
                }
            ],
        )
        step2 = ActionStep(
            step_num=2,
            model_output="m2",
            observations=["obs2"],
            observations_images=[],
        )
        session.append_step(step1)
        session.append_step(step2)

        strategy = RemainCompressStrategy(k=1)
        compressible = CompressibleAgentSession(session, strategy)
        msgs = compressible.to_messages()

        # remain 只生成请求投影，不得回写 canonical session。
        canonical_steps = [s for s in compressible.session.steps if isinstance(s, ActionStep)]
        self.assertEqual(canonical_steps[-1].observations, ["obs2"])
        self.assertEqual(canonical_steps[0].observations, ["obs1"])
        self.assertEqual(len(canonical_steps[0].observations_images), 1)
        self.assertEqual(
            canonical_steps[0].attachments,
            [
                {
                    "attachment_type": "async_task_notification",
                    "created_at": 1.0,
                    "payload": {
                        "async_task_id": "job_1",
                        "status": "completed",
                        "summary": "dispatch done",
                        "result": {"answer": 42},
                        "output_dir": "/tmp/job_1",
                    },
                }
            ],
        )
        self.assertIsInstance(msgs, list)
        self.assertGreater(len(msgs), 0)
        projected = "\n".join(_join_text_blocks(message) for message in msgs)
        self.assertIn(DEFAULT_OMITTED_OBSERVATION_PLACEHOLDER, projected)
        self.assertIn("obs2", projected)
        self.assertNotIn("obs1", projected)

    def test_serialize_runtime_attachments_text_emits_completed_job_summary_and_result(self):
        serialized = serialize_runtime_attachments_text(
            [
                {
                    "attachment_type": "async_task_notification",
                    "created_at": 1.0,
                    "payload": {
                        "async_task_id": "job_1",
                        "status": "completed",
                        "summary": "shell done",
                        "result": {"stdout": "ok"},
                        "output_dir": "/tmp/job_1",
                    },
                },
                {
                    "attachment_type": "inbox_message",
                    "created_at": 2.0,
                    "payload": {
                        "from": "teamlead",
                        "text": "please check task_1",
                        "summary": "assigned task_1",
                        "timestamp": "2026-04-04T00:00:02Z",
                    },
                },
            ]
        )

        self.assertIn("<attachments>", serialized)
        self.assertIn('<async_task_notification async_task_id="job_1" status="completed" output_dir="/tmp/job_1">', serialized)
        self.assertIn("shell done", serialized)
        self.assertIn("{&quot;stdout&quot;: &quot;ok&quot;}", serialized)
        self.assertIn("<inbox_message", serialized)
        self.assertLess(serialized.index("<async_task_notification"), serialized.index("<inbox_message"))

    def test_serialize_runtime_attachments_text_failed_job_omits_removed_fields(self):
        serialized = serialize_runtime_attachments_text(
            [
                {
                    "attachment_type": "async_task_notification",
                    "created_at": 1.0,
                    "payload": {
                        "async_task_id": "job_2",
                        "status": "failed",
                        "summary": "worker failed",
                        "output_dir": "/tmp/job_2",
                    },
                }
            ]
        )

        self.assertIn("worker failed", serialized)
        self.assertNotIn("kind=", serialized)
        self.assertNotIn("stderr_preview", serialized)
        self.assertNotIn("returncode", serialized)
        self.assertNotIn("error", serialized)

    def test_no_compress_strategy_never_compresses(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(TaskStep(task="t"))
        session.append_step(ActionStep(step_num=1, model_output="m", observations=["o"]))
        strategy = NoCompressStrategy()
        self.assertFalse(strategy.should_compress(session))
        out = strategy.compress(session)
        self.assertEqual(len(out.steps), 2)
        self.assertEqual(_count_action_steps(out), 1)

    def test_remain_should_compress_when_has_action_steps(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(TaskStep(task="t"))
        self.assertFalse(RemainCompressStrategy(k=0).should_compress(session))
        session.append_step(ActionStep(step_num=1, model_output="m", observations=["o"]))
        self.assertTrue(RemainCompressStrategy(k=0).should_compress(session))

    def test_compact_should_compress_at_threshold(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(TaskStep(task="t"))

        class DummyModel:
            def generate(self, messages, stop_sequence=None):
                return {"role": "assistant", "content": "<summary>brief</summary>"}

        strategy = CompactCompressStrategy(step_threshold=2, compression_model=DummyModel())
        self.assertFalse(strategy.should_compress(session))
        session.append_step(ActionStep(step_num=1, model_output="m1", observations=["o1"]))
        self.assertFalse(strategy.should_compress(session))
        session.append_step(ActionStep(step_num=2, model_output="m2", observations=["o2"]))
        self.assertTrue(strategy.should_compress(session))

    def test_compact_compress_returns_summary_step_session(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(TaskStep(task="t"))
        session.append_step(ActionStep(step_num=1, model_output="m", observations=["o"]))

        class DummyModel:
            def generate(self, messages, stop_sequence=None):
                return {"role": "assistant", "content": "<summary>User asked for X. Done A, B. Next: C.</summary>"}

        strategy = CompactCompressStrategy(step_threshold=1, compression_model=DummyModel())
        out = strategy.compress(session)
        self.assertEqual(len(out.steps), 2)
        self.assertIsInstance(out.steps[0], SummaryStep)
        self.assertIsInstance(out.steps[1], ActionStep)
        self.assertIn("summary", out.steps[0].content.lower())
        self.assertIn("User asked for X", out.steps[0].content)

    def test_compact_compress_preserves_recent_action_suffix(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(TaskStep(task="t"))
        session.append_step(ActionStep(step_num=1, model_output="m1", observations=["o1"]))
        session.append_step(ActionStep(step_num=2, model_output="m2", observations=["o2"]))
        session.append_step(ActionStep(step_num=3, model_output="m3", observations=["o3"]))

        class DummyModel:
            def generate(self, messages, stop_sequence=None):
                del messages, stop_sequence
                return {"role": "assistant", "content": "<summary>Done.</summary>"}

        strategy = CompactCompressStrategy(step_threshold=1, compression_model=DummyModel())
        out = strategy.compress(session)

        self.assertEqual(len(out.steps), 2)
        self.assertIsInstance(out.steps[0], SummaryStep)
        self.assertIsInstance(out.steps[1], ActionStep)
        self.assertEqual(out.steps[1].step_num, 3)
        self.assertEqual(out.steps[1].observations, ["o3"])

    def test_compact_compress_expands_suffix_when_tail_has_error(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(TaskStep(task="t"))
        session.append_step(ActionStep(step_num=1, model_output="m1", observations=["o1"]))
        session.append_step(ActionStep(step_num=2, model_output="m2", observations=["o2"]))
        session.append_step(
            ActionStep(
                step_num=3,
                model_output="m3",
                observations=["o3"],
                error=AgentError("boom"),
            )
        )

        class DummyModel:
            def generate(self, messages, stop_sequence=None):
                del messages, stop_sequence
                return {"role": "assistant", "content": "<summary>Done.</summary>"}

        strategy = CompactCompressStrategy(step_threshold=1, compression_model=DummyModel())
        out = strategy.compress(session)

        self.assertEqual(len(out.steps), 3)
        self.assertIsInstance(out.steps[0], SummaryStep)
        self.assertEqual([step.step_num for step in out.steps[1:]], [2, 3])

    def test_compact_compress_excludes_system_prompt_from_summary_transcript(self):
        session = AgentSession(system_prompt="SYSTEM SECRET")
        session.append_step(TaskStep(task="t"))
        session.append_step(ActionStep(step_num=1, model_output="m1", observations=["o1"]))

        captured: dict[str, object] = {}

        class DummyModel:
            def generate(self, messages, stop_sequence=None):
                del stop_sequence
                captured["messages"] = messages
                return {"role": "assistant", "content": "<summary>Done.</summary>"}

        strategy = CompactCompressStrategy(step_threshold=1, compression_model=DummyModel())
        strategy.compress(session)

        summarizer_messages = captured["messages"]
        self.assertIsInstance(summarizer_messages, list)
        user_message = summarizer_messages[1]
        self.assertNotIn("SYSTEM SECRET", user_message["content"][0]["text"])
        self.assertIn("<task>t</task>", user_message["content"][0]["text"])

    def test_compact_summary_input_uses_per_observation_limits_without_remain(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(TaskStep(task="t"))
        session.append_step(
            ActionStep(
                step_num=1,
                model_output="m1",
                observations=["a" * 40_000],
                observation_limits=[40_000],
            )
        )
        session.append_step(
            ActionStep(
                step_num=2,
                model_output="m2",
                observations=["b" * 40_000],
                observation_limits=[40_000],
            )
        )
        session.append_step(ActionStep(step_num=3, model_output="m3", observations=["recent"]))
        captured: dict[str, object] = {}

        class DummyModel:
            def generate(self, messages, stop_sequence=None):
                del stop_sequence
                captured["messages"] = messages
                return {"role": "assistant", "content": "<summary>Done.</summary>"}

        CompactCompressStrategy(step_threshold=1, compression_model=DummyModel()).compress(session)

        summarizer_messages = captured["messages"]
        self.assertIsInstance(summarizer_messages, list)
        transcript = summarizer_messages[1]["content"][0]["text"]
        older_chars = transcript.count("a")
        newer_chars = transcript.count("b")
        self.assertGreaterEqual(newer_chars, 40_000)
        self.assertLessEqual(newer_chars, 40_100)
        self.assertGreaterEqual(older_chars, 40_000)
        self.assertLessEqual(older_chars, 40_100)
        self.assertGreater(older_chars + newer_chars, 64_000)

    def test_compact_requires_compression_model(self):
        with self.assertRaises(ValueError):
            CompactCompressStrategy(step_threshold=1, compression_model=None)
        with self.assertRaises(ValueError):
            CompactCompressStrategy(step_threshold=1, compression_model=object())

    def test_to_messages_triggers_compression_when_threshold_met(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(TaskStep(task="t"))
        session.append_step(ActionStep(step_num=1, model_output="m", observations=["o"]))

        class DummyModel:
            def generate(self, messages, stop_sequence=None):
                return {"role": "assistant", "content": "<summary>Done.</summary>"}

        strategy = CompactCompressStrategy(step_threshold=1, compression_model=DummyModel())
        compressible = CompressibleAgentSession(session, strategy)
        msgs = compressible.to_messages()
        self.assertEqual(len(compressible.session.steps), 2)
        self.assertIsInstance(compressible.session.steps[0], SummaryStep)
        self.assertIsInstance(compressible.session.steps[1], ActionStep)
        self.assertIsInstance(msgs, list)
        self.assertGreaterEqual(len(msgs), 2)

    def test_agent_session_clone_copies_summary_step(self):
        session = AgentSession(system_prompt="sys")
        session.append_step(SummaryStep(content="summary text"))

        cloned = session.clone()

        self.assertIsNot(session.steps[0], cloned.steps[0])
        self.assertEqual(cloned.steps[0].content, "summary text")


def _count_action_steps(session):
    return sum(1 for s in session.steps if isinstance(s, ActionStep))


class SegmentedSystemPromptTests(unittest.TestCase):
    """覆盖分段 system prompt 的消息构造与序列化往返。"""

    def test_segmented_system_message_marks_static_cache_boundary(self):
        from juice_agents.core.agent.sessions.content import CACHE_BOUNDARY_KEY

        session = AgentSession(
            system_prompt="静态\n\n动态",
            system_prompt_static="静态",
            system_prompt_dynamic="动态",
        )

        system_msg = session.to_messages()[0]
        blocks = system_msg["content"]

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["text"], "静态")
        self.assertTrue(blocks[0][CACHE_BOUNDARY_KEY])
        self.assertEqual(blocks[1]["text"], "动态")
        self.assertNotIn(CACHE_BOUNDARY_KEY, blocks[1])

    def test_empty_dynamic_segment_emits_single_block(self):
        session = AgentSession(
            system_prompt="静态",
            system_prompt_static="静态",
            system_prompt_dynamic="",
        )

        blocks = session.to_messages()[0]["content"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "静态")

    def test_legacy_single_segment_session_unchanged(self):
        from juice_agents.core.agent.sessions.content import CACHE_BOUNDARY_KEY

        # static 为 None 表示历史/自定义模板路径，退回单段且不打缓存标记
        session = AgentSession(system_prompt="只有单段")

        blocks = session.to_messages()[0]["content"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "只有单段")
        self.assertNotIn(CACHE_BOUNDARY_KEY, blocks[0])

    def test_serialization_round_trips_segments(self):
        from juice_agents.core.agent.sessions import deserialize_session, serialize_session

        session = AgentSession(
            system_prompt="静态\n\n动态",
            system_prompt_static="静态",
            system_prompt_dynamic="动态",
        )
        session.append_step(TaskStep(task="做点什么"))

        restored = deserialize_session(serialize_session(session))

        self.assertEqual(restored.system_prompt_static, "静态")
        self.assertEqual(restored.system_prompt_dynamic, "动态")
        self.assertEqual(restored.system_prompt, "静态\n\n动态")

    def test_serialization_preserves_legacy_none_segments(self):
        from juice_agents.core.agent.sessions import deserialize_session, serialize_session

        session = AgentSession(system_prompt="单段")
        restored = deserialize_session(serialize_session(session))

        self.assertIsNone(restored.system_prompt_static)
        self.assertIsNone(restored.system_prompt_dynamic)

    def test_serialization_round_trips_observation_limits_and_request_metrics(self):
        from juice_agents.core.agent.sessions import deserialize_session, serialize_session

        session = AgentSession(system_prompt="sys")
        session.append_step(
            ActionStep(
                step_num=1,
                model_output="m",
                observations=["full observation"],
                observation_limits=[321],
                request_bytes=654,
                usage={"input_tokens": 100, "output_tokens": 20},
            )
        )

        payload = serialize_session(session)
        restored = deserialize_session(payload)
        restored_step = restored.steps[0]

        self.assertIsInstance(restored_step, ActionStep)
        self.assertEqual(restored_step.observation_limits, [321])
        self.assertEqual(restored_step.request_bytes, 654)
        self.assertEqual(
            restored_step.usage,
            {"input_tokens": 100, "output_tokens": 20},
        )

    def test_deserialization_ignores_legacy_session_observation_budgets(self):
        from juice_agents.core.agent.sessions import deserialize_session

        restored = deserialize_session(
            {
                "system_prompt": "legacy",
                "max_observation_length": 7,
                "max_observation_context_length": 9,
                "steps": [
                    {
                        "type": "action",
                        "step_num": 1,
                        "model_output": "m",
                        "observations": ["x" * 100],
                    }
                ],
            }
        )

        self.assertFalse(hasattr(restored, "max_observation_length"))
        self.assertFalse(hasattr(restored, "max_observation_context_length"))
        restored_step = restored.steps[0]
        self.assertIsInstance(restored_step, ActionStep)
        self.assertEqual(restored_step.observation_limits, [])
        self.assertEqual(restored_step.observations, ["x" * 100])


if __name__ == "__main__":
    unittest.main()
