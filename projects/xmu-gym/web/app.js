import { SPORTS } from "./catalog.js";

/* ============================================================
   本机桥：只走 /api/invoke，命令与 app/server.py 一一对应。
   页面不模拟任何预约结果；失败就照实说。
   ============================================================ */
async function invoke(command, args = {}) {
  let res;
  try {
    res = await fetch("/api/invoke", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-XMU-Bridge": "1" },
      body: JSON.stringify({ command, args }),
    });
  } catch {
    throw new Error("连不上本机服务：请确认 127.0.0.1:1420 的页面服务在运行。");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "本地操作失败");
  return body.result;
}

/* ============================================================
   时间：一律按中国时区算，不猜本机时区。
   ============================================================ */
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function chinaToday() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (t) => parts.find((p) => p.type === t)?.value ?? "";
  return { y: +get("year"), m: +get("month"), d: +get("day") };
}

function isoOf(offset = 0) {
  const { y, m, d } = chinaToday();
  const t = new Date(Date.UTC(y, m - 1, d + offset));
  return `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, "0")}-${String(
    t.getUTCDate()
  ).padStart(2, "0")}`;
}

function dayInfo(offset) {
  const iso = isoOf(offset);
  const [y, m, d] = iso.split("-").map(Number);
  const wk = WEEK[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  const label = ["今天", "明天", "后天"][offset] ?? "";
  return { iso, y, m, d, wk, label, md: `${m}.${String(d).padStart(2, "0")}` };
}

function chinaClock() {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

/* ============================================================
   状态
   ============================================================ */
const S = {
  tab: "tomorrow",
  sport: "健身房",
  venue: "翔安校区健身房",
  offset: 1,
  availability: null,
  selected: null,
  orders: null,
  account: null, // 已核验才为 true；指纹不外显
  fp: null,
  accountLabel: "",
  profiles: [],
  selectedProfile: "",
  demo: false,
  authBusy: false,
  authError: "",
  queryBusy: false,
  bookBusy: false,
  bookMode: "book",
  scheduler: "unknown",
  daily: null,
  stage: "",
  stageOk: false,
  lastError: "",
};

const $ = (id) => document.getElementById(id);

/* 微信卡图模式：/?card=tomorrow|orders|settings —— 隐藏标签栏、收窄版心，供无头浏览器截图 */
const CARD_VIEW = new URLSearchParams(location.search).get("card");

/* ============================================================
   小工具
   ============================================================ */
function toast(text, kind = "") {
  const host = $("toast_host");
  const el = document.createElement("div");
  el.className = "toast" + (kind ? ` is-${kind}` : "");
  el.textContent = text;
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .3s ease, transform .3s ease";
    el.style.opacity = "0";
    el.style.transform = "translateY(6px)";
    setTimeout(() => el.remove(), 320);
  }, kind === "bad" ? 9000 : 4200);
}

function catalogVenue() {
  const sport = SPORTS.find((s) => s.ocr_label === S.sport) || SPORTS[0];
  const venue =
    sport.venues.find((v) => v.ocr_label === S.venue) || sport.venues[0];
  return { sport, venue };
}

function slotQuota(slot) {
  const cells = slot.cells || [];
  const unlocked = cells.filter((c) => !c.admin_locked);
  const known = unlocked.filter(
    (c) =>
      ["available", "unavailable"].includes(c.status) &&
      c.capacity > 0 &&
      Number.isFinite(c.remaining) &&
      c.remaining >= 0
  );
  const remaining = known.reduce((sum, c) => sum + c.remaining, 0);
  const allFull =
    unlocked.length > 0 &&
    unlocked.every((c) => known.includes(c) && c.remaining === 0);
  const lockedOnly = unlocked.length === 0 && cells.length > 0;
  return { remaining, allFull, lockedOnly, known: known.length, total: cells.length };
}

function orderFor(iso) {
  return (S.orders || []).find((o) => o.date === iso) || null;
}

