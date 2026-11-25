const PROVIDERS = {
  deepseek: {
    llmBase: "https://api.deepseek.com/v1",
    llmModel: "deepseek-chat",
    embedding: false,
    shareKey: false,
  },
  openai: {
    llmBase: "https://api.openai.com/v1",
    llmModel: "gpt-4o-mini",
    embedding: true,
    embeddingBase: "https://api.openai.com/v1",
    embeddingModel: "text-embedding-3-small",
    embeddingDim: "1536",
    shareKey: true,
  },
  custom: {},
};

const form = document.querySelector("[data-ai-form]");
const embeddingConfigured = form?.dataset.embeddingConfigured === "1";

function field(name) {
  return form?.querySelector(`[name="${name}"]`);
}

function applyProvider(name) {
  const provider = PROVIDERS[name];
  if (!form || !provider) return;

  if (provider.llmBase) field("llm_base_url").value = provider.llmBase;
  if (provider.llmModel) field("llm_model").value = provider.llmModel;

  if (provider.embeddingBase) field("embedding_base_url").value = provider.embeddingBase;
  if (provider.embeddingModel) field("embedding_model").value = provider.embeddingModel;
  if (provider.embeddingDim) field("embedding_dim").value = provider.embeddingDim;

  const embeddingEnabled = field("embedding_enabled");
  if (embeddingEnabled) embeddingEnabled.checked = Boolean(provider.embedding);

  const shareKey = field("share_key");
  if (shareKey) {
    shareKey.checked = Boolean(provider.shareKey) && !embeddingConfigured;
  }
}

document.querySelectorAll("[data-provider]").forEach((radio) => {
  radio.addEventListener("change", () => {
    if (radio.checked) applyProvider(radio.value);
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
