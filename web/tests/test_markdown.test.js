// Created by Codex on 2026/10/22.
// Copyright © 2026 CMIC. All rights reserved.
const assert = require("node:assert/strict");
const test = require("node:test");
const { renderSafeMarkdown, hasMarkdownTable } = require("../assets/markdown.js");

function mockNode(tag = "root", text = null) {
  return {
    tag, text, children: [], append(...children) { this.children.push(...children); },
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

test("renders GFM tables with header, alignment, inline breaks and safe cells", () => {
  const root = mockNode();
  renderSafeMarkdown(root, [
    "# 审计报告", "",
    "| 检查项 | 结论 | 风险 |",
    "|:--|:--:|--:|",
    "| 返回键 | 通过 | 低 |",
    "| 弱网 | 待补 | 中<br>需回归 |",
    "| 转义 | a \\| b | <img src=x onerror=alert(1)> |"
  ].join("\n"));
  const nodes = flatten(root);
  assert.ok(nodes.some(node => node.tag === "div" && node.className === "markdown-table-frame"));
  assert.ok(nodes.some(node => node.tag === "div" && node.className === "markdown-table-wrap"));
  assert.ok(nodes.some(node => node.tag === "table"));
  const headers = nodes.filter(node => node.tag === "th");
  assert.equal(headers.length, 3);
  assert.equal(headers[0].text ?? headers[0].children.map(child => child.text ?? "").join(""), "检查项");
  assert.equal(headers[1]["data-align"], "center");
  assert.equal(headers[2]["data-align"], "right");
  assert.equal(nodes.filter(node => node.tag === "tr").length, 4);
  assert.ok(nodes.some(node => node.tag === "br"));
  const text = nodes.filter(node => node.tag === "#text").map(node => node.text).join("");
  assert.ok(text.includes("a | b"));
  assert.ok(text.includes("<img src=x onerror=alert(1)>"));
  assert.equal(nodes.some(node => node.tag === "img"), false);
});

test("detects a table only outside fenced code and table-first parsing wins", () => {
  assert.equal(hasMarkdownTable("| A | B |\n| - | - |\n| 1 | 2 |"), true);
  assert.equal(hasMarkdownTable("```\n| A | B |\n| - | - |\n```"), false);
  assert.equal(hasMarkdownTable("普通段落\n\n没有表格"), false);
});
