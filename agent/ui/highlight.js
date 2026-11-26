/* Lightweight offline Highlight.js-compatible syntax highlighter */
(function (global) {
  const KEYWORDS = new Set([
    "def", "class", "return", "import", "from", "as", "if", "elif", "else", "for", "while",
    "try", "except", "finally", "with", "raise", "pass", "break", "continue", "in", "is",
    "not", "and", "or", "True", "False", "None", "async", "await", "yield", "lambda",
    "const", "let", "var", "function", "export", "default", "new", "this", "typeof",
    "null", "undefined", "true", "false", "switch", "case", "throw", "catch"
  ]);

  const tokenRegex = /(#[^\n]*|\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(\d+(?:\.\d+)?)\b|\b([A-Za-z_][A-Za-z0-9_]*)\b/g;

  function highlightCodeText(escapedHtml) {
    // Decode entities temporarily so regex matches cleanly, then re-escape per token
    const raw = escapedHtml
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&amp;/g, "&");

    function esc(s) {
      return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    let result = "";
    let lastIndex = 0;
    let match;
    tokenRegex.lastIndex = 0;

    while ((match = tokenRegex.exec(raw)) !== null) {
      if (match.index > lastIndex) {
        result += esc(raw.slice(lastIndex, match.index));
      }
      const [full, comment, str, num, ident] = match;
      if (comment !== undefined) {
        result += `<span class="hljs-comment">${esc(comment)}</span>`;
      } else if (str !== undefined) {
        result += `<span class="hljs-string">${esc(str)}</span>`;
      } else if (num !== undefined) {
        result += `<span class="hljs-number">${esc(num)}</span>`;
      } else if (ident !== undefined) {
        if (KEYWORDS.has(ident)) {
          result += `<span class="hljs-keyword">${esc(ident)}</span>`;
        } else {
          result += esc(ident);
        }
      } else {
        result += esc(full);
      }
      lastIndex = tokenRegex.lastIndex;
    }

    if (lastIndex < raw.length) {
      result += esc(raw.slice(lastIndex));
    }
    return result;
  }

  function highlightElement(el) {
    if (!el || el.dataset.highlighted === "yes") return;
    el.innerHTML = highlightCodeText(el.innerHTML);
    el.dataset.highlighted = "yes";
  }

  function highlightAllUnder(container) {
    if (!container) return;
    container.querySelectorAll("pre code").forEach(highlightElement);
  }

  global.hljs = { highlightElement, highlightAllUnder };
})(window);
