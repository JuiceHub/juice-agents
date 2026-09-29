import test from "node:test";
import assert from "node:assert/strict";
import { linkifyImagePaths } from "./MarkdownContent.js";

test("linkifyImagePaths only links image paths outside fenced code", () => {
  const markdown = [
    "generated .juice/runners/run/observation_images/out.png and notes.txt",
    "```",
    "print('/tmp/hidden.png')",
    "```",
  ].join("\n");

  const linked = linkifyImagePaths(markdown);

  assert.match(linked, /\[\.juice\/runners\/run\/observation_images\/out\.png\]\(#juice-file:\.juice%2Frunners%2Frun%2Fobservation_images%2Fout\.png\)/);
  assert.match(linked, /notes\.txt/);
  assert.doesNotMatch(linked, /#juice-file:%2Ftmp%2Fhidden\.png/);
});
