(() => {
  if (globalThis.__lsContentLoaded) return;
  globalThis.__lsContentLoaded = true;

  const MAX_MESSAGES = 2000;

  function platformFor(host) {
    if (/chatgpt\.com|openai\.com/.test(host)) return "chatgpt";
    if (/deepseek\.com/.test(host)) return "deepseek";
    if (/qoder\.(com|cn)/.test(host)) return "qoder";
    return "other";
  }

  function textOf(node) {
    if (!node) return "";
    const clone = node.cloneNode(true);
    clone
      .querySelectorAll("button, svg, [role='button'], [aria-hidden='true'], .sr-only")
      .forEach((element) => element.remove());
    return String(clone.innerText || clone.textContent || "")
      .replace(/\r\n/g, "\n")
      .trim();
  }

  function titleOf(platform) {
    const selectors =
      platform === "chatgpt"
        ? ["[data-testid='conversation-title']", "h1"]
        : [
            "h1",
            "[class*='conversation-title']",
            "[class*='chat-title']",
            "[class*='title']",
          ];
    for (const selector of selectors) {
      const text = textOf(document.querySelector(selector));
      if (text && text.length <= 200) return text;
    }
    const docTitle = String(document.title || "")
      .replace(/\s*[-|–].*$/, "")
      .trim();
    if (docTitle && !/^(chatgpt|deepseek|qoder)$/i.test(docTitle)) return docTitle;
    return "";
  }

  function externalId(platform, title, messages) {
    const path = location.pathname;
    const match =
      path.match(/\/(?:c|chat|s)\/([A-Za-z0-9-]{6,})/) ||
      path.match(/\/([A-Za-z0-9-]{12,})$/);
    if (match) return `${platform}-${match[1]}`.slice(0, 200);
    const seed = `${platform}|${title}|${(messages[0] && messages[0].content) || ""}`;
    let hash = 5381;
    for (let i = 0; i < seed.length; i += 1) {
      hash = ((hash << 5) + hash + seed.charCodeAt(i)) >>> 0;
    }
    return `${platform}-ext-${hash.toString(16)}`;
  }

  function collectChatGPT() {
    return Array.from(document.querySelectorAll("[data-message-author-role]")).map(
      (node) => ({
        role: node.getAttribute("data-message-author-role"),
        content: textOf(node.querySelector(".markdown") || node),
      })
    );
  }

  function roleFromNode(node, index) {
    const explicit =
      node.getAttribute("data-role") ||
      node.getAttribute("data-message-author-role") ||
      node.getAttribute("data-author");
    if (explicit) return explicit;
    const marker = `${node.className || ""} ${
      node.getAttribute("data-testid") || ""
    }`.toLowerCase();
    if (/(^|\b)(user|human|question|query)(\b|$)/.test(marker)) return "user";
    if (/(^|\b)(assistant|ai|bot|answer|response)(\b|$)/.test(marker)) {
      return "assistant";
    }
    return index % 2 === 0 ? "user" : "assistant";
  }

  function collectGeneric() {
    const selectors = [
      "[data-role]",
      "[data-message-author-role]",
      ".ds-message",
      "[class*='chat-message']",
      "[class*='message-item']",
      "[class*='message-row']",
    ];
    let nodes = [];
    for (const selector of selectors) {
      const found = Array.from(document.querySelectorAll(selector));
      if (found.length > nodes.length) nodes = found;
    }
    const seen = new Set();
    return nodes
      .filter((node) => {
        if (seen.has(node)) return false;
        seen.add(node);
        return true;
      })
      .map((node, index) => ({
        role: roleFromNode(node, index),
        content: textOf(
          node.querySelector(
            ".ds-markdown, [class*='markdown'], [class*='content']"
          ) || node
        ),
      }));
  }

  function cleanMessages(raw) {
    const out = [];
    for (const item of raw) {
      const role = String(item.role || "").toLowerCase();
      if (!["user", "assistant", "system"].includes(role)) continue;
      const content = String(item.content || "").trim();
      if (!content) continue;
      const last = out[out.length - 1];
      if (last && last.role === role && last.content === content) continue;
      out.push({ role, content });
      if (out.length >= MAX_MESSAGES) break;
    }
    return out;
  }

  function extract() {
    const platform = platformFor(location.hostname);
    const raw = platform === "chatgpt" ? collectChatGPT() : collectGeneric();
    const messages = cleanMessages(raw);
    if (!messages.length) {
      return {
        ok: false,
        platform,
        error:
          "没有识别到消息。请确认已经打开一条具体会话；如果页面结构刚更新，请改用平台的导出文件导入。",
      };
    }
    const fallbackTitle = messages[0].content.replace(/\s+/g, " ").slice(0, 60);
    const title = (titleOf(platform) || fallbackTitle || "未命名会话").slice(
      0,
      255
    );
    return {
      ok: true,
      platform,
      title,
      external_id: externalId(platform, title, messages),
      url: location.href,
      messages,
    };
  }

  globalThis.__lsExtract = extract;

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.type !== "LS_EXTRACT") return;
    try {
      sendResponse(extract());
    } catch (error) {
      sendResponse({
        ok: false,
        error: String((error && error.message) || error),
      });
    }
  });
})();
