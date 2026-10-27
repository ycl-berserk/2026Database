const DEFAULTS = {
  baseUrl: "http://127.0.0.1:8000",
  token: "",
  accountLabel: "",
  courseCode: "",
  tags: "",
};

const PLATFORM_NAMES = {
  chatgpt: "ChatGPT",
  deepseek: "DeepSeek",
  qoder: "Qoder",
};

const $ = (id) => document.getElementById(id);
let extraction = null;
let busy = false;

function setStatus(message, kind = "info") {
  const node = $("status");
  node.textContent = message;
  node.dataset.kind = kind;
}

function setBadge(platform) {
  $("platform-badge").textContent = PLATFORM_NAMES[platform] || "未识别";
}

function splitTags(value) {
  return String(value || "")
    .split(/[,，、;；]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 20);
}

function collectSettings() {
  return {
    baseUrl: $("base-url").value.trim() || DEFAULTS.baseUrl,
    token: $("token").value.trim(),
    accountLabel: $("account-label").value.trim(),
    courseCode: $("course-code").value.trim(),
    tags: $("tags").value.trim(),
  };
}

function normalizeBaseUrl(value) {
  const url = new URL(value);
  if (!/^https?:$/.test(url.protocol)) {
    throw new Error("学习空间地址必须以 http:// 或 https:// 开头");
  }
  return `${url.origin}${url.pathname}`.replace(/\/+$/, "");
}

async function loadSettings() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  $("base-url").value = stored.baseUrl || DEFAULTS.baseUrl;
  $("token").value = stored.token || "";
  $("account-label").value = stored.accountLabel || "";
  $("course-code").value = stored.courseCode || "";
  $("tags").value = stored.tags || "";
}

async function saveSettings() {
  const settings = collectSettings();
  await chrome.storage.local.set({
    baseUrl: settings.baseUrl,
    token: settings.token,
    accountLabel: settings.accountLabel,
    courseCode: settings.courseCode,
    tags: settings.tags,
  });
  return settings;
}

function renderPreview(result) {
  $("conversation-title").textContent = result.title;
  $("conversation-meta").textContent = `${result.messages.length} 条消息 · ${result.external_id}`;
  $("conversation-snippet").textContent = result.messages[0].content;
  $("preview").hidden = false;
  setBadge(result.platform);
}

async function extractCurrent() {
  if (busy) return;
  extraction = null;
  $("send").disabled = true;
  $("preview").hidden = true;
  setStatus("正在读取当前会话…");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || tab.id === undefined) throw new Error("没有可用的标签页");
    const result = await chrome.tabs.sendMessage(tab.id, { type: "LS_EXTRACT" });
    if (!result) throw new Error("当前页面没有响应采集脚本");
    if (!result.ok) {
      setBadge(result.platform);
      setStatus(result.error, "error");
      return;
    }
    extraction = result;
    renderPreview(result);
    if ($("token").value.trim()) {
      $("send").disabled = false;
      setStatus(`已读取 ${result.messages.length} 条消息，确认后才会发送。`, "ok");
    } else {
      $("settings").open = true;
      setStatus(
        `已读取 ${result.messages.length} 条消息。先在「连接设置」填写采集令牌，再发送。`,
        "warn"
      );
    }
  } catch (error) {
    setBadge("other");
    setStatus(
      "无法读取当前页面。请在 ChatGPT / DeepSeek / Qoder 的会话标签页打开本扩展；也可以改用平台导出文件。",
      "error"
    );
  }
}

function ensureHostPermission(baseUrl) {
  const origin = `${new URL(baseUrl).origin}/*`;
  // 必须在点击手势内直接调用 request；已授权时它不会再次弹窗。
  return chrome.permissions.request({ origins: [origin] });
}

async function sendCurrent() {
  if (busy || !extraction) return;
  const settings = collectSettings();
  if (!settings.token) {
    $("settings").open = true;
    setStatus("请先在「连接设置」填写采集令牌。", "error");
    return;
  }

  let baseUrl;
  try {
    baseUrl = normalizeBaseUrl(settings.baseUrl);
  } catch (error) {
    $("settings").open = true;
    setStatus(String(error.message || error), "error");
    return;
  }

  try {
    const allowed = await ensureHostPermission(baseUrl);
    if (!allowed) {
      setStatus("需要允许扩展访问该地址，才能把会话发送到学习空间。", "error");
      return;
    }
  } catch (error) {
    setStatus(`无法请求站点权限：${error.message || error}`, "error");
    return;
  }

  await saveSettings();
  const payload = {
    platform: extraction.platform,
    account_label:
      settings.accountLabel || `${extraction.platform}-extension`,
    conversations: [
      {
        external_id: extraction.external_id,
        title: extraction.title,
        course_code: settings.courseCode || null,
        tags: splitTags(settings.tags),
        messages: extraction.messages.map((message) => ({
          role: message.role,
          content: message.content,
        })),
      },
    ],
  };

  busy = true;
  $("send").disabled = true;
  setStatus("正在发送…");
  try {
    const response = await fetch(`${baseUrl}/api/ingest`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${settings.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      data = null;
    }
    if (!response.ok) {
      if (response.status === 401) {
        throw new Error("令牌无效或已撤销，请到 学习空间 → 采集令牌 重新创建。");
      }
      if (response.status === 429) {
        throw new Error("请求过于频繁：每个令牌每分钟最多 60 次，请稍后再试。");
      }
      throw new Error((data && data.detail) || `请求失败（HTTP ${response.status}）`);
    }

    const parts = [
      `新增 ${data.conversations_new} 个会话、${data.messages_new} 条消息`,
    ];
    if (data.conversations_updated) {
      parts.push(`合并 ${data.conversations_updated} 个已有会话`);
    }
    if (data.messages_skipped) {
      parts.push(`跳过 ${data.messages_skipped} 条重复消息`);
    }
    const conversation = (data.conversations || [])[0];
    if (conversation && !conversation.course_id) {
      parts.push("已放入收件箱");
    }
    setStatus(`已发送：${parts.join("，")}。`, "ok");
    $("send").disabled = false;
  } catch (error) {
    setStatus(String(error.message || error), "error");
    $("send").disabled = false;
  } finally {
    busy = false;
  }
}

$("send").addEventListener("click", sendCurrent);
$("refresh").addEventListener("click", extractCurrent);
$("save-settings").addEventListener("click", async () => {
  try {
    normalizeBaseUrl($("base-url").value.trim() || DEFAULTS.baseUrl);
  } catch (error) {
    setStatus(String(error.message || error), "error");
    return;
  }
  await saveSettings();
  setStatus("连接设置已保存在本机浏览器。", "ok");
  if (extraction && $("token").value.trim()) $("send").disabled = false;
});
$("token").addEventListener("input", () => {
  if (extraction) $("send").disabled = !$("token").value.trim();
});

(async function init() {
  await loadSettings();
  await extractCurrent();
})();
