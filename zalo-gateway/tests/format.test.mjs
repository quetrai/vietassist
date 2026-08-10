import test from "node:test";
import assert from "node:assert/strict";
import {markdownToZalo, splitFormattedMessage} from "../dist/format.js";

test("bold, star italic and underscore italic are styled", () => {
  const result = markdownToZalo("**đậm** và *nghiêng* và _nghiêng_ và foreign_net_vol");
  assert.equal(result.text, "đậm và nghiêng và nghiêng và foreign_net_vol");
  assert.deepEqual(result.styles, [
    {start: 0, len: 3, st: "b"},
    {start: 7, len: 7, st: "i"},
    {start: 18, len: 7, st: "i"},
  ]);
});

test("links are parsed before underscores in URLs", () => {
  const result = markdownToZalo("[xem](https://example.com/a_b_c)");
  assert.equal(result.text, "xem (https://example.com/a_b_c)");
  assert.deepEqual(result.styles, []);
});

test("bullets and code markers are normalized", () => {
  const result = markdownToZalo("- mục 1\n* mục 2\ndùng `auto_close`");
  assert.equal(result.text, "• mục 1\n• mục 2\ndùng auto_close");
  assert.deepEqual(result.styles, []);
});

test("long formatted messages split after formatting and preserve style ranges", () => {
  const result = markdownToZalo("**" + "a".repeat(100) + "**\n\n" + "b".repeat(100));
  const chunks = splitFormattedMessage(result, 110);
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].text, "a".repeat(100));
  assert.deepEqual(chunks[0].styles, [{start: 0, len: 100, st: "b"}]);
  assert.equal(chunks[1].text, "b".repeat(100));
  assert.deepEqual(chunks[1].styles, []);
});

test("a style spanning a split is clipped and rebased", () => {
  const result = markdownToZalo("**" + "a".repeat(200) + "**");
  const chunks = splitFormattedMessage(result, 100);
  assert.equal(chunks.length, 2);
  assert.deepEqual(chunks.map((chunk) => chunk.styles), [
    [{start: 0, len: 100, st: "b"}],
    [{start: 0, len: 100, st: "b"}],
  ]);
});

test("split never breaks a surrogate pair", () => {
  const result = markdownToZalo("a".repeat(9) + "😀" + "b".repeat(9));
  const chunks = splitFormattedMessage(result, 10);
  assert.equal(chunks.map((chunk) => chunk.text).join(""), result.text);
  for (const chunk of chunks) {
    assert.ok(!chunk.text.includes("\ud83d") || chunk.text.includes("😀"));
    assert.ok(!chunk.text.includes("\ude00") || chunk.text.includes("😀"));
  }
});
