import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
import type { ToolCallBlock } from "../../frontend/shared/src/presenter/stream.js";
import { presentActiveAgent, presentStreamEvent, type MessageBlock } from "../../frontend/cli/src/lib/presenter.js";
import { conversationReducer, createConversationState } from "../../frontend/shared/src/conversation/state.js";
import type { ActiveAgentSnapshot, RunnerStreamEvent } from "../../frontend/shared/src/gateway/types.js";
import type { RowVM } from "../../frontend/cli/src/lib/transcriptRows.js";
import { TUI_THEME } from "../../frontend/cli/src/lib/theme.js";
const requireCli = createRequire(new URL("../../frontend/cli/package.json", import.meta.url));
// Root tests are loaded as CommonJS. Import the Ink renderer asynchronously so
// yoga-layout's ESM initialization is not transformed into a synchronous require.
const renderer = import(new URL("../../frontend/cli/src/lib/transcriptRows.tsx", import.meta.url).href);
async function render(block: MessageBlock, columns: number, key: string): Promise<RowVM[]> {
    return (await renderer).flattenBlockToRows(block, columns, key);
}
function tool(status: string, overrides: Partial<ToolCallBlock> = {}): MessageBlock {
    return {
        id: "tool:call-1",
        kind: "tool",
        // Structured tool calls render their lifecycle fields, never this raw text.
        text: "hidden observation body",
        toolCall: {
            name: "read",
            mode: "foreground",
            status,
            parameters: { path: "README.md" },
            ...overrides
        }
    };
}
for (const [status, label, glyph] of [
    ["running", "Running", "▸"],
    ["completed", "Completed", "✓"],
    ["success", "Completed", "✓"],
    ["background", "Launched", "↗"],
    ["failed", "Failed", "✗"],
    ["error", "Failed", "✗"],
    ["denied", "Denied", "✗"],
    ["cancelled", "Cancelled", "⊘"],
    ["unknown", "Unconfirmed", "▸"],
]) {
    test(`CLI tool lifecycle renders ${status} with its own label`, async () => {
        const rows = await render(tool(status), 100, `status:${status}`);
        assert.equal(rows.length, 1);
        assert.equal(rows[0].prefix, `${glyph} `);
        assert.equal(rows[0].text, `${label} read (path: README.md)`);
        assert.equal(rows[0].text.includes("(running)"), false);
        if (["failed", "error", "denied"].includes(status)) {
            assert.equal(rows[0].textColor, TUI_THEME.message.errorAccent);
        }
    });
}
test("CLI summarizes multiline input in its title while retaining the complete parameter", async () => {
    const code = Array.from({ length: 10 }, (_, index) => `print(${index})`).join("\n");
    const block = tool("completed", { name: "Python", parameters: { code } });
    const rows = await render(block, 100, "multiline-parameter-summary");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].text, "Completed Python (code: print(0)…)");
    assert.equal(block.toolCall?.parameters.code, code);
    assert.doesNotMatch(rows[0].text, /Input|print\(1\)|more lines|hidden observation body/);
});
test("CLI caps a long parameter summary at sixty characters without a separate input body", async () => {
    const command = `python ${"very_long_script_name_".repeat(8)}.py\nsecond-command`;
    const block = tool("running", { name: "shell", parameters: { command } });
    // Keep enough width for the full header, so this assertion tests the
    // parameter summary budget independently of terminal line wrapping.
    const rows = await render(block, 160, "long-parameter-summary");
    const summary = rows[0].text.match(/\(command: (.*)\)$/)?.[1];
    assert.equal(rows.length, 1);
    assert.ok(summary?.startsWith("python "));
    assert.ok(summary?.endsWith("…"));
    assert.ok(summary && summary.length <= 60);
    assert.doesNotMatch(rows[0].text, /second-command|Input|more lines/);
    assert.equal(block.toolCall?.parameters.command, command);
});
test("CLI shows only the first nonempty parameter and retains other parameter values", async () => {
    const parameters = { empty: "  ", command: "pytest", workdir: "/workspace/project", background: "false" };
    const block = tool("running", { name: "shell", parameters });
    const before = structuredClone(parameters);
    const rows = await render(block, 100, "multiple-parameter-summary");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].text, "Running shell (command: pytest)");
    assert.deepEqual(block.toolCall?.parameters, before);
});
test("CLI preserves complete errors after summarizing multiline tool input", async () => {
    const rows = await render(tool("failed", {
        name: "shell",
        parameters: { command: Array.from({ length: 10 }, (_, index) => `command-${index}`).join("\n") },
        error: Array.from({ length: 9 }, (_, index) => `failure-${index}`).join("\n")
    }), 100, "failed-input");
    const text = rows.map((row) => row.text).join("\n");
    assert.match(text, /Failed shell/);
    assert.match(text, /Error: failure-0/);
    assert.match(text, /failure-8/);
    assert.equal(rows[0].text, "Failed shell (command: command-0…)");
    assert.doesNotMatch(text, /command-1|Input|more lines/);
    assert.equal(rows.find((row) => row.text.includes("failure-8"))?.dim, false);
});
test("CLI wraps lifecycle summaries and errors to narrow Unicode terminal widths", async () => {
    const stringWidth = (await import(requireCli.resolve("string-width"))).default;
    const block = tool("denied", {
        name: "读取文件工具",
        parameters: { path: "目录/很长的文件名/报告.md\n第二行 👨‍👩‍👧‍👦 🔎\tmore" },
        error: "权限不足：不能读取这个文件。\nPlease request access before retrying 🔒"
    });
    for (const columns of [5, 8, 12, 32, 80]) {
        const rows = await render(block, columns, `unicode:${columns}`);
        for (const row of rows) {
            assert.ok(stringWidth(`${row.prefix}${row.text}`) <= columns - 2, `row exceeds ${columns} terminal columns: ${row.prefix}${row.text}`);
        }
    }
});
test("CLI refreshes a lifecycle row when the same call ID receives a new state", async () => {
    const running = tool("running");
    const initialRows = await render(running, 100, running.id!);
    const failed = tool("failed", { error: "permission denied" });
    const terminalRows = await render(failed, 100, failed.id!);
    assert.equal(initialRows[0].text, "Running read (path: README.md)");
    assert.equal(terminalRows[0].text, "Failed read (path: README.md)");
    assert.ok(terminalRows.some((row) => row.text === "Error: permission denied"));
    assert.notEqual(initialRows, terminalRows);
});
test("CLI shows tool-returned error text using the tool status instead of guessing failure", async () => {
    const rows = await render(tool("completed", {
        name: "shell", parameters: { command: "sample-command" },
        observation: "Error: command reported a domain failure"
    }), 100, "returned-domain-error");
    assert.equal(rows[0].text, "Completed shell (command: sample-command)");
    assert.equal(rows[0].prefix, "✓ ");
    assert.ok(rows.some((row) => row.text === "Error: command reported a domain failure"));
});
test("CLI folds successful results without dropping them from the tool record", async () => {
    const observation = Array.from({ length: 10 }, (_, index) => `result-${index}`).join("\n");
    const block = tool("completed", { observation });
    const rows = await render(block, 100, "result-preview");
    const text = rows.map((row) => row.text).join("\n");
    assert.match(text, /result-5/);
    assert.doesNotMatch(text, /result-6/);
    assert.match(text, /… \+4 more lines/);
    assert.equal(block.toolCall?.observation, observation);
});
test("CLI shows all failed result details beside a distinct caught exception", async () => {
    const observation = Array.from({ length: 9 }, (_, index) => `failure-output-${index}`).join("\n");
    const rows = await render(tool("failed", {
        observation, error: "RuntimeError: execution stopped"
    }), 100, "failure-result-and-exception");
    const text = rows.map((row) => row.text).join("\n");
    assert.match(text, /failure-output-8/);
    assert.match(text, /RuntimeError: execution stopped/);
    assert.equal(rows.find((row) => row.text.includes("failure-output-8"))?.dim, false);
});
test("CLI does not repeat an exception already present as the complete tool result", async () => {
    const rows = await render(tool("failed", {
        observation: "PermissionError: private.md", error: "PermissionError: private.md"
    }), 100, "identical-error-result");
    assert.equal(rows.filter((row) => row.text.includes("PermissionError: private.md")).length, 1);
});
test("CLI preserves a CodeAct traceback without repeating its embedded exception", async () => {
    const error = "PermissionError: private.md";
    const observation = `[logs]\nreading private.md\n[error]\nTraceback (most recent call last):\n${error}`;
    const rows = await render(tool("failed", {
        name: "Python", observation, error,
        parameters: { code: 'read(path="private.md")' }
    }), 100, "python-traceback-result");
    const text = rows.map((row) => row.text).join("\n");
    assert.match(text, /Traceback \(most recent call last\):/);
    assert.match(text, /reading private.md/);
    assert.equal(text.split(error).length, 2);
});
// Browser dict/JSON observations escape page newlines. To the terminal they
// are one long logical line, which still needs a limit after visual wrapping.
for (const [name, observation] of [
    ["browser_get_html", `{'html': '${"<main>报告内容</main>\\n".repeat(700)}END_OF_PAGE', 'truncated': False}`],
    ["browser_get_text", JSON.stringify({ text: `${"Page content: 数据与报告\n".repeat(700)}END_OF_PAGE`, truncated: false })],
]) {
    test(`CLI bounds ${name} results by visual rows in live and restored history`, async () => {
        assert.equal(observation.split("\n").length, 1, "the fixture must reproduce an escaped single-line browser result");
        assert.ok(observation.includes("\\n"));
        const callId = `${name}-call`;
        const args = { selector: "body" };
        const start: RunnerStreamEvent = {
            timestamp: 1,
            kind: "tool_start",
            event_id: `${name}-start`,
            permission_mode: "default", agent_mode: "agent", runner_id: "runner-1",
            agent_id: "root-id", agent_name: "root", agent_role: "root", round_id: "round-1",
            step_num: 1,
            data: { call_id: callId, tool_name: name, tool_args: args }
        };
        const end: RunnerStreamEvent = {
            ...start,
            kind: "tool_end",
            event_id: `${name}-end`,
            data: { ...start.data, status: "completed", observation, error: null }
        };
        const state = [start, end].reduce((previous, event) => conversationReducer(previous, {
            type: "ambient_event", event, blocks: presentStreamEvent(event)
        }), createConversationState());
        const actor: ActiveAgentSnapshot = {
            agent_id: "root-id", agent_name: "root", agent_kind: "root", agent_role: "root",
            status: "idle", is_root: true, current_async_task_id: "", metadata: {}, session: {},
            steps: [{
                    type: "action", step_num: 1, model_output: "", thought: "", code_action: "", reasoning_content: "",
                    tool_calls: [{ name, args, call_id: callId, mode: "foreground", status: "completed", observation_index: 0, is_terminal: false }],
                    observations: [observation], observation_images: [], attachments: [], error: null, round_outcome: "continue", output: null
                }]
        };
        const liveTools = state.messages.filter((block) => block.kind === "tool");
        const savedTools = presentActiveAgent(actor).filter((block) => block.kind === "tool");
        assert.equal(liveTools.length, 1);
        assert.equal(savedTools.length, 1);
        for (const columns of [40, 80, 120]) {
            // The existing full-detail path provides the number of visually wrapped
            // rows. This checks the omission count without duplicating the terminal's
            // Unicode-width and prefix layout algorithm inside the test.
            const full = await render(tool("failed", { name, parameters: args, observation }), columns, `${name}:full:${columns}`);
            const fullResultCount = full.filter((row) => row.key.includes(":result:")).length;
            assert.ok(fullResultCount > 6);
            const projections: string[][] = [];
            for (const [source, block] of [["live", liveTools[0]], ["saved", savedTools[0]]] as const) {
                const rows = await render(block, columns, `${name}:${source}:${columns}`);
                const resultRows = rows.filter((row) => row.key.includes(":result:"));
                const moreRows = rows.filter((row) => row.key.includes(":result-more:"));
                assert.equal(resultRows.length, 6, `${source} ${name} at ${columns} columns must show six visual result rows`);
                assert.equal(moreRows.map((row) => row.text).join(""), `… +${fullResultCount - 6} more lines`);
                assert.doesNotMatch(resultRows.map((row) => row.text).join(""), /END_OF_PAGE/);
                assert.equal(block.toolCall?.observation, observation, "display folding must retain the complete tool result");
                projections.push(rows.map((row) => `${row.prefix}${row.text}`));
            }
            assert.deepEqual(projections[0], projections[1], "live and saved browser tools must have the same compact presentation");
        }
        assert.equal(end.data?.observation, observation);
    });
}