/* 核验账号失败：别拿「提交结果不明」吓人，直接给下一步动作。 */
function authErrorText(error) {
  const raw = String((error && error.message) || error || "");
  const code = (raw.match(/[A-Z][A-Z_]{2,60}/) || [])[0] || "";
  const needsLogin = [
    "NO_ENTRY", "INVALID_ENTRY", "SESSION_EXPIRED", "SESSION_READ_FAILED",
    "NO_SESSION_COOKIE", "HTTP_AUTH_UNVERIFIED", "LOGIN_CONFIRM_REQUIRED",
  ];
  if (needsLogin.includes(code)) {
    return `核验失败 · ${code}\n读不到可用的微信登录态：在电脑微信里打开「厦大体育」小程序，确认还在登录状态，再点一次核验。`;
  }
  return explain(error, "核验账号");
}

/* 服务端的错误文案是按「提交预约」写的；只读操作用自己的说法，别吓人。 */
function explain(error, action) {
  const raw = String((error && error.message) || error || "");
  const code = (raw.match(/[A-Z][A-Z_]{2,60}/) || [])[0];
  const detail = code ? raw.replace(code + "：", "").trim() : raw;
  if (!code) return raw;
  return `${action}失败 · ${code}\n这次只发了${action === "查场次" ? "查询" : action}，没有提交任何预约。`;
}

/* ============================================================
   渲染：报头 / 标签
   ============================================================ */
function renderChrome() {
  const t = dayInfo(1);
  $("tab_meta_tomorrow").textContent = `${t.md} ${t.wk}`;
  $("tab_meta_orders").textContent = S.orders ? `${S.orders.length} 个` : "—";

  const bits = [];
  if (S.demo) bits.push("演示数据");
  if (S.scheduler === "enabled") bits.push("每日任务 06:58 待命");
  else if (S.scheduler === "paused") bits.push("每日任务已暂停");
  else if (S.scheduler === "not_configured") bits.push("每日任务未配置");
  else bits.push("每日任务状态未知");
  if (S.account) bits.push("账号已核验");
  $("masthead_line").textContent = bits.join(" · ");

  document.querySelectorAll("[data-tab]").forEach((btn) => {
    const on = btn.dataset.tab === S.tab;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-selected", String(on));
  });
}

function tick() {
  $("masthead_clock").textContent = chinaClock();
}

/* ============================================================
   渲染：明天
   ============================================================ */
