// Created by Codex on 2026/10/22.
// Copyright © 2026 CMIC. All rights reserved.
(() => {
  const SAFE_TAGS = new Set(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "pre", "code", "strong", "em", "a", "br",
    "table", "thead", "tbody", "tr", "th", "td"]);

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

  // GFM 表格单元格里允许用 <br> 手工换行；其余原始 HTML 仍按纯文本处理。
  function inlineCell(source) {
    const nodes = [];
    String(source).split(/<br\s*\/?>/i).forEach((part, index) => {
      if (index) nodes.push({ tag: "br", children: [] });
      nodes.push(...inline(part));
    });
    return nodes;
  }

  function splitTableRow(line) {
    let text = String(line).trim();
    if (text.startsWith("|")) text = text.slice(1);
    if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
    const cells = [];
    let buffer = "", inCode = false;
    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if (char === "\\" && text[index + 1] === "|") { buffer += "|"; index += 1; continue; }
      if (char === "`") { inCode = !inCode; buffer += char; continue; }
      if (char === "|" && !inCode) { cells.push(buffer.trim()); buffer = ""; continue; }
      buffer += char;
    }
    cells.push(buffer.trim());
    return cells;
  }

  function tableAlignment(line) {
    if (!String(line).includes("|")) return null;
    const cells = splitTableRow(line);
    if (!cells.length) return null;
    const alignments = [];
    for (const cell of cells) {
      const value = cell.trim();
      if (!/^:?-+:?$/.test(value)) return null;
      alignments.push(value.startsWith(":") && value.endsWith(":") ? "center" : value.endsWith(":") ? "right" : value.startsWith(":") ? "left" : "");
    }
    return alignments;
  }

  function tableAt(lines, index) {
    const line = lines[index];
    if (!line || !line.includes("|") || index + 1 >= lines.length) return null;
    const alignments = tableAlignment(lines[index + 1]);
    if (!alignments) return null;
    const header = splitTableRow(line);
    if (!header.length || header.length !== alignments.length) return null;
    return { header, alignments };
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
      // 表格优先：表头 + 分隔行同时成立时，先按 GFM 表格消费，避免被当段落。
      const table = tableAt(lines, index);
      if (table) {
        flushParagraph();
        index += 2;
        const rows = [];
        while (index < lines.length) {
          const row = lines[index];
          if (!row.trim() || !row.includes("|")) break;
          if (/^\s*```/.test(row) || /^\s{0,3}#{1,6}\s+/.test(row) || /^\s{0,3}([-*+]\s+|\d+[.)]\s+)/.test(row)) break;
          rows.push(splitTableRow(row));
          index += 1;
        }
        blocks.push({ tag: "table", header: table.header, alignments: table.alignments, rows });
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

  function appendTable(parent, node) {
    const frame = document.createElement("div"); frame.className = "markdown-table-frame";
    const wrap = document.createElement("div"); wrap.className = "markdown-table-wrap";
    const table = document.createElement("table");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    node.header.forEach((cell, index) => {
      const th = document.createElement("th");
      if (node.alignments[index]) th.setAttribute("data-align", node.alignments[index]);
      for (const child of inlineCell(cell)) appendNode(th, child);
      headRow.append(th);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    for (const row of node.rows) {
      const tr = document.createElement("tr");
      node.header.forEach((_, index) => {
        const td = document.createElement("td");
        if (node.alignments[index]) td.setAttribute("data-align", node.alignments[index]);
        for (const child of inlineCell(row[index] ?? "")) appendNode(td, child);
        tr.append(td);
      });
      body.append(tr);
    }
    table.append(head, body);
    wrap.append(table);
    frame.append(wrap);
    parent.append(frame);
  }

  function appendNode(parent, node) {
    if (typeof node === "string") { parent.append(document.createTextNode(node)); return; }
    if (node.tag === "table") { appendTable(parent, node); return; }
    if (!SAFE_TAGS.has(node.tag)) { parent.append(document.createTextNode(node.children.map(String).join(""))); return; }
    const element = document.createElement(node.tag);
    if (node.tag === "a" && node.attrs?.href && safeHref(node.attrs.href)) {
      element.href = node.attrs.href; element.target = "_blank"; element.rel = "noopener noreferrer";
    }
    for (const child of node.children || []) appendNode(element, child);
    parent.append(element);
  }

  function renderSafeMarkdown(parent, markdown) {
    for (const node of parseSafeMarkdown(markdown)) appendNode(parent, node);
  }

  function hasMarkdownTable(markdown) {
    const lines = String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n");
    let fenced = false;
    for (let index = 0; index < lines.length; index += 1) {
      if (/^\s*```/.test(lines[index])) { fenced = !fenced; continue; }
      if (fenced) continue;
      if (tableAt(lines, index)) return true;
    }
    return false;
  }

  if (typeof window !== "undefined") {
    window.renderSafeMarkdown = renderSafeMarkdown;
    window.markdownHasTable = hasMarkdownTable;
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { parseSafeMarkdown, renderSafeMarkdown, hasMarkdownTable };
})();
