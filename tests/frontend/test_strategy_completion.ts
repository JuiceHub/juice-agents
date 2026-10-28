import assert from "node:assert/strict";
import test from "node:test";

import {
  acceptCommandCompletion,
  buildGhostText,
  buildInlineHint,
  getCommandCandidates,
  type CandidateItem,
} from "../../frontend/cli/src/hooks/useCompletion.js";
import { executeCommand } from "../../frontend/cli/src/lib/commands.js";

function candidateFor(input: string, label: string, cursor = input.length): CandidateItem {
  const candidate = getCommandCandidates(input, [], [], cursor).find((item) => item.label === label);
  assert.ok(candidate, `Expected ${label} completion for ${JSON.stringify(input)}`);
  return candidate;
}

test("slash list and command prefix expose strategy", () => {
  assert.ok(getCommandCandidates("/").some((item) => item.label === "/strategy"));
  assert.deepEqual(getCommandCandidates("/str").map((item) => item.label), ["/strategy"]);
});

test("accepting strategy opens concrete valid parameter candidates", () => {
  const command = acceptCommandCompletion({
    input: "/str", candidate: candidateFor("/str", "/strategy"),
  });
  assert.deepEqual(command, { input: "/strategy ", cursor: 10 });
  assert.deepEqual(getCommandCandidates(command.input).map((item) => item.label), ["breadth=3", "reset"]);
  // A bare breadth= placeholder plus the inserted space would fail command
  // validation. Every offered argument must be immediately executable.
  for (const { label } of getCommandCandidates(command.input)) {
    assert.ok(label === "reset" || /^breadth=[1-9]\d*$/.test(label));
  }
});

test("strategy parameter prefixes narrow to supported arguments", () => {
  assert.deepEqual(getCommandCandidates("/strategy b").map((item) => item.label), ["breadth=3"]);
  assert.deepEqual(getCommandCandidates("/strategy breadth=").map((item) => item.label), ["breadth=3"]);
  assert.deepEqual(getCommandCandidates("/strategy r").map((item) => item.label), ["reset"]);
});

test("complete strategy arguments close completion with or without trailing whitespace", () => {
  for (const argument of ["breadth=1", "breadth=3", "breadth=12", "reset"]) {
    assert.deepEqual(getCommandCandidates(`/strategy ${argument}`), []);
    assert.deepEqual(getCommandCandidates(`/strategy ${argument} `), []);
  }
});

test("strategy never suggests removed parameters or breadth lists", () => {
  for (const argument of ["d", "depth=", "beam", "beam_width=", "temperature=", "breadth=["]) {
    assert.deepEqual(getCommandCandidates(`/strategy ${argument}`), []);
  }
  assert.deepEqual(getCommandCandidates("/strategy breadth=3 r"), []);
});

test("strategy inline hint appears only while awaiting its first argument", () => {
  const input = "/strategy ";
  assert.equal(buildInlineHint({ input, candidates: getCommandCandidates(input) }), "<breadth=N|reset>");
  for (const draft of ["/strategy", "/strategy b", "/strategy breadth=3 ", "/strategy reset "]) {
    assert.equal(buildInlineHint({ input: draft, candidates: getCommandCandidates(draft) }), null);
  }
  assert.equal(buildInlineHint({ input, cursor: 5, candidates: getCommandCandidates(input) }), null);
});

test("strategy command and argument ghost text match accepted completion", () => {
  for (const [input, label, suffix] of [
    ["/str", "/strategy", "ategy"],
    ["/strategy b", "breadth=3", "readth=3"],
    ["/strategy r", "reset", "eset"],
  ]) {
    const candidate = candidateFor(input, label);
    const candidates = [candidate];
    assert.equal(buildGhostText({ input, candidates, selectedIndex: 0, visible: true }), suffix);
    assert.equal(buildGhostText({ input, candidates, selectedIndex: 0, visible: false }), null);
    const accepted = acceptCommandCompletion({ input, candidate });
    assert.equal(accepted.input, `${input}${suffix} `);
    assert.equal(accepted.cursor, accepted.input.length);
  }
});

test("strategy completion preserves multiline draft text around the cursor", () => {
  const input = "draft\n/str\nkeep this";
  const cursor = input.indexOf("\n", input.indexOf("/str"));
  const candidate = candidateFor(input, "/strategy", cursor);
  assert.equal(buildGhostText({ input, cursor, candidates: [candidate], selectedIndex: 0, visible: true }), "ategy");
  const accepted = acceptCommandCompletion({ input, cursor, candidate });
  assert.deepEqual(accepted, { input: "draft\n/strategy\nkeep this", cursor: "draft\n/strategy".length });
});

test("argument completion replaces the whole cursor token and preserves its following line", () => {
  const input = "/strategy breX\nkeep this";
  const cursor = input.indexOf("X");
  const candidate = candidateFor(input, "breadth=3", cursor);
  // A cursor inside an unfinished token must not show a suffix at its end.
  assert.equal(buildGhostText({ input, cursor, candidates: [candidate], selectedIndex: 0, visible: true }), null);
  const accepted = acceptCommandCompletion({ input, cursor, candidate });
  assert.deepEqual(accepted, { input: "/strategy breadth=3\nkeep this", cursor: "/strategy breadth=3".length });
});

for (const [prefix, argument, expected] of [
  ["b", "breadth=3", { breadth: 3 }],
  ["r", "reset", { reset: true }],
] as const) {
  test(`accepted ${argument} completion issues only valid supported strategy RPC`, async () => {
    const calls: unknown[] = [];
    const client: any = {
      switchStrategy: async (params: { breadth?: number; reset?: boolean }) => {
        calls.push(params);
        return {
          status: "pending",
          strategy: {
            type: params.reset ? "greedy" : "exploration",
            params: params.reset ? {} : { breadth: params.breadth },
            execution: "linear",
            scorer_configured: false,
          },
          message: "configured",
        };
      },
    };
    const input = `/strategy ${prefix}`;
    const accepted = acceptCommandCompletion({ input, candidate: candidateFor(input, argument) });
    const result = await executeCommand(accepted.input, client, "/tmp/workspace");
    assert.equal(result.blocks[0].kind, "system");
    assert.deepEqual(calls, [{ base_dir: "/tmp/workspace", ...expected }]);
    assert.deepEqual(getCommandCandidates(accepted.input), []);
  });
}