function renderTomorrow() {
  const info = dayInfo(S.offset);
  $("ticket_kicker").textContent = `${info.label} · ${info.y}年${info.m}月${info.d}日 ${info.wk}`;

  document.querySelectorAll("#day_switch .day").forEach((btn) => {
    const off = +btn.dataset.offset;
    const d = dayInfo(off);
    btn.classList.toggle("is-on", off === S.offset);
    btn.querySelector("small").textContent = `${d.md} ${d.wk}`;
  });

  const booked = orderFor(info.iso);
  const stamp = $("ticket_stamp");
  const slotsHost = $("slots");
  const stage = $("stage_line");
  const btnAuth = $("btn_auth");
  const btnQuery = $("btn_query");
  const btnBook = $("btn_book");

  btnAuth.hidden = true;
  btnQuery.hidden = true;
  stamp.hidden = true;

  if (booked) {
    $("ticket_title").textContent = `${booked.start}–${booked.end}`;
    $("ticket_sub").textContent = `${booked.venue || S.venue} · ${
      booked.court || "场地待定"
    }。这一场已经在你名下，不用再提交。`;
    stamp.hidden = false;
    stage.innerHTML = "<b>已核实</b>：这一天的预约在订单里能查到。";
    slotsHost.innerHTML = `
      <div class="booked-row">
        <div class="booked-main">
          <p class="booked-time">${booked.start}–${booked.end}</p>
          <p class="booked-meta">${booked.court || ""} ${
            booked.can_cancel ? "· 开始前一小时以外可以取消" : "· 已进入不可取消的时段"
          }</p>
        </div>
        <button type="button" class="ticket-btn" data-qr="${booked.order_id}" data-det="${
          booked.details_id
        }">
          <span class="stub">${
            '<svg width="16" height="12" viewBox="0 0 17 13" fill="none" aria-hidden="true"><path d="M1 3.4C1 2.6 1.6 2 2.4 2h12.2c.8 0 1.4.6 1.4 1.4v1.1a1.6 1.6 0 0 0 0 3.2v1.1c0 .8-.6 1.4-1.4 1.4H2.4C1.6 9.2 1 8.6 1 7.8V6.7a1.6 1.6 0 0 0 0-3.2z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M6 5.2v2.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-dasharray="1 1.6"/></svg>'
          }入场码</span>
          <span class="tail">${booked.date.slice(5).replace("-", ".")}</span>
        </button>
        <button type="button" class="hold" data-hold-cancel="${booked.order_id}" data-det="${
          booked.details_id
        }" ${booked.can_cancel ? "" : "disabled"}>
          <span class="fill"></span><span class="txt">按住 1 秒取消</span>
        </button>
      </div>`;
    btnBook.disabled = true;
    btnBook.innerHTML = `这一天已经订好 <span class="arrow" aria-hidden="true">✓</span>`;
    $("foot_hint").textContent =
      "同一时段不要重复提交：即使换一个按钮再点一次，也可能生成第二笔订单。";
    return;
  }

  S.bookMode = "book";
  btnBook.innerHTML = `订这个时段 <span class="arrow" aria-hidden="true">→</span>`;

  if (S.authBusy) {
    $("ticket_title").textContent = "正在核验账号";
    $("ticket_sub").textContent = "读本机微信里的登录态，不需要输密码。";
    stage.innerHTML = "核验中…";
    slotsHost.innerHTML = `<div class="slot-empty">核验账号后才能查场次</div>`;
    btnBook.disabled = true;
    return;
  }

  if (!S.account) {
    $("ticket_title").textContent = "先核验一下账号";
    $("ticket_sub").textContent =
      "登录态取自本机微信里的厦大体育小程序。小程序保持登录，关掉窗口也不影响。";
    stage.innerHTML = S.authError
      ? `<b>上次核验失败</b> · ${S.authError}，先按提示处理再点「核验账号」`
      : "账号还没核验，查不了场次。";
    btnAuth.hidden = false;
    slotsHost.innerHTML = `<div class="slot-empty">核验后会列出这一天还有名额的时段</div>`;
    btnBook.disabled = true;
    $("foot_hint").textContent = "预约失败时页面不会自动重试，避免重复下单。";
    return;
  }

  if (S.queryBusy) {
    $("ticket_title").textContent = "正在查场次";
    $("ticket_sub").textContent = "名额只代表查询那一刻的状态。";
    stage.innerHTML = "查询中…";
    slotsHost.innerHTML = `<div class="slot-empty">读取中…</div>`;
    btnBook.disabled = true;
    return;
  }

  const av = S.availability;
  if (!av) {
    const failed = S.lastError && S.lastError.split("\n")[0];
    $("ticket_title").textContent = failed ? "这次没查到" : "查一下这一天的场次";
    $("ticket_sub").textContent = failed
      ? failed
      : "先查再订，避免拿旧名额去提交。";
    stage.innerHTML = failed ? "<b>查询失败</b>，什么都没提交" : "<b>账号已核验</b>，还没有查场次。";
    btnQuery.hidden = false;
    slotsHost.innerHTML = `<div class="slot-empty">${
      failed ? "再点一次「查询场次」" : "点「查场次」看这一天还剩什么"
    }</div>`;
    btnBook.disabled = true;
    return;
  }

  if (av.state === "date_not_published") {
    $("ticket_title").textContent = "这一天还没放号";
    $("ticket_sub").textContent =
      "学校只在放号之后才列出时段。次日场次通常每天 07:00 放出。";
    stage.innerHTML = "学校还没发布这一天的场次。";
    btnQuery.hidden = false;
    slotsHost.innerHTML = `<div class="slot-empty">等放号之后再查</div>`;
    btnBook.disabled = true;
    return;
  }

  const slots = av.slots || [];
  if (!slots.length) {
    $("ticket_title").textContent = "查不到时段";
    $("ticket_sub").textContent = "可能是场地当天不开放，或者接口返回为空。";
    stage.innerHTML = "接口返回的时段是空的。";
    btnQuery.hidden = false;
    slotsHost.innerHTML = `<div class="slot-empty">没有可选时段</div>`;
    btnBook.disabled = true;
    return;
  }

  let openCount = 0;
  const sel = S.selected;
  slotsHost.innerHTML = slots
    .map((slot) => {
      const q = slotQuota(slot);
      const isFull = q.allFull || q.lockedOnly;
      if (!isFull) openCount += 1;
      const on = sel && sel.start === slot.start && sel.end === slot.end;
      const note = q.lockedOnly
        ? "全部场地锁定"
        : q.allFull
        ? "已满"
        : q.known
        ? `余 ${q.remaining} 个名额`
        : "名额未知";
      return `<button type="button" class="slot${on ? " is-on" : ""}${
        isFull ? " is-full" : ""
      }" data-start="${slot.start}" data-end="${slot.end}" ${
        isFull ? "disabled" : ""
      } aria-pressed="${on ? "true" : "false"}">
        <span class="slot-time">${slot.start}–${slot.end}</span>
        <span class="slot-note">${isFull ? "" : '<i class="dot"></i>'}${note}</span>
      </button>`;
    })
    .join("");

  slotsHost.classList.toggle("is-allfull", openCount === 0);
  $("ticket_sub").textContent = `${catalogVenue().venue.label} · 查询于 ${
    (av.fetched_at || "").slice(11, 16) || "刚刚"
  }`;
  $("ticket_title").textContent = openCount
    ? `还有 ${openCount} 个时段能订`
    : "这一天全满了";
  stage.innerHTML = `<b>账号已核验</b> · ${av.slots.length} 个时段，${openCount} 个还有名额`;
  btnQuery.hidden = false;

  if (!openCount) {
    S.bookMode = "nextday";
    btnBook.disabled = S.offset >= 2;
    btnBook.innerHTML = S.offset >= 2
      ? "后天也满了"
      : `看后天 <span class="arrow" aria-hidden="true">→</span>`;
    $("foot_hint").textContent =
      "满额只是查询那一刻的状态；有人取消，名额会回到池子里。每天 07:00 放号时最值得再查一次。";
    return;
  }

  S.bookMode = "book";
  btnBook.disabled = !sel;
  $("foot_hint").textContent = sel
    ? "提交后如果结果不明，页面只会让你去小程序核实，不会重复提交。"
    : "先在上面挑一个时段。";
}

