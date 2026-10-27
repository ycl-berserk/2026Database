(function () {
  "use strict";

  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  function toast(message) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    requestAnimationFrame(() => el.classList.add("show"));
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 2600);
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

  document.querySelectorAll(".flash").forEach(function (el) {
    setTimeout(function () {
      el.style.transition = "opacity .4s";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 420);
    }, 5200);
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

  window.LS = {
    api: api,
    toast: toast,
    csrf: csrf,
    confirmSubmit: confirmSubmit,
    setNavOpen: setNavOpen
  };
})();
