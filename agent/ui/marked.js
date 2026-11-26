/* Lightweight offline Marked.js-compatible Markdown parser */
(function (global) {
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function parseInline(text) {
    let out = escapeHtml(text);
    // Inline code
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Bold + Italic
    out = out.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    // Links [label](url)
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return out;
  }

  function parseMarkdown(md) {
    if (!md) return "";
    const normalized = String(md).replace(/\r\n/g, "\n");
    const codeBlocks = [];

    // Extract fenced code blocks first
    const withPlaceholders = normalized.replace(/```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g, function (_, lang, code) {
      const idx = codeBlocks.length;
      const safeLang = escapeHtml((lang || "text").trim().toLowerCase());
      const safeCode = escapeHtml(code.replace(/\n$/, ""));
      codeBlocks.push({ lang: safeLang, code: safeCode });
      return `\n@@CODEBLOCK_${idx}@@\n`;
    });

    const lines = withPlaceholders.split("\n");
    const html = [];
    let inUl = false;
    let inOl = false;

    function closeLists() {
      if (inUl) { html.push("</ul>"); inUl = false; }
      if (inOl) { html.push("</ol>"); inOl = false; }
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();

      const cbMatch = trimmed.match(/^@@CODEBLOCK_(\d+)@@$/);
      if (cbMatch) {
        closeLists();
        const block = codeBlocks[Number(cbMatch[1])];
        html.push(
          `<div class="code-wrapper">` +
            `<div class="code-header">` +
              `<span>${block.lang || "code"}</span>` +
              `<button type="button" class="copy-btn" onclick="window.__copyCode(this)">Copy</button>` +
            `</div>` +
            `<pre><code class="language-${block.lang}">${block.code}</code></pre>` +
          `</div>`
        );
        continue;
      }

      if (!trimmed) {
        closeLists();
        continue;
      }

      // Headings
      const hMatch = trimmed.match(/^(#{1,3})\s+(.*)$/);
      if (hMatch) {
        closeLists();
        const level = hMatch[1].length;
        html.push(`<h${level}>${parseInline(hMatch[2])}</h${level}>`);
        continue;
      }

      // Blockquote
      if (trimmed.startsWith("> ")) {
        closeLists();
        html.push(`<blockquote>${parseInline(trimmed.slice(2))}</blockquote>`);
        continue;
      }

      // Unordered list
      const ulMatch = trimmed.match(/^[-*]\s+(.*)$/);
      if (ulMatch) {
        if (inOl) { html.push("</ol>"); inOl = false; }
        if (!inUl) { html.push("<ul>"); inUl = true; }
        html.push(`<li>${parseInline(ulMatch[1])}</li>`);
        continue;
      }

      // Ordered list
      const olMatch = trimmed.match(/^\d+\.\s+(.*)$/);
      if (olMatch) {
        if (inUl) { html.push("</ul>"); inUl = false; }
        if (!inOl) { html.push("<ol>"); inOl = true; }
        html.push(`<li>${parseInline(olMatch[1])}</li>`);
        continue;
      }

      closeLists();
      html.push(`<p>${parseInline(trimmed)}</p>`);
    }

    closeLists();
    return html.join("\n");
  }

  global.marked = { parse: parseMarkdown };
})(window);
