// Created by Codex on 2026/10/22.
// Copyright © 2026 CMIC. All rights reserved.
const assert = require("node:assert/strict");
const test = require("node:test");
const { renderSafeMarkdown } = require("../assets/markdown.js");

function mockNode(tag = "root", text = null) {
  return {
    tag, text, children: [], append(child) { this.children.push(child); },
    setAttribute(name, value) { this[name] = value; }
  };
}

global.document = {
  createTextNode(value) { return mockNode("#text", String(value)); },
  createElement(tag) { return mockNode(tag); }
};

function flatten(node) { return [node, ...node.children.flatMap(flatten)]; }

test("renders Markdown structure while keeping raw HTML inert and links safe", () => {
  const root = mockNode();
  renderSafeMarkdown(root, [
    "# Context", "", "A **bold** and *emphasized* phrase with `code`.",
    "[safe](https://example.com/path) [unsafe](javascript:alert(1))",
    "<img src=x onerror=alert(1)>", "", "- first", "- second", "", "```html", "<script>alert(1)</script>", "```"
  ].join("\n"));
  const nodes = flatten(root);
  const tags = nodes.map(node => node.tag);
  assert.ok(tags.includes("h1"));
  assert.ok(tags.includes("strong"));
  assert.ok(tags.includes("em"));
  assert.ok(tags.includes("ul"));
  assert.ok(tags.includes("pre"));
  assert.equal(tags.includes("script"), false);
  assert.equal(tags.includes("img"), false);
  const anchors = nodes.filter(node => node.tag === "a");
  assert.equal(anchors.length, 1);
  assert.equal(anchors[0].href, "https://example.com/path");
  assert.equal(anchors[0].rel, "noopener noreferrer");
  assert.ok(nodes.some(node => node.tag === "#text" && node.text.includes("<img src=x onerror=alert(1)>")));
  assert.ok(nodes.some(node => node.tag === "#text" && node.text.includes("<script>alert(1)</script>")));
  assert.ok(nodes.some(node => node.tag === "#text" && node.text === "unsafe"));
});
