/* Shared helpers for the development pages. All text is inserted with textContent (no innerHTML). */
(function () {
  // A new value on EVERY real page load. After a refresh it changes; a frozen page keeps the old one.
  const id = Math.random().toString(36).slice(2, 10) + "-" + Date.now().toString(36);
  window.__hhPageLoadId = id;
  document.documentElement.setAttribute("data-page-load", id);
})();

window.HH = (function () {
  async function api(path, method, body) {
    const options = { method: method || "GET", headers: {}, credentials: "same-origin" };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    try {
      const res = await fetch(path, options);
      let data = null;
      try { data = await res.json(); } catch (e) { /* no body */ }
      return { ok: res.ok, status: res.status, data: data };
    } catch (e) {
      return { ok: false, status: 0, data: { error: { message: "Cannot reach the server." } } };
    }
  }

  function showError(el, res) {
    el.textContent = "";
    const err = res.data && res.data.error;
    const detail = res.data && res.data.detail;
    const top = document.createElement("div");
    top.textContent = err ? err.message : (typeof detail === "string" ? detail : "Something went wrong.");
    el.appendChild(top);
    if (err && err.details) {
      err.details.forEach(function (d) {
        const line = document.createElement("div");
        line.textContent = d.field + ": " + d.message;
        el.appendChild(line);
      });
    } else if (Array.isArray(detail)) {
      detail.forEach(function (d) {
        const line = document.createElement("div");
        line.textContent = (d.loc || []).slice(1).join(".") + ": " + d.msg;
        el.appendChild(line);
      });
    }
  }

  function renderPlayers(listEl, players) {
    listEl.textContent = "";
    players.forEach(function (p) {
      const li = document.createElement("li");
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.style.background = p.pawn_color;
      li.appendChild(dot);
      li.appendChild(document.createTextNode(p.display_name));
      listEl.appendChild(li);
    });
  }

  // Tiny DOM builder: text is always inserted with textContent / text nodes (never innerHTML).
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "class") { node.className = attrs[key]; }
      else if (key === "text") { node.textContent = attrs[key]; }
      else if (key.indexOf("on") === 0) { node.addEventListener(key.slice(2), attrs[key]); }
      else if (attrs[key] !== null && attrs[key] !== undefined) { node.setAttribute(key, attrs[key]); }
    });
    (children || []).forEach(function (child) {
      if (child === null || child === undefined) { return; }
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function errorText(res) {
    const err = res.data && res.data.error;
    if (!err) { return "Something went wrong."; }
    const extra = (err.details || []).map(function (d) { return d.message; }).join(" ");
    return err.message + (extra ? " " + extra : "");
  }

  return { api: api, showError: showError, renderPlayers: renderPlayers, el: el, errorText: errorText };
})();