/* ============================================================
   渲染：每日纸条
   ============================================================ */
function renderDaily() {
  const dot = $("daily_dot");
  dot.className = "strip-dot";
  const d = S.daily;
  const stateText = {
    enabled: "已启用",
    paused: "已暂停",
    not_configured: "未配置",
    unknown: "状态未知",
  }[S.scheduler];

  if (S.scheduler === "paused" || S.scheduler === "not_configured") dot.classList.add("is-warn");
  if (S.scheduler === "unknown") dot.classList.add("is-bad");

  if (!d) {
    $("daily_title").textContent = "每日自动预约 · 还没设过";
    $("daily_sub").textContent = "到「设置」里选一个首选时段，每天 07:00 会自动提交。";
    return;
  }
  const t = d.task;
  const later = t.allow_time_fallback ? "满了往后找更晚的" : "满了就不订";
  $("daily_title").textContent = `每日自动预约 · ${stateText}`;
  $("daily_sub").textContent = `次日 ${t.target_time_start}–${t.target_time_end} · ${t.venue} · ${later}`;
}

/* ============================================================
   渲染：订单
   ============================================================ */
function renderOrders() {
  const host = $("orders_host");
  if (!S.account) {
    host.innerHTML = `<div class="empty">
      <p class="empty-title">先核验账号</p>
      <p class="empty-desc">订单要读学校系统，先让我确认这台机器上的微信登录态还行。</p>
      <div style="margin-top:18px"><button type="button" class="btn btn-primary btn-sm" id="empty_auth">核验账号</button></div>
    </div>`;
    $("empty_auth").onclick = () => checkAuth(false);
    return;
  }
  if (!S.orders) {
    host.innerHTML = `<p class="loading-line">正在读取订单…</p>`;
    return;
  }
  if (!S.orders.length) {
    host.innerHTML = `<div class="empty">
      <img class="empty-art" src="/assets/egret.png" alt="" />
      <p class="empty-title">手上没有未核销的预约</p>
      <p class="empty-desc">约好之后会出现在这里，入场码也在这里取。核销或取消过的会自己消失。</p>
    </div>`;
    return;
  }
  host.innerHTML = S.orders
    .map((o) => {
      const [y, m, d] = o.date.split("-").map(Number);
      const wk = WEEK[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
      return `<div class="order">
        <div class="order-date">
          <p class="order-day">${d}</p>
          <p class="order-wk">${m} 月 · ${wk}</p>
        </div>
        <div class="order-main">
          <p class="order-time">${o.start}–${o.end}</p>
          <p class="order-meta">${o.venue || ""}${o.court ? " · " + o.court : ""}${
        o.sport ? " · " + o.sport : ""
      }</p>
        </div>
        <div class="order-actions">
          <button type="button" class="ticket-btn" data-qr="${o.order_id}" data-det="${o.details_id}">
            <span class="stub"><svg width="16" height="12" viewBox="0 0 17 13" fill="none" aria-hidden="true"><path d="M1 3.4C1 2.6 1.6 2 2.4 2h12.2c.8 0 1.4.6 1.4 1.4v1.1a1.6 1.6 0 0 0 0 3.2v1.1c0 .8-.6 1.4-1.4 1.4H2.4C1.6 9.2 1 8.6 1 7.8V6.7a1.6 1.6 0 0 0 0-3.2z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M6 5.2v2.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-dasharray="1 1.6"/></svg>入场码</span>
            <span class="tail">${o.date.slice(5).replace("-", ".")}</span>
          </button>
          <button type="button" class="hold" data-hold-cancel="${o.order_id}" data-det="${o.details_id}" ${
        o.can_cancel ? "" : "disabled"
      }><span class="fill"></span><span class="txt">${
        o.can_cancel ? "按住取消" : "不可取消"
      }</span></button>
        </div>
      </div>`;
    })
    .join("");
}

/* ============================================================
   设置
   ============================================================ */
function fillSportSelect() {
  const sel = $("sel_sport");
  sel.innerHTML = SPORTS.map(
    (s) =>
      `<option value="${s.ocr_label}" ${s.ocr_label === S.sport ? "selected" : ""}>${s.label}</option>`
  ).join("");
}

function fillVenueSelect() {
  const { sport } = catalogVenue();
  const sel = $("sel_venue");
  sel.innerHTML = sport.venues
    .map(
      (v) =>
        `<option value="${v.ocr_label}" ${v.ocr_label === S.venue ? "selected" : ""}>${v.label}</option>`
    )
    .join("");
}

function fillSlotSelects(prefStart, prefEnd) {
  const { venue } = catalogVenue();
  const slots = venue.slots.length
    ? venue.slots
    : [
        { start: "13:30", end: "15:00" },
        { start: "15:00", end: "16:30" },
      ];
  const starts = [...new Set(slots.map((s) => s.start))];
  const selStart = $("sel_start");
  selStart.innerHTML = starts
    .map((t) => `<option value="${t}" ${t === prefStart ? "selected" : ""}>${t}</option>`)
    .join("");
  const pickStart = starts.includes(prefStart) ? prefStart : starts[0];
  const ends = [...new Set(slots.filter((s) => s.start >= pickStart).map((s) => s.end))];
  $("sel_end").innerHTML = ends
    .map((t) => `<option value="${t}" ${t === prefEnd ? "selected" : ""}>${t}</option>`)
    .join("");
}

function renderSettings() {
  fillSportSelect();
  fillVenueSelect();
  $("daily_venue_note").textContent = `当前场馆：${S.sport} · ${S.venue}（在「预订」里换）`;
  const t = S.daily?.task;
  fillSlotSelects(t?.target_time_start || "13:30", t?.target_time_end || "15:00");
  $("chk_fallback").checked = t ? t.allow_time_fallback !== false : true;

  const acc = $("account_state");
  acc.className = "account-state" + (S.account ? " is-ok" : "");
  acc.textContent = S.account
    ? `已核验：${S.accountLabel || "本机微信里的登录态可用"}`
    : S.authBusy
    ? "核验中…"
    : "未核验";

  const profile = $("sel_profile");
  if (profile) {
    const wanted = S.selectedProfile || profile.value;
    const placeholder = S.profiles.length > 1
      ? `<option value="">请选择微信配置</option>`
      : "";
    profile.innerHTML = S.profiles.length
      ? placeholder + S.profiles
          .map(
            (row) =>
              `<option value="${row.id}">${row.label} · 票据 ${row.ticket_age_hours} 小时</option>`
          )
          .join("")
      : `<option value="">未找到微信配置</option>`;
    if (wanted) profile.value = wanted;
    if (!S.selectedProfile && S.profiles.length === 1) S.selectedProfile = profile.value;
  }
  $("chk_scheduler").checked = S.scheduler === "enabled";

  const stateText = {
    enabled: "已启用：每天 06:58 待命，07:00 提交",
    paused: "已暂停：任务还在，但不会自动提交",
    not_configured: "未配置：没有找到每日任务",
    unknown: "无法确认每日任务状态",
  }[S.scheduler];
  $("daily_state_note").textContent = stateText;
}

/* ============================================================
   动作
   ============================================================ */
async function checkAuth(loud = true) {
  if (S.authBusy) return false;
  S.authBusy = true;
  renderTomorrow();
  renderSettings();
  try {
    const result = await invoke("start_http_run", { until: "auth" });
    S.account = true;
    S.accountLabel = result?.account_label || "";
    S.authError = "";
    if (loud) toast("账号已核验", "ok");
    renderChrome();
    return true;
  } catch (e) {
    S.account = false;
    S.lastError = "";
    S.authError =
      (String((e && e.message) || e).match(/[A-Z][A-Z_]{2,60}/) || [])[0] ||
      "登录态不可用";
    if (loud) toast(authErrorText(e), "bad");
    return false;
  } finally {
    S.authBusy = false;
    renderTomorrow();
    renderSettings();
    renderOrders();
  }
}

async function queryAvailability() {
  if (S.queryBusy) return;
  S.queryBusy = true;
  S.selected = null;
  renderTomorrow();
  try {
    const r = await invoke("get_venue_availability", {
      sport: S.sport,
      venue: S.venue,
      date: isoOf(S.offset),
    });
    S.availability = r;
    S.lastError = "";
    S.selected = (r.slots || []).find((s) => !slotQuota(s).allFull) || null;
  } catch (e) {
    S.availability = null;
    S.lastError = explain(e, "查场次");
    toast(S.lastError, "bad");
  } finally {
    S.queryBusy = false;
    renderTomorrow();
  }
}

async function loadOrders() {
  if (!S.account) {
    S.orders = null;
    renderOrders();
    renderChrome();
    return;
  }
  try {
    const r = await invoke("get_my_orders");
    S.orders = Array.isArray(r.orders) ? r.orders : [];
    S.fp = r.account_fingerprint || S.fp;
  } catch (e) {
    S.orders = null;
    toast(explain(e, "读订单"), "bad");
  }
  renderOrders();
  renderChrome();
  renderTomorrow();
}

function askConfirm() {
  return new Promise((resolve) => {
    const dlg = $("dlg_confirm");
    const done = () => {
      dlg.removeEventListener("close", done);
      resolve(dlg.returnValue === "ok");
    };
    dlg.addEventListener("close", done);
    dlg.returnValue = "";
    dlg.showModal();
  });
}

async function bookSelected() {
  if (S.bookMode === "nextday") {
    S.offset = Math.min(2, S.offset + 1);
    S.availability = null;
    S.selected = null;
    S.bookMode = "book";
    renderTomorrow();
    queryAvailability();
    return;
  }
  const slot = S.selected;
  if (!slot || S.bookBusy) return;
  const info = dayInfo(S.offset);
  const { venue } = catalogVenue();

  $("cf_venue").textContent = venue.label;
  $("cf_date").textContent = `${info.y}年${info.m}月${info.d}日 ${info.wk}`;
  $("cf_time").textContent = `${slot.start}–${slot.end}`;
  if (!(await askConfirm())) return;

  S.bookBusy = true;
  const btn = $("btn_book");
  btn.disabled = true;
  btn.textContent = "提交中…";
  try {
    await invoke("save_task", {
      task: {
        task_id: crypto.randomUUID(),
        sport: S.sport,
        venue: S.venue,
        target_date: info.iso,
        fire_at: new Date().toISOString(),
        target_time_start: slot.start,
        target_time_end: slot.end,
        allow_time_fallback: false,
        time_fallback_order: "up_then_down",
        executor: "http",
      },
    });
    await invoke("start_http_run", { until: "submit" });
    toast("预约已核实，入场码可以取了", "ok");
    S.availability = null;
    await loadOrders();
  } catch (e) {
    toast(String(e.message || e), "bad");
  } finally {
    S.bookBusy = false;
    renderTomorrow();
  }
}

async function openQr(orderId, detailsId) {
  const dlg = $("dlg_qr");
  const frame = $("qr_frame");
  frame.innerHTML = `<p class="loading-line">正在生成…</p>`;
  dlg.showModal();
  try {
    const r = await invoke("get_entry_qr", {
      selected: true,
      accountFingerprint: S.fp,
      orderId,
      detailsId,
    });
    frame.innerHTML = `<img alt="入场码" src="data:image/png;base64,${r.png_base64}" />`;
  } catch (e) {
    frame.innerHTML = `<p class="empty-desc">入场码没取到：${String(
      e.message || e
    )}<br>请直接在小程序里打开，别重复预约。</p>`;
  }
}

async function cancelOrder(orderId, detailsId) {
  try {
    const r = await invoke("cancel_my_order", {
      confirmed: true,
      accountFingerprint: S.fp,
      orderId,
      detailsId,
    });
    if (r && r.ok) toast("已取消", "ok");
    else toast(r?.message || "取消结果未确认，请到小程序核对，勿重复取消", "bad");
  } catch (e) {
    toast(String(e.message || e), "bad");
  }
  await loadOrders();
}

async function saveDaily() {
  if (!S.account) {
    toast("先核验账号，再保存每日设置", "bad");
    return;
  }
  const start = $("sel_start").value;
  const end = $("sel_end").value;
  if (start >= end) {
    toast("结束时间要晚于开始时间", "bad");
    return;
  }
  const fallback = $("chk_fallback").checked;
  const btn = $("btn_save_daily");
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    await invoke("save_task", {
      task: {
        task_id: crypto.randomUUID(),
        sport: S.sport,
        venue: S.venue,
        target_date: isoOf(1),
        fire_at: new Date().toISOString(),
        target_time_start: start,
        target_time_end: end,
        allow_time_fallback: fallback,
        time_fallback_order: "up_then_down",
        executor: "http",
      },
    });
    const r = await invoke("save_daily_schedule", {
      allow_time_fallback: fallback,
      enabled: $("chk_scheduler").checked,
    });
    S.scheduler = r.scheduler_status || S.scheduler;
    await loadDaily();
    toast("每日设置已保存", "ok");
  } catch (e) {
    toast(String(e.message || e), "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "保存";
    renderSettings();
    renderChrome();
  }
}

async function loadDaily() {
  try {
    S.daily = await invoke("get_daily_schedule");
    if (S.daily?.task) {
      S.sport = S.daily.task.sport || S.sport;
      S.venue = S.daily.task.venue || S.venue;
      S.scheduler = S.daily.scheduler_status || S.scheduler;
      $("chk_scheduler").checked = !!S.daily.scheduler?.enabled;
    }
  } catch {
    S.daily = null;
  }
  renderDaily();
  renderChrome();
}

/* ============================================================
   长按取消 / 事件
   ============================================================ */
function bindHold(root) {
  root.addEventListener("pointerdown", (ev) => {
    const btn = ev.target.closest?.(".hold");
    if (!btn || btn.disabled) return;
    ev.preventDefault();
    btn.classList.add("is-holding");
    btn.dataset.timer = String(
      setTimeout(() => {
        btn.classList.remove("is-holding");
        cancelOrder(btn.dataset.holdCancel, btn.dataset.det);
      }, 950)
    );
  });
  const stop = (ev) => {
    const btn = ev.target.closest?.(".hold");
    if (!btn || !btn.dataset.timer) return;
    clearTimeout(+btn.dataset.timer);
    delete btn.dataset.timer;
    btn.classList.remove("is-holding");
  };
  root.addEventListener("pointerup", stop);
  root.addEventListener("pointerleave", stop, true);
  root.addEventListener("pointercancel", stop);
}

function setTab(next) {
  S.tab = next;
  if (next === "tomorrow") {
    fillSportSelect();
    fillVenueSelect();
  }
  document.querySelectorAll(".panel").forEach((p) => {
    p.hidden = p.id !== `panel_${next}`;
  });
  renderChrome();
}

function bind() {
  document.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });
  document.querySelectorAll("[data-goto]").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.goto));
  });

  $("day_switch").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".day");
    if (!btn) return;
    S.offset = +btn.dataset.offset;
    S.availability = null;
    S.selected = null;
    renderTomorrow();
    if (S.account) queryAvailability();
  });

  $("slots").addEventListener("click", (ev) => {
    const row = ev.target.closest(".slot");
    if (!row || row.disabled) return;
    S.selected = { start: row.dataset.start, end: row.dataset.end };
    renderTomorrow();
  });

  $("btn_auth").onclick = () => checkAuth(true).then((ok) => ok && queryAvailability());
  $("btn_auth2").onclick = () => checkAuth(true);
  $("sel_profile").onchange = async (ev) => {
    S.selectedProfile = ev.target.value;
    S.account = false;
    S.accountLabel = "";
    try {
      await invoke("select_profile", { id: S.selectedProfile });
      await checkAuth(true);
    } catch (e) {
      toast(String(e.message || e), "bad");
    }
  };
  $("btn_query").onclick = queryAvailability;
  $("btn_book").onclick = bookSelected;
  $("btn_refresh_orders").onclick = loadOrders;
  $("btn_save_daily").onclick = saveDaily;
  $("btn_qr_close").onclick = () => $("dlg_qr").close();

  $("sel_sport").onchange = (ev) => {
    const sport = SPORTS.find((s) => s.ocr_label === ev.target.value);
    if (!sport) return;
    S.sport = sport.ocr_label;
    S.venue = sport.venues[0].ocr_label;
    S.availability = null;
    S.selected = null;
    S.lastError = "";
    fillVenueSelect();
    renderSettings();
    renderTomorrow();
    if (S.account) queryAvailability();
  };
  $("sel_venue").onchange = (ev) => {
    S.venue = ev.target.value;
    S.availability = null;
    S.selected = null;
    S.lastError = "";
    fillSlotSelects($("sel_start").value, $("sel_end").value);
    renderSettings();
    renderTomorrow();
    if (S.account) queryAvailability();
  };
  $("sel_start").onchange = () => fillSlotSelects($("sel_start").value, "");
  $("sel_end").onchange = () => {};

  document.addEventListener("click", (ev) => {
    const qr = ev.target.closest?.("[data-qr]");
    if (qr) {
      openQr(qr.dataset.qr, qr.dataset.det);
      return;
    }
    const go = ev.target.closest?.("[data-goto]");
    if (go) setTab(go.dataset.goto);
  });

  bindHold(document.body);
}

