import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
import type { ActionStep, ActiveAgentSnapshot } from "../../frontend/shared/src/gateway/types.js";
import { presentActiveAgent } from "../../frontend/shared/src/presenter/stream.js";
const requireWeb = createRequire(new URL("../../frontend/web/package.json", import.meta.url));
const React = requireWeb("react") as typeof import("react");
const { renderToStaticMarkup } = requireWeb("react-dom/server");
/** A persisted CodeAct step contains both audited inner calls and its public wrapper. */
function savedCodeAct(status = "failed", observation = "Python process output", error = "PermissionError: private.md"): ActiveAgentSnapshot {
    const step: ActionStep = {
        step_num: 1, model_output: "", thought: "", reasoning_content: "",
        code_action: 'read(path="private.md")', observations: ["inner call output", observation],
        observation_images: [], attachments: [], error: null, round_outcome: "continue", output: null,
        tool_calls: [
            { name: "read", args: { path: "private.md" }, mode: "foreground", status: "failed",
                call_id: "inner-1", observation_index: 0, is_terminal: false },
            { name: "Python", args: { code: 'read(path="private.md")' }, mode: "foreground", status,
                call_id: "python-1", observation_index: 1, is_terminal: false, error, execution_time: 0.5 },
        ]
    };
    return {
        agent_id: "root-id", agent_name: "root", agent_kind: "root", agent_role: "root",
        status: "idle", is_root: true, current_async_task_id: "", metadata: {}, session: {},
        steps: [{ ...step, type: "action" }]
    };
}
async function renderHistory(actor: ActiveAgentSnapshot): Promise<string> {
    // The Web view imports ESM-only Markdown dependencies. Loading it through a
    // native dynamic import preserves their initialization under root test runs.
    const { ThreadView } = await import(new URL("../../frontend/web/src/components/ThreadView.tsx", import.meta.url).href);
    return renderToStaticMarkup(React.createElement(ThreadView, {
        status: null, activeActorName: null, models: [], skills: [], plugins: [], availableAgentNames: [],
        selectedModelName: "test-model", selectedModelEffort: "disabled", messages: presentActiveAgent(actor),
        streaming: false, queuedMessages: [], runtimeSwitching: false, error: "", pendingAsk: null,
        onSend: () => { }, onCancel: () => { }, onRuntimeChange: () => { }, onAnswerAsk: () => { }, onOpenFile: () => { }
    }));
}
test("shared restored CodeAct history projects one wrapper with its saved failure details", () => {
    const blocks = presentActiveAgent(savedCodeAct());
    const tools = blocks.filter((block) => block.kind === "tool");
    assert.equal(tools.length, 1);
    assert.equal(tools[0].title, "Failed Python");
    assert.equal(tools[0].toolCall?.callId, "python-1");
    assert.equal(tools[0].toolCall?.status, "failed");
    assert.equal(tools[0].toolCall?.parameters.code, 'read(path="private.md")');
    assert.equal(tools[0].toolCall?.executionTime, 0.5);
    assert.equal(tools[0].toolCall?.observation, "Python process output");
    assert.equal(tools[0].toolCall?.error, "PermissionError: private.md");
    assert.equal(blocks.some((block) => block.kind === "error"), false);
    assert.equal(blocks.some((block) => block.text.includes("inner call output")), false);
});
for (const status of ["failed", "denied"]) {
    test(`Web restored ${status} wrapper shows result and exception inside one failed tool card`, async () => {
        const html = await renderHistory(savedCodeAct(status));
        assert.equal((html.match(/<article class="message tool">/g) || []).length, 1);
        assert.match(html, /aria-label="Tool failed"/);
        assert.match(html, /<dt>code<\/dt>/);
        assert.match(html, /<dt>Result<\/dt><dd>Python process output<\/dd>/);
        assert.match(html, /<dt>Error<\/dt><dd>PermissionError: private.md<\/dd>/);
        assert.doesNotMatch(html, /inner call output/);
        assert.doesNotMatch(html, /class="message error"/);
    });
}
test("Web keeps a completed wrapper successful when returned text contains Error", async () => {
    const html = await renderHistory(savedCodeAct("completed", "Error: a sample domain result", ""));
    assert.match(html, /aria-label="Tool succeeded"/);
    assert.doesNotMatch(html, /aria-label="Tool failed"/);
    assert.match(html, /<dt>Result<\/dt><dd>Error: a sample domain result<\/dd>/);
    assert.match(html, /Completed Python/);
});
test("restored Python traceback includes its exception once in shared text and Web details", async () => {
    const error = "PermissionError: private.md";
    const observation = `[logs]\nreading private.md\n[error]\nTraceback (most recent call last):\n${error}`;
    const actor = savedCodeAct("failed", observation, error);
    const tool = presentActiveAgent(actor).find((block) => block.kind === "tool");
    const html = await renderHistory(actor);
    assert.equal(tool?.text.split(error).length, 2);
    assert.equal(html.split(error).length, 2);
    assert.match(html, /<dt>Result<\/dt>/);
    assert.doesNotMatch(html, /<dt>Error<\/dt>/);
});
