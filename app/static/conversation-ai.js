(function () {
  "use strict";

  const button = document.querySelector("[data-ai-suggest]");
  if (!button) return;

  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const panel = document.querySelector("[data-ai-panel]");
  const status = document.querySelector("[data-ai-status]");
  const form = document.querySelector(".organize-form");
  const summary = form && form.querySelector('textarea[name="summary"]');
  const newTags = form && form.querySelector('input[name="new_tags"]');

  function say(message, isError) {
    if (status) status.textContent = message;
    if (panel) {
      panel.hidden = false;
      panel.dataset.state = isError ? "error" : "ok";
    }
  }

  function checkboxFor(name) {
    const target = name.trim().toLowerCase();
    return Array.from(form.querySelectorAll('input[name="tag_ids"]')).find(function (input) {
      return (input.dataset.tagName || "").trim().toLowerCase() === target;
    });
  }

  function mergeNewTag(name) {
    const current = (newTags.value || "").split(/[,，\n]+/).map(function (item) {
      return item.trim();
    }).filter(Boolean);
    if (!current.some(function (item) { return item.toLowerCase() === name.toLowerCase(); })) {
      current.push(name);
    }
    newTags.value = current.join("，");
  }

  button.addEventListener("click", async function () {
    const conversationId = button.dataset.aiSuggest;
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "正在生成…";
    say("正在阅读这段对话，请稍候…");
    try {
      const response = await fetch("/api/conversations/" + conversationId + "/suggest", {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-CSRF-Token": csrf },
      });
      const data = await response.json().catch(function () { return null; });
      if (!response.ok) {
        throw new Error((data && data.detail) || "AI 建议生成失败");
      }
      let filled = 0;
      (data.tags || []).forEach(function (tag) {
        const box = checkboxFor(tag.name);
        if (box) {
          box.checked = true;
        } else {
          mergeNewTag(tag.name);
        }
        filled += 1;
      });
      if (summary && data.summary) summary.value = data.summary;
      say(
        (filled ? "已填入 " + filled + " 个标签" : "未发现合适的标签") +
          (data.summary ? "，并生成摘要。" : "。") +
          "请检查后点击“保存归类与摘要”。"
      );
    } catch (error) {
      say("建议生成失败：" + error.message + "。可稍后重试，或手动填写标签与摘要。", true);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });
})();
