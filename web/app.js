/* DS Bing Wallpaper — web UI controller. */
(() => {
  "use strict";

  const TOKEN = (document.querySelector('meta[name="dsbw-token"]') || {}).content || "";
  const VERSION = (document.querySelector('meta[name="dsbw-version"]') || {}).content || "";

  const $ = (id) => document.getElementById(id);
  const state = {
    data: null,
    filter: "all",
    busy: false,
    loading: true,
    firstRender: true,
    lastFilter: null,
  };

  /* ------------------------------------------------------------ util */

  const STAR = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.2-4.1 5.8-.8L12 3.6z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';
  const STAR_FILLED = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.2-4.1 5.8-.8L12 3.6z"/></svg>';

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function bytes(n) {
    if (!n || n < 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let value = n, i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
  }

  function dateLabel(startdate) {
    if (!/^\d{8}$/.test(String(startdate || ""))) return "";
    const raw = String(startdate);
    const d = new Date(`${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}T12:00:00`);
    if (Number.isNaN(d.getTime())) return raw;
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }

  function dayLabel(startdate) {
    const raw = String(startdate || "");
    if (!/^\d{8}$/.test(raw)) return "";
    return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
  }

  function clock(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "–";
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }

  function relativeFuture(iso) {
    if (!iso) return "–";
    const target = new Date(iso).getTime();
    if (Number.isNaN(target)) return "–";
    const diff = Math.round((target - Date.now()) / 60000);
    if (diff <= 0) return "即将进行";
    if (diff < 60) return `${diff} 分钟后`;
    const hours = Math.floor(diff / 60);
    const mins = diff % 60;
    return mins ? `${hours} 小时 ${mins} 分后` : `${hours} 小时后`;
  }

  function safeBingUrl(value) {
    try {
      const url = new URL(String(value || ""));
      const host = url.hostname.toLowerCase();
      if (url.protocol !== "https:") return "";
      return host === "bing.com" || host.endsWith(".bing.com") ? url.href : "";
    } catch (_) {
      return "";
    }
  }

  function thumbUrl(id) {
    return `/api/thumb/${encodeURIComponent(id)}?t=${encodeURIComponent(TOKEN)}`;
  }
  function previewUrl(id) {
    return `/api/preview/${encodeURIComponent(id)}?t=${encodeURIComponent(TOKEN)}`;
  }

  let toastTimer = 0;
  function toast(message, kind) {
    const el = $("toast");
    el.textContent = message;
    el.className = `toast show ${kind || ""}`.trim();
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = "toast"; }, 3200);
  }

  /* ------------------------------------------------------------- api */

  async function api(path, body) {
    const options = {
      method: body === undefined ? "GET" : "POST",
      headers: { "X-DSBW-Token": TOKEN },
      cache: "no-store",
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* ignore */ }
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  /* ----------------------------------------------------------- render */

  function currentImage() {
    const data = state.data;
    if (!data) return null;
    const byId = (id) => data.images.find((img) => img.id === id) || null;
    return byId(data.currentId) || byId(data.selectedId) || data.images.find((img) => img.available) || null;
  }

  function renderStatus() {
    const data = state.data;
    const pill = $("statusPill");

    if (!data) {
      pill.className = "pill offline";
      pill.innerHTML = "<i></i><b>未连接</b>";
      return;
    }

    if (data.lastError) {
      // 抓取失败与模式无关：数据没进来时必须明确报出来。
      pill.className = "pill offline";
      pill.innerHTML = "<i></i><b>同步异常</b>";
    } else if (data.mode === "off") {
      pill.className = "pill paused";
      pill.innerHTML = "<i></i><b>已固定</b>";
    } else if (data.mode === "favorites") {
      pill.className = "pill live";
      pill.innerHTML = "<i></i><b>收藏轮换中</b>";
    } else {
      pill.className = "pill live";
      pill.innerHTML = "<i></i><b>跟随最新</b>";
    }

    $("lastCheck").textContent = data.lastCheckAt ? `${clock(data.lastCheckAt)}` : "–";
    $("nextCheck").textContent = relativeFuture(data.nextCheckAt);
    const desktop = data.desktopPath ? data.desktopPath.split("/").pop() : "未知";
    const desktopEl = $("desktopFile");
    desktopEl.textContent = desktop;
    desktopEl.title = data.desktopPath || "";
  }

  function intervalLabel(minutes) {
    const value = Number(minutes) || 180;
    if (value >= 1440) return `${Math.round(value / 1440)} 天`;
    if (value >= 60) return `${Math.round(value / 60)} 小时`;
    return `${value} 分钟`;
  }

  function renderMode() {
    const data = state.data;
    const note = $("modeNote");
    const wrap = $("intervalWrap");
    const select = $("intervalSelect");
    if (!data) return;

    const mode = ["off", "favorites"].includes(data.mode) ? data.mode : "latest";
    document.querySelectorAll(".segment").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.mode === mode);
    });

    wrap.hidden = mode !== "favorites";
    if (mode === "favorites") select.value = String(data.favoriteIntervalMinutes || 180);

    if (mode === "off") {
      note.textContent = "已固定当前壁纸；图库仍每小时自动抓取";
    } else if (mode === "latest") {
      note.textContent = "跟随 Bing 每日最新壁纸，同一张不重复设置";
    } else {
      const favs = data.stats.favorites;
      if (!favs) note.textContent = "还没有收藏，先点卡片右上角的星标";
      else if (favs === 1) note.textContent = "只有 1 张收藏，会一直使用这一张";
      else note.textContent = `${favs} 张收藏轮换 · 下次 ${data.nextRotateAt ? clock(data.nextRotateAt) : "即将"}`;
    }
  }

  function renderHero() {
    const data = state.data;
    const image = currentImage();
    const img = $("heroImg");
    const hero = $("hero");

    if (!data || !image) {
      hero.style.display = "none";
      return;
    }
    hero.style.display = "";

    const url = previewUrl(image.id);
    if (img.dataset.src !== url) {
      img.dataset.src = url;
      img.classList.remove("loaded");
      img.onload = () => img.classList.add("loaded");
      img.onerror = () => { img.classList.remove("loaded"); };
      img.src = url;
    }

    if (data.mode === "off") {
      $("heroBadge").innerHTML =
        `<i></i>${data.pausedReason === "user-picked" ? "已固定 · 手动选择" : "已固定 · 保持当前"}`;
    } else if (data.mode === "favorites") {
      $("heroBadge").innerHTML = `<i></i>收藏轮换 · 每 ${intervalLabel(data.favoriteIntervalMinutes)}`;
    } else {
      $("heroBadge").innerHTML = "<i></i>自动更新 · 最新壁纸";
    }
    $("heroDate").textContent = dayLabel(image.startdate);
    $("heroTitle").textContent = image.title || "未命名壁纸";
    $("heroCopyright").textContent = image.copyright || "";

    const tags = [];
    if (image.width && image.height) tags.push(`${image.width}×${image.height}`);
    tags.push(`#${String(image.id).split("-").pop()}`);
    if (image.favorite) tags.push("★ 已收藏");
    if (data.mode !== "off" && image.isCurrent) {
      tags.push(data.mode === "favorites" ? "收藏轮换中" : "每小时检查更新");
    }
    $("heroTags").innerHTML = tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("");

    const learn = $("heroLearn");
    const learnUrl = safeBingUrl(image.copyrightlink);
    learn.hidden = !learnUrl;
    if (learnUrl) learn.href = learnUrl;

  }

  function renderStats() {
    const data = state.data;
    if (!data) return;
    $("countAll").textContent = data.stats.count;
    $("countFav").textContent = data.stats.favorites;
    const favs = data.stats.favorites;
    $("libraryMeta").textContent =
      `最近 ${data.retentionDays} 天 · ${data.stats.available} 张 · ${bytes(data.stats.totalBytes)}` +
      (favs ? ` · 收藏 ${favs} 张` : "");
    $("dataDir").textContent = data.app.dataDir;
    $("appVersion").textContent = data.app.version || VERSION;
    $("helperState").textContent = data.app.helper ? "原生壁纸接口已就绪" : "壁纸助手未编译（回退 AppleScript）";
  }

  function renderNotice() {
    const notice = $("notice");
    const data = state.data;
    if (!data) { notice.hidden = true; return; }

    if (data.lastError) {
      notice.hidden = false;
      notice.className = "notice error";
      notice.textContent = `最近一次同步出错：${data.lastError}`;
      return;
    }
    if (data.mode === "off") {
      notice.hidden = false;
      notice.className = "notice";
      notice.textContent =
        "已固定当前壁纸：桌面不会被自动更换，但图库仍会每小时自动抓取新壁纸。"
        + "在顶部选择「最新壁纸」或「收藏轮换」即可恢复自动切换。";
      return;
    }
    notice.hidden = true;
  }

  function renderGrid() {
    const data = state.data;
    const grid = $("grid");
    const empty = $("empty");
    if (!data) { grid.dataset.key = ""; grid.innerHTML = skeleton(6); return; }

    const images = data.images.filter((img) => img.available);
    const filtered = state.filter === "fav" ? images.filter((img) => img.favorite) : images;

    $("toolbarHint").textContent = state.filter === "fav"
      ? `${filtered.length} 张收藏，永久保留不会被清理`
      : "点击任意壁纸即可切换，并暂停自动更新";

    if (!filtered.length) {
      grid.dataset.key = "";
      grid.innerHTML = "";
      empty.hidden = false;
      if (state.filter === "fav") {
        $("emptyTitle").textContent = "还没有收藏";
        $("emptyText").textContent = "点击壁纸右上角的星标即可收藏，收藏的壁纸永远不会被自动清理。";
      } else {
        $("emptyTitle").textContent = data.stats.count ? "图库为空" : "还没有壁纸";
        $("emptyText").textContent = "点击右上角「立即刷新」，从 Bing 抓取今天的壁纸。";
      }
      return;
    }
    empty.hidden = true;

    // 同一批壁纸只打补丁：切换/收藏时不再重建 DOM，避免缩略图闪一下。
    const key = `${state.filter}|${filtered.map((img) => img.id).join(",")}`;
    if (grid.dataset.key === key) {
      patchCards(filtered);
      return;
    }

    // 只有结构变化（首次加载、切换筛选、图库增删）才重建并播放入场动画。
    const animate = state.firstRender || state.filter !== state.lastFilter;
    grid.classList.toggle("is-static", !animate);
    grid.dataset.key = key;
    state.firstRender = false;
    state.lastFilter = state.filter;

    grid.innerHTML = filtered.map((img, index) => {
      const learnUrl = safeBingUrl(img.copyrightlink);
      const classes = ["card"];
      if (img.isCurrent) classes.push("is-current");
      const delay = Math.min(index * 40, 320);
      return `
        <article class="${classes.join(" ")}" role="button" tabindex="0" data-id="${esc(img.id)}"
                 style="animation-delay:${delay}ms" aria-label="${esc(img.title || "壁纸")}">
          <img alt="${esc(img.title || "Bing 壁纸")}" loading="lazy" decoding="async">
          <div class="card-shade"></div>
          <div class="card-top">
            <span class="card-date">${esc(dateLabel(img.startdate))}</span>
            <div class="card-tools">
              <a class="info-btn" href="${esc(learnUrl)}" data-learn="${esc(learnUrl)}"
                 target="_blank" rel="noopener noreferrer" title="了解这张壁纸" aria-label="了解这张壁纸"
                 ${learnUrl ? "" : "hidden"}>
                <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path d="M4.5 11.5l7-7M6 4.5h5.5V10" stroke="currentColor" stroke-width="1.5"
                        stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </a>
              <button class="fav-btn ${img.favorite ? "is-on" : ""}" data-fav="${esc(img.id)}"
                      type="button" aria-label="${img.favorite ? "取消收藏" : "收藏"}"
                      aria-pressed="${img.favorite ? "true" : "false"}">${img.favorite ? STAR_FILLED : STAR}</button>
            </div>
          </div>
          <div class="card-bottom">
            <div class="card-text">
              <h3 class="card-title">${esc(img.title || "Bing 每日壁纸")}</h3>
              <p class="card-sub">${esc(img.copyright || dayLabel(img.startdate))}</p>
            </div>
            <span class="card-action">${img.isCurrent ? "使用中" : "设为壁纸"}</span>
          </div>
        </article>`;
    }).join("");

    grid.querySelectorAll("img[loading]").forEach((img, i) => {
      const item = filtered[i];
      if (!item) return;
      img.onload = () => img.classList.add("loaded");
      img.src = thumbUrl(item.id);
      // 命中浏览器缓存时 onload 可能在赋值前就错过了。
      if (img.complete) img.classList.add("loaded");
    });
  }

  function patchCards(filtered) {
    const nodes = new Map();
    $("grid").querySelectorAll(".card[data-id]").forEach((el) => nodes.set(el.dataset.id, el));

    filtered.forEach((img) => {
      const card = nodes.get(img.id);
      if (!card) return;
      card.classList.toggle("is-current", !!img.isCurrent);

      const action = card.querySelector(".card-action");
      if (action) action.textContent = img.isCurrent ? "使用中" : "设为壁纸";

      const fav = card.querySelector(".fav-btn");
      if (fav) {
        fav.classList.toggle("is-on", !!img.favorite);
        fav.innerHTML = img.favorite ? STAR_FILLED : STAR;
        fav.setAttribute("aria-pressed", img.favorite ? "true" : "false");
        fav.setAttribute("aria-label", img.favorite ? "取消收藏" : "收藏");
      }

      const info = card.querySelector(".info-btn");
      if (info) {
        const url = safeBingUrl(img.copyrightlink);
        info.hidden = !url;
        if (url) {
          info.href = url;
          info.dataset.learn = url;
        }
      }

      const title = card.querySelector(".card-title");
      if (title) title.textContent = img.title || "Bing 每日壁纸";
      const sub = card.querySelector(".card-sub");
      if (sub) sub.textContent = img.copyright || dayLabel(img.startdate);
    });
  }

  function skeleton(count) {
    return Array.from({ length: count }, () => '<div class="card" style="opacity:.35"></div>').join("");
  }

  function render() {
    renderStatus();
    renderMode();
    renderHero();
    renderStats();
    renderNotice();
    renderGrid();
  }

  /* ---------------------------------------------------------- actions */

  async function loadState(silent) {
    try {
      const payload = await api("/api/state");
      state.data = payload;
      state.loading = false;
      render();
    } catch (err) {
      if (!silent) {
        const pill = $("statusPill");
        pill.className = "pill offline";
        pill.innerHTML = "<i></i><b>未连接</b>";
        toast(`无法连接本地服务：${err.message}`, "err");
      }
    }
  }

  async function withBusy(fn) {
    if (state.busy) return;
    state.busy = true;
    try { await fn(); } finally { state.busy = false; }
  }

  async function switchTo(id) {
    await withBusy(async () => {
      const findCard = () => document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
      const card = findCard();
      if (card) card.classList.add("is-busy");
      try {
        const result = await api("/api/switch", { id });
        state.data = result.status;
        render();
        toast("已切换壁纸，并暂停自动更新", "ok");
      } catch (err) {
        toast(`切换失败：${err.message}`, "err");
      } finally {
        // render() 可能复用了原节点，也可能重建过，重新查一次最稳妥。
        const now = findCard();
        if (now) now.classList.remove("is-busy");
      }
    });
  }

  async function toggleFavorite(id) {
    await withBusy(async () => {
      try {
        const result = await api("/api/favorite", { id });
        state.data = result.status;
        render();
        const image = result.status.images.find((img) => img.id === id);
        toast(image && image.favorite ? "已收藏，不会随 10 天清理删除" : "已取消收藏", "ok");
      } catch (err) {
        toast(`操作失败：${err.message}`, "err");
      }
    });
  }

  async function refreshNow() {
    const btn = $("refreshBtn");
    btn.disabled = true;
    btn.classList.add("spin");
    try {
      const result = await api("/api/refresh", {});
      state.data = result.status;
      render();
      const sync = result.sync || {};
      const parts = [];
      if (sync.downloaded && sync.downloaded.length) parts.push(`新增 ${sync.downloaded.length} 张`);
      if (sync.action === "switched") parts.push("已切换最新壁纸");
      if (sync.action === "unchanged") parts.push("已是最新");
      if (sync.removed && sync.removed.length) parts.push(`清理 ${sync.removed.length} 张过期`);
      toast(parts.length ? `同步完成：${parts.join("，")}` : "同步完成", "ok");
    } catch (err) {
      toast(`同步失败：${err.message}`, "err");
    } finally {
      btn.disabled = false;
      btn.classList.remove("spin");
    }
  }

  async function setMode(mode, intervalMinutes) {
    await withBusy(async () => {
      try {
        const payload = { mode };
        if (intervalMinutes) payload.intervalMinutes = intervalMinutes;
        const result = await api("/api/mode", payload);
        state.data = result.status;
        render();
        const messages = {
          favorites: "已切换到收藏轮换",
          latest: "已切换到跟随最新壁纸",
          off: "已固定当前壁纸",
        };
        toast(messages[mode] || "已切换模式", "ok");
      } catch (err) {
        toast(`切换模式失败：${err.message}`, "err");
        await loadState(true);
      }
    });
  }

  async function revealLibrary() {
    const btn = $("revealBtn");
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const result = await api("/api/reveal-library", {});
      toast(`已在访达中打开：${result.path}`, "ok");
    } catch (err) {
      toast(`打开失败：${err.message}`, "err");
    } finally {
      btn.disabled = false;
    }
  }

  /* ------------------------------------------------------------- wire */

  function bind() {
    $("grid").addEventListener("click", (event) => {
      if (event.target.closest("[data-learn]")) {
        // 外链由 <a> 自己处理（保留中键/复制链接），只需阻止冒泡到卡片。
        event.stopPropagation();
        return;
      }

      const fav = event.target.closest("[data-fav]");
      if (fav) {
        event.stopPropagation();
        toggleFavorite(fav.dataset.fav);
        return;
      }
      const card = event.target.closest(".card[data-id]");
      if (card) switchTo(card.dataset.id);
    });

    $("grid").addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const card = event.target.closest(".card[data-id]");
      if (!card || event.target.closest("[data-fav], .info-btn")) return;
      event.preventDefault();
      switchTo(card.dataset.id);
    });

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("is-active"));
        tab.classList.add("is-active");
        state.filter = tab.dataset.filter;
        renderGrid();
      });
    });

    $("refreshBtn").addEventListener("click", refreshNow);
    $("revealBtn").addEventListener("click", revealLibrary);

    document.querySelectorAll(".segment").forEach((btn) => {
      btn.addEventListener("click", () => setMode(btn.dataset.mode));
    });
    $("intervalSelect").addEventListener("change", (event) => {
      setMode("favorites", Number(event.target.value));
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "r" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        refreshNow();
      }
    });
  }

  async function boot() {
    bind();
    await loadState(false);
    setInterval(() => renderStatus(), 30_000);
    setInterval(() => loadState(true), 60_000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) loadState(true);
    });
  }

  boot();
})();
