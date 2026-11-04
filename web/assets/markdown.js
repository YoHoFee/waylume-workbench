// Created by Codex on 2026/10/22.
// Copyright © 2026 CMIC. All rights reserved.
(() => {
  const SAFE_TAGS = new Set(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "pre", "code", "strong", "em", "a", "br"]);

  function safeHref(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }

  function inline(source) {
    const nodes = [];
    const pattern = /\*\*([^*]+)\*\*|__([^_]+)__|\*([^*]+)\*|_([^_]+)_|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;
    let cursor = 0, match;
    while ((match = pattern.exec(source))) {
      if (match.index > cursor) nodes.push(source.slice(cursor, match.index));
      let node = null;
      if (match[1] || match[2]) node = { tag: "strong", children: inline(match[1] || match[2]) };
      else if (match[3] || match[4]) node = { tag: "em", children: inline(match[3] || match[4]) };
      else if (match[5]) node = { tag: "code", children: [match[5]] };
      else if (match[6]) {
        const href = safeHref(match[7]);
        node = href ? { tag: "a", attrs: { href, target: "_blank", rel: "noopener noreferrer" }, children: inline(match[6]) } : match[6];
      }
      nodes.push(node);
      cursor = pattern.lastIndex;
    }
    if (cursor < source.length) nodes.push(source.slice(cursor));
    return nodes;
  }

  function parseSafeMarkdown(markdown) {
    const lines = String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [], index = 0;
    const flushParagraph = () => {
      if (!paragraph.length) return;
      const children = [];
      paragraph.forEach((line, lineIndex) => { if (lineIndex) children.push({ tag: "br", children: [] }); children.push(...inline(line)); });
      blocks.push({ tag: "p", children }); paragraph = [];
    };
    while (index < lines.length) {
      const line = lines[index];
      if (/^\s*```/.test(line)) {
        flushParagraph(); index += 1; const codeLines = [];
        while (index < lines.length && !/^\s*```/.test(lines[index])) codeLines.push(lines[index++]);
        if (index < lines.length) index += 1;
        blocks.push({ tag: "pre", children: [{ tag: "code", children: [codeLines.join("\n")] }] });
        continue;
      }
      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) { flushParagraph(); blocks.push({ tag: `h${heading[1].length}`, children: inline(heading[2]) }); index += 1; continue; }
      const list = line.match(/^\s{0,3}([-*+]\s+|\d+[.)]\s+)(.*)$/);
      if (list) {
        flushParagraph(); const ordered = /^\d/.test(list[1]), tag = ordered ? "ol" : "ul", items = [];
        while (index < lines.length) {
          const item = lines[index].match(/^\s{0,3}([-*+]\s+|\d+[.)]\s+)(.*)$/);
          if (!item || /^\d/.test(item[1]) !== ordered) break;
          items.push({ tag: "li", children: inline(item[2]) }); index += 1;
        }
        blocks.push({ tag, children: items }); continue;
      }
      if (!line.trim()) { flushParagraph(); index += 1; continue; }
      paragraph.push(line); index += 1;
    }
    flushParagraph();
    return blocks;
  }

  function renderSafeMarkdown(parent, markdown) {
    for (const node of parseSafeMarkdown(markdown)) appendNode(parent, node);
  }
  function appendNode(parent, node) {
    if (typeof node === "string") { parent.append(document.createTextNode(node)); return; }
    if (!SAFE_TAGS.has(node.tag)) { parent.append(document.createTextNode(node.children.map(String).join(""))); return; }
    const element = document.createElement(node.tag);
    if (node.tag === "a" && node.attrs?.href && safeHref(node.attrs.href)) {
      element.href = node.attrs.href; element.target = "_blank"; element.rel = "noopener noreferrer";
    }
    for (const child of node.children || []) appendNode(element, child);
    parent.append(element);
  }

  if (typeof window !== "undefined") window.renderSafeMarkdown = renderSafeMarkdown;
  if (typeof module !== "undefined" && module.exports) module.exports = { parseSafeMarkdown, renderSafeMarkdown };
})();
