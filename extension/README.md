# 学习空间 · 会话采集扩展（MV3）

把当前 ChatGPT / DeepSeek / Qoder 会话一键发送到自建的学习空间。扩展**不抓取历史、不在后台轮询**：只有你打开弹窗并点击「发送到学习空间」时，才会读取当前标签页的可见对话并发起一次请求。

## 安装

1. 在学习空间页面进入 **采集令牌**（`/tokens`），创建一枚专用令牌。明文只显示一次。
2. 打开 `chrome://extensions`，开启「开发者模式」，选择「加载已解压的扩展程序」，指向本目录 `extension/`。
3. 打开任意受支持的会话页面，点击工具栏中的扩展图标。
4. 展开「连接设置」，填写：
   - 学习空间地址：默认 `http://127.0.0.1:8000`
   - 采集令牌：粘贴上一步的令牌
   - 来源账号：可选，如 `alice@chatgpt`
5. 点击「保存设置」，然后点击「发送到学习空间」。首次发送会请求该地址的站点权限。

发送成功后弹窗会显示新增 / 合并 / 跳过的会话与消息数；未填课程编号时会放入收件箱，之后可在学习空间归类。

## 隐私与权限

- **仅点击发送**：内容脚本只在收到弹窗消息时提取当前会话，不会自动上传，也没有后台请求。
- **令牌只存本机**：令牌保存在 `chrome.storage.local`，不会写入网页，也不会上传到第三方。随时可在 `/tokens` 撤销。
- **最小权限**：固定权限只有 `activeTab` / `storage`；对学习空间地址的访问权限在首次发送时按需申请（`optional_host_permissions`）。
- **数据声明**：仓库内不包含任何真实聊天记录或令牌。演示数据均为围绕课程知识点生成的模拟内容。

## 支持的页面

| 平台 | 页面 | 提取方式 |
|---|---|---|
| ChatGPT | `chatgpt.com`、`chat.openai.com` | `[data-message-author-role]` + Markdown 正文 |
| DeepSeek | `chat.deepseek.com` | 常见消息容器 class 与角色属性 |
| Qoder | `qoder.com`、`qoder.cn` | 常见消息容器 class 与角色属性 |

平台前端改版后，页面结构可能变化。若弹窗提示「无法读取当前页面」或只能识别部分消息，请改用平台的「导出对话」功能，在学习空间 **导入记录** 页上传 JSON / Markdown 文件；导入适配器会处理 ChatGPT `conversations.json`、Qoder 会话导出和 DeepSeek Markdown。

## 开发说明

```
extension/
├── manifest.json   # MV3 声明：content script + popup + 按需站点权限
├── content.js      # 页面提取：平台识别、角色/正文清洗、external_id 生成
├── popup.html      # 弹窗结构：预览、课程/标签、连接设置
├── popup.css       # 与主站一致的纸张/墨色样式
└── popup.js        # 设置持久化、发送 /api/ingest、错误提示
```

发送体与导入页共用同一份统一格式：

```json
{
  "platform": "deepseek",
  "account_label": "alice@deepseek",
  "conversations": [{
    "external_id": "网页地址派生的稳定 ID",
    "title": "会话标题",
    "course_code": "DB",
    "tags": ["事务"],
    "messages": [{"role": "user", "content": "…"}]
  }]
}
```

`tests/unit/test_extension_assets.py` 校验 manifest 权限、弹窗契约、仅 Bearer 鉴权、无硬编码令牌以及 README 中的隐私与回退说明。
