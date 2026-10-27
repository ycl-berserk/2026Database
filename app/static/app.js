(function () {
  "use strict";

  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  function toast(message, options) {
    options = options || {};
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    el.textContent = "";
    const label = document.createElement("span");
    label.textContent = message;
    el.appendChild(label);
    if (options.actionLabel && typeof options.onAction === "function") {
      const action = document.createElement("button");
      action.type = "button";
      action.className = "toast-action";
      action.textContent = options.actionLabel;
      action.addEventListener("click", function () {
        hide();
        options.onAction();
      });
      el.appendChild(action);
    }
    function hide() {
      el.classList.remove("show");
      clearTimeout(el._timer);
      el._timer = setTimeout(function () { el.textContent = ""; }, 220);
    }
    requestAnimationFrame(function () { el.classList.add("show"); });
    clearTimeout(el._timer);
    el._timer = setTimeout(hide, options.duration || 2600);
  }

  async function api(url, options) {
    const opts = Object.assign({ method: "GET", headers: {} }, options || {});
    opts.headers = Object.assign({ Accept: "application/json" }, opts.headers);
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const method = (opts.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") {
      opts.headers["X-CSRF-Token"] = csrf;
    }
    const res = await fetch(url, Object.assign({ credentials: "same-origin" }, opts));
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { detail: text }; }
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) || res.statusText || "请求失败";
      const err = new Error(detail);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function confirmSubmit(message) {
    return function (event) {
      if (!window.confirm(message)) {
        event.preventDefault();
        return false;
      }
      return true;
    };
  }

  document.addEventListener("click", function (event) {
    const trigger = event.target.closest("[data-confirm]");
    if (trigger) {
      const message = trigger.getAttribute("data-confirm") || "确认执行该操作？";
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    }
  });

  function dismissFlash(el) {
    if (!el || !el.isConnected) return;
    el.style.transition = "opacity .25s";
    el.style.opacity = "0";
    setTimeout(function () { el.remove(); }, 260);
  }

  document.querySelectorAll(".flash").forEach(function (el) {
    const close = el.querySelector(".flash-close");
    if (close) close.addEventListener("click", function () { dismissFlash(el); });
    if (el.getAttribute("data-flash-category") !== "error") {
      setTimeout(function () { dismissFlash(el); }, 5200);
    }
  });

  document.querySelectorAll("[data-demo-login]").forEach(function (el) {
    el.addEventListener("click", function () {
      const form = document.getElementById("login-form");
      const username = document.getElementById("login-username");
      const password = document.getElementById("login-password");
      if (!form || !username || !password) return;
      username.value = el.getAttribute("data-username") || "";
      password.value = el.getAttribute("data-password") || "";
      if (form.requestSubmit) form.requestSubmit();
      else form.submit();
    });
  });

  document.addEventListener("submit", function (event) {
    const form = event.target.closest("form[data-undo-submit]");
    if (!form || form.dataset.undoPending === "1") return;
    event.preventDefault();
    if (form.checkValidity && !form.checkValidity()) {
      form.reportValidity();
      return;
    }
    form.dataset.undoPending = "1";
    const item = form.closest("[data-undo-item]");
    if (item) {
      item.classList.add("is-removing");
      item.setAttribute("aria-hidden", "true");
    }
    const message = form.getAttribute("data-undo-submit") || "已删除";
    let settled = false;
    const restore = function () {
      form.dataset.undoPending = "";
      if (item) {
        item.classList.remove("is-removing");
        item.removeAttribute("aria-hidden");
      }
    };
    const timer = setTimeout(function () {
      settled = true;
      form.submit();
    }, 5000);
    toast(message, {
      duration: 5000,
      actionLabel: "撤销",
      onAction: function () {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        restore();
        toast("已撤销");
      }
    });
    window.addEventListener("pagehide", function () {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      // 离开页面时立即提交，避免“看起来删了其实没删”；撤销按钮只在页面停留期间可用。
      if (navigator.sendBeacon) {
        try {
          navigator.sendBeacon(form.action, new FormData(form));
          return;
        } catch (_) { /* 回退为普通提交 */ }
      }
      form.submit();
    }, { once: true });
  });

  function copyText(event) {
    const trigger = event.target.closest("[data-copy]");
    if (!trigger) return;
    const text = trigger.getAttribute("data-copy");
    if (!text) return;
    navigator.clipboard.writeText(text).then(function () {
      toast("已复制到剪贴板");
    }).catch(function () {
      toast("复制失败，请手动选择");
    });
  }
  document.addEventListener("click", copyText);

  const navToggles = document.querySelectorAll("[data-nav-toggle]");
  const navBackdrop = document.querySelector(".nav-backdrop");
  const sidebar = document.getElementById("sidebar");
  const mobileQuery = window.matchMedia("(max-width: 820px)");

  function setNavOpen(open) {
    document.body.classList.toggle("nav-open", open);
    navToggles.forEach(function (el) {
      el.setAttribute("aria-expanded", String(open));
    });
    if (navBackdrop) navBackdrop.setAttribute("aria-hidden", String(!open));
    if (open && mobileQuery.matches && sidebar) {
      const first = sidebar.querySelector(".nav-link");
      if (first) {
        try { first.focus({ preventScroll: true }); } catch (_) { first.focus(); }
      }
    }
  }

  navToggles.forEach(function (el) {
    el.addEventListener("click", function () {
      setNavOpen(!document.body.classList.contains("nav-open"));
    });
  });
  if (navBackdrop) navBackdrop.addEventListener("click", function () { setNavOpen(false); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setNavOpen(false);
  });
  if (sidebar) {
    sidebar.querySelectorAll(".nav-link").forEach(function (link) {
      link.addEventListener("click", function () {
        if (mobileQuery.matches) setNavOpen(false);
      });
    });
  }
  if (mobileQuery.addEventListener) {
    mobileQuery.addEventListener("change", function (event) {
      if (!event.matches) setNavOpen(false);
    });
  }

  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {
        /* 离线能力不可用时不影响页面使用 */
      });
    });
  }

  const OFFLINE_DB = "ls-offline";
  const OFFLINE_DB_VERSION = 2;
  const OFFLINE_STORES = ["conversations", "drafts", "outbox", "queue"];
  let offlineDbPromise = null;

  function newEventId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (ch) {
      const r = (Math.random() * 16) | 0;
      const v = ch === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function openOfflineDb() {
    if (!("indexedDB" in window)) {
      return Promise.reject(new Error("当前浏览器不支持离线缓存"));
    }
    if (!offlineDbPromise) {
      offlineDbPromise = new Promise(function (resolve, reject) {
        const req = indexedDB.open(OFFLINE_DB, OFFLINE_DB_VERSION);
        req.onupgradeneeded = function () {
          const db = req.result;
          OFFLINE_STORES.forEach(function (name) {
            if (!db.objectStoreNames.contains(name)) {
              db.createObjectStore(name, { keyPath: "id" });
            }
          });
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    }
    return offlineDbPromise;
  }

  function requestResult(req) {
    return new Promise(function (resolve, reject) {
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  async function idbGetAll(store) {
    const db = await openOfflineDb();
    return requestResult(db.transaction(store, "readonly").objectStore(store).getAll());
  }

  async function idbGet(store, key) {
    const db = await openOfflineDb();
    return requestResult(db.transaction(store, "readonly").objectStore(store).get(key));
  }

  async function idbPut(store, value) {
    const db = await openOfflineDb();
    return new Promise(function (resolve, reject) {
      const tx = db.transaction(store, "readwrite");
      tx.objectStore(store).put(value);
      tx.oncomplete = function () { resolve(value); };
      tx.onerror = function () { reject(tx.error); };
    });
  }

  async function idbDelete(store, key) {
    const db = await openOfflineDb();
    return new Promise(function (resolve, reject) {
      const tx = db.transaction(store, "readwrite");
      tx.objectStore(store).delete(key);
      tx.oncomplete = resolve;
      tx.onerror = function () { reject(tx.error); };
    });
  }

  async function idbClear(store) {
    const db = await openOfflineDb();
    return new Promise(function (resolve, reject) {
      const tx = db.transaction(store, "readwrite");
      tx.objectStore(store).clear();
      tx.oncomplete = resolve;
      tx.onerror = function () { reject(tx.error); };
    });
  }

  async function idbCount(store) {
    const db = await openOfflineDb();
    return requestResult(db.transaction(store, "readonly").objectStore(store).count());
  }

  const offline = {
    supported: "indexedDB" in window,
    saveConversation: async function (id) {
      const data = await api("/api/conversations/" + id + "/offline");
      await idbPut("conversations", {
        id: Number(id),
        saved_at: data.saved_at,
        data: data
      });
      updateOfflineCount();
      return data;
    },
    isSaved: async function (id) {
      return Boolean(await idbGet("conversations", Number(id)));
    },
    listConversations: function () { return idbGetAll("conversations"); },
    removeConversation: function (id) { return idbDelete("conversations", Number(id)); },
    queueDraft: function (draft) { return idbPut("drafts", draft); },
    queueReview: function (action) { return idbPut("outbox", action); },
    pendingCount: async function () {
      const drafts = await idbCount("drafts");
      const actions = await idbCount("outbox");
      return { drafts: drafts, reviews: actions, total: drafts + actions };
    },
    flush: flushOfflineWork,
    clear: async function () {
      await Promise.all(OFFLINE_STORES.map(function (name) { return idbClear(name); }));
      updateOfflineCount();
    }
  };

  async function updateOfflineCount() {
    const links = document.querySelectorAll("[data-offline-count-link]");
    if (!links.length || !offline.supported) return;
    try {
      const conversations = await idbCount("conversations");
      const pending = await offline.pendingCount();
      links.forEach(function (el) {
        let label = conversations ? "离线资料 " + conversations : "离线资料";
        if (pending.total) label += " · 待同步 " + pending.total;
        el.textContent = label;
      });
    } catch (_) { /* 隐私模式下 IndexedDB 不可用，不影响页面 */ }
  }

  async function flushOfflineWork(quiet) {
    if (!navigator.onLine || !offline.supported) return;
    let sent = 0;
    let dropped = 0;
    text: {
      try {
        const drafts = await idbGetAll("drafts");
        for (const draft of drafts) {
          try {
            await api("/api/annotations", {
              method: "POST",
              body: {
                message_id: draft.message_id,
                kind: draft.kind,
                content: draft.content,
                event_id: draft.id
              }
            });
            sent += 1;
          } catch (err) {
            if (err.status >= 400 && err.status < 500) dropped += 1;
            else break text;
          }
          await idbDelete("drafts", draft.id);
        }
        const actions = await idbGetAll("outbox");
        actions.sort(function (a, b) {
          return String(a.created_at || "").localeCompare(String(b.created_at || ""));
        });
        for (const action of actions) {
          try {
            await api("/api/reviews/" + action.review_id + "/" + action.action, {
              method: "POST",
              body: { event_id: action.id }
            });
            sent += 1;
          } catch (err) {
            if (err.status >= 400 && err.status < 500) dropped += 1;
            else break text;
          }
          await idbDelete("outbox", action.id);
        }
      } finally {
        updateOfflineCount();
      }
    }
    if (sent && !quiet) {
      toast("已同步 " + sent + " 条离线操作");
    }
    if (dropped) {
      toast(dropped + " 条离线操作已失效，未再重试");
    }
  }

  async function cacheReviewQueue() {
    if (!navigator.onLine || !offline.supported) return;
    try {
      const data = await api("/api/reviews/offline");
      await idbPut("queue", {
        id: "active",
        generated_at: data.generated_at,
        csrf_token: data.csrf_token,
        items: data.items || []
      });
    } catch (_) { /* 队列缓存失败不影响在线复习 */ }
  }

  document.addEventListener("submit", function (event) {
    const form = event.target.closest(".annotate-form");
    if (!form || navigator.onLine || !offline.supported) return;
    event.preventDefault();
    if (form.checkValidity && !form.checkValidity()) {
      form.reportValidity();
      return;
    }
    const data = new FormData(form);
    const draft = {
      id: newEventId(),
      message_id: Number(data.get("message_id") || 0),
      kind: data.get("kind") || "note",
      content: String(data.get("content") || "").trim(),
      back: data.get("back") || "",
      csrf_token: csrf,
      created_at: new Date().toISOString()
    };
    offline.queueDraft(draft).then(function () {
      form.reset();
      const details = form.closest("details");
      if (details) details.open = false;
      updateOfflineCount();
      toast("已暂存批注，联网后自动提交");
    }).catch(function () {
      toast("离线暂存失败，请检查浏览器存储权限");
    });
  });

  document.addEventListener("submit", function (event) {
    const form = event.target.closest("form[data-review-action]");
    if (!form) return;
    let input = form.querySelector('input[name="event_id"]');
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "event_id";
      input.value = newEventId();
      form.appendChild(input);
    }
    if (navigator.onLine || !offline.supported) return;
    event.preventDefault();
    const action = {
      id: input.value,
      review_id: Number(form.getAttribute("data-review-id") || 0),
      action: form.getAttribute("data-review-action") || "done",
      scope: (new FormData(form)).get("scope") || "active",
      csrf_token: csrf,
      created_at: new Date().toISOString()
    };
    offline.queueReview(action).then(function () {
      const card = form.closest("[data-undo-item]");
      if (card) {
        card.classList.add("review-pending");
        const badge = document.createElement("span");
        badge.className = "badge badge-warn";
        badge.textContent = "离线打卡 · 待同步";
        const head = card.querySelector(".review-head");
        if (head) head.appendChild(badge);
      }
      form.querySelectorAll("button").forEach(function (btn) { btn.disabled = true; });
      updateOfflineCount();
      toast("当前离线，打卡已排队，联网后自动提交");
    }).catch(function () {
      toast("离线打卡暂存失败，请检查浏览器存储权限");
    });
  });

  if (location.pathname === "/reviews") cacheReviewQueue();
  flushOfflineWork(true);

  document.addEventListener("click", async function (event) {
    const btn = event.target.closest("[data-offline-save]");
    if (!btn) return;
    if (!navigator.onLine) {
      toast("当前离线，联网后再保存");
      return;
    }
    const id = btn.getAttribute("data-offline-save");
    btn.disabled = true;
    try {
      const data = await offline.saveConversation(id);
      btn.textContent = "已保存到离线";
      toast("已保存「" + data.conversation.title + "」到离线");
    } catch (err) {
      toast((err && err.message) || "保存失败，请稍后重试");
    } finally {
      btn.disabled = false;
    }
  });

  document.querySelectorAll("[data-offline-save]").forEach(function (btn) {
    if (!offline.supported) return;
    offline.isSaved(btn.getAttribute("data-offline-save")).then(function (saved) {
      if (saved) btn.textContent = "已保存到离线";
    }).catch(function () {});
  });
  updateOfflineCount();

  function updateConnectionBanner() {
    const banner = document.querySelector("[data-offline-banner]");
    const online = navigator.onLine;
    document.body.classList.toggle("is-offline", !online);
    if (banner) banner.hidden = online;
  }
  window.addEventListener("online", function () {
    updateConnectionBanner();
    toast("网络已恢复");
    flushOfflineWork(false);
    if (location.pathname === "/reviews") cacheReviewQueue();
  });
  window.addEventListener("offline", function () {
    updateConnectionBanner();
    toast("已离线，已保存的会话仍可阅读");
  });
  updateConnectionBanner();

  window.LS = {
    api: api,
    toast: toast,
    csrf: csrf,
    confirmSubmit: confirmSubmit,
    setNavOpen: setNavOpen,
    offline: offline,
    updateOfflineCount: updateOfflineCount
  };
})();