/* ============================================================
   启动
   ============================================================ */
async function boot() {
  bind();
  if (CARD_VIEW) {
    document.body.classList.add("is-card");
    S.tab = ["orders", "settings"].includes(CARD_VIEW) ? CARD_VIEW : "tomorrow";
    document
      .querySelectorAll(".panel")
      .forEach((p) => (p.hidden = p.id !== `panel_${S.tab}`));
  }
  tick();
  setInterval(tick, 1000);
  renderChrome();

  try {
    const info = await invoke("get_app_info");
    S.demo = !!info.demo;
    $("demo_badge").hidden = !S.demo;
  } catch {
    S.demo = false;
  }
  try {
    S.profiles = await invoke("list_profiles");
    if (S.profiles.length > 1) {
      toast("检测到多个微信配置，请在「设置 → 账号」选择当前账号", "bad");
    }
  } catch {
    S.profiles = [];
  }
  try {
    S.scheduler = await invoke("get_scheduler_status");
  } catch {
    S.scheduler = "unknown";
  }
  await loadDaily();
  renderTomorrow();
  renderOrders();

  const ok = S.profiles.length > 1 ? false : await checkAuth(false);
  if (ok) {
    await loadOrders();
    await queryAvailability();
  }
  renderSettings();
}

boot();
