import test from "node:test";
import assert from "node:assert/strict";
import {
  removePresetPairs,
  selectedHistoryRanges,
  validExampleDialogs,
} from "../src/utils/personaPresets.mjs";

test("deleting selected exchanges preserves segment boundaries and provenance", () => {
  const dialogs = ["u1", "a1", "u2", "a2", "", "opening", "u3", "a3"];
  const segments = [
    { id: "one", start_pair: 0, pair_count: 2, source_title: "conversation" },
    { id: "two", start_pair: 2, pair_count: 2, source_review_id: "review" },
  ];
  assert.deepEqual(removePresetPairs(dialogs, segments, [1, 3]), {
    begin_dialogs: ["u1", "a1", "", "opening"],
    segments: [
      { ...segments[0], pair_count: 1 },
      { ...segments[1], start_pair: 1, pair_count: 1 },
    ],
  });
  assert.equal(dialogs.length, 8);
  assert.equal(segments[1].start_pair, 2);
});

test("deleting whole segments removes empty metadata and reindexes later segments", () => {
  assert.deepEqual(
    removePresetPairs(
      ["u1", "a1", "u2", "a2"],
      [
        { id: "one", start_pair: 0, pair_count: 1 },
        { id: "two", start_pair: 1, pair_count: 1 },
      ],
      [0],
    ),
    {
      begin_dialogs: ["u2", "a2"],
      segments: [{ id: "two", start_pair: 0, pair_count: 1 }],
    },
  );
});

test("history ranges use native indexes and retain consecutive visible messages across tool entries", () => {
  const messages = [1, 3, 8, 10, 12].map((index) => ({ index }));
  assert.deepEqual(selectedHistoryRanges(messages, [12, 1, 3, 10]), [
    { start: 1, end: 3 },
    { start: 10, end: 12 },
  ]);
  assert.deepEqual(selectedHistoryRanges(messages, []), []);
});

test("assistant opening placeholders are valid but empty answers and incomplete exchanges are rejected", () => {
  assert.equal(validExampleDialogs(["", "主动开场", "回复", "接话"]), true);
  assert.equal(validExampleDialogs(["问题", "回答", "", "回答"]), false);
  assert.equal(validExampleDialogs(["问题", ""]), false);
  assert.equal(validExampleDialogs(["问题"]), false);
});
