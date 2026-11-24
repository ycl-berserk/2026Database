const PRESETS = {
  llm: {
    deepseek: { base: "https://api.deepseek.com/v1", model: "deepseek-chat" },
    openai: { base: "https://api.openai.com/v1", model: "gpt-4o-mini" },
    ollama: { base: "http://127.0.0.1:11434/v1", model: "qwen2.5:7b" },
  },
  embedding: {
    openai: { base: "https://api.openai.com/v1", model: "text-embedding-3-small" },
    ollama: { base: "http://127.0.0.1:11434/v1", model: "nomic-embed-text" },
  },
};

const form = document.querySelector("[data-ai-form]");

document.querySelectorAll("[data-preset]").forEach((select) => {
  select.addEventListener("change", () => {
    const kind = select.dataset.preset;
    const preset = PRESETS[kind] && PRESETS[kind][select.value];
    if (!preset) return;
    const section = select.closest("section");
    const base = section.querySelector(`[name="${kind}_base_url"]`);
    const model = section.querySelector(`[name="${kind}_model"]`);
    if (base) base.value = preset.base;
    if (model) model.value = preset.model;
  });
});

document.querySelectorAll("[data-test]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!form) return;
    const target = button.dataset.test;
    const status = document.querySelector(`[data-test-status="${target}"]`);
    const data = new FormData(form);
    data.set("target", target);
    status.textContent = "测试中…";
    status.dataset.ok = "";
    try {
      const response = await fetch("/settings/ai/test", {
        method: "POST",
        body: data,
        headers: {
          "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
        },
      });
      const payload = await response.json();
      status.textContent = payload.message || "测试完成";
      status.dataset.ok = payload.ok ? "1" : "0";
    } catch (error) {
      status.textContent = "测试请求失败";
      status.dataset.ok = "0";
    }
  });
});
