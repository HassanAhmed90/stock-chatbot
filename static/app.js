/* ============================================================================
 * Multi-Store Stock & Order Allocation System — Frontend (Vanilla JS)
 * ============================================================================ */

const state = {
  user: null,
  activeTab: null,
  adminOverview: null,
  dashFilters: {
    ord: ["New", "In process", "OK", "Hold"],
    rec: ["New", "In process", "OK", "Hold"],
    del: ["Dispatched", "Delivered", "Not delivered yet", "Returned", "Partially returned", "Cancelled by the consumer"],
    zones: ["Zone I", "Zone II", "Zone III", "Zone IV", "Zone V"],
  },
  statsPeriod: "All time",
  recZones: ["Zone I", "Zone II", "Zone III", "Zone IV", "Zone V"],
};

const ADMIN_TABS = [
  { id: "approvals", label: "✅ Approvals" },
  { id: "itr", label: "🔁 ITR (Transfers)" },
  { id: "dashboard", label: "📊 Zone Dashboard" },
  { id: "stats", label: "📈 Portal Statistics" },
  { id: "users", label: "👥 Users & Zones" },
  { id: "stock", label: "🏬 Stock (Daily Report)" },
  { id: "orders", label: "📄 Released Orders" },
  { id: "kb", label: "📚 Knowledge Base" },
];

const ZONE_TABS = [
  { id: "z_new", label: "➕ New Order" },
  { id: "z_mine", label: "📋 My Orders" },
  { id: "z_del", label: "🚚 Delivery" },
  { id: "z_itr", label: "🔁 ITR (Warehouses)" },
  { id: "z_stock", label: "🏬 Stock (all zones)" },
];

const RECOVERY_TABS = [
  { id: "r_new", label: "➕ New Recovery" },
  { id: "r_mine", label: "📋 Recoveries" },
];

function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtNum(v) {
  const n = Number(v || 0);
  return Number.isFinite(n) ? n.toLocaleString() : "0";
}

function renderMd(text) {
  let s = esc(text || "");
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  const lines = s.split("\n");
  let html = "";
  let inList = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("• ") || trimmed.startsWith("- ")) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${trimmed.slice(2)}</li>`;
    } else {
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      if (trimmed) html += `<p>${line}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return html;
}

function toast(msg, type = "info", durationMs = 4200) {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), durationMs);
}

const TOKEN_KEY = "stock_portal_token";
function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}
function setToken(t) {
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {}
}

async function api(url, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;

  const fetchOpts = { ...opts, headers };
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    fetchOpts.body = JSON.stringify(opts.json);
    delete fetchOpts.json;
  }
  const res = await fetch(url, fetchOpts);
  const refreshed = res.headers.get("X-Refreshed-Token");
  if (refreshed && getToken()) {
    setToken(refreshed);
  }
  let data = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    data = await res.json();
  }
  if (!res.ok) {
    if (res.status === 401 && state.user) {
      // Server rejected our session (expired, invalid, or account disabled)
      setToken(null);
      state.user = null;
      state.activeTab = null;
      renderRoot();
      setTimeout(() => {
        const u = document.getElementById("login-username");
        const p = document.getElementById("login-password");
        if (u) u.value = "";
        if (p) p.value = "";
        toast("🔒 Session expired. Please sign in again.", "warning", 6000);
      }, 300);
    }
    throw new Error((data && data.detail) || `Request failed (${res.status})`);
  }
  return data;
}

function renderTable(columns, rows, emptyText = "No data.") {
  if (!rows || rows.length === 0) {
    return `<div class="alert alert-info">${esc(emptyText)}</div>`;
  }
  const cols = columns && columns.length ? columns : Object.keys(rows[0]);
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
        <tbody>
          ${rows
            .map(
              (r) =>
                `<tr>${cols
                  .map((c) => {
                    const val = r[c];
                    const disp = val === null || val === undefined ? "" : val;
                    return `<td>${esc(disp)}</td>`;
                  })
                  .join("")}</tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

// ============================================================================
// AUTHENTICATION FLOW
// ============================================================================
async function bootstrap() {
  try {
    const me = await api("/api/auth/me");
    state.user = me.authenticated ? me.user : null;
  } catch {
    state.user = null;
  }
  renderRoot();
}

function renderRoot() {
  const authView = document.getElementById("auth-view");
  const appView = document.getElementById("app-view");

  if (!state.user || state.user.must_change) {
    authView.classList.remove("hidden");
    appView.classList.add("hidden");
    renderAuth();
    return;
  }

  authView.classList.add("hidden");
  appView.classList.remove("hidden");
  renderAppShell();
}

function renderAuth() {
  const loginForm = document.getElementById("login-form");
  const fpForm = document.getElementById("force-pw-form");
  const title = document.getElementById("auth-title");
  const sub = document.getElementById("auth-subtitle");
  document.getElementById("login-error").classList.add("hidden");
  document.getElementById("fp-error").classList.add("hidden");

  if (state.user && state.user.must_change) {
    title.textContent = "🔑 Set a new password";
    sub.textContent = `Signed in as ${state.user.username}`;
    loginForm.classList.add("hidden");
    fpForm.classList.remove("hidden");
  } else {
    title.textContent = "🔐 Sign in";
    sub.textContent = "Order Allocation System — zone users, recovery, and admin";
    loginForm.classList.remove("hidden");
    fpForm.classList.add("hidden");
  }
}

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("login-error");
  errEl.classList.add("hidden");
  try {
    const res = await api("/api/auth/login", {
      method: "POST",
      json: {
        username: document.getElementById("login-username").value,
        password: document.getElementById("login-password").value,
      },
    });
    setToken(res.token);
    state.user = res.user;
    state.activeTab = null;
    renderRoot();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

document.getElementById("force-pw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("fp-error");
  errEl.classList.add("hidden");
  try {
    const res = await api("/api/auth/force-password", {
      method: "POST",
      json: {
        new_password: document.getElementById("fp-new").value,
        confirm_password: document.getElementById("fp-confirm").value,
      },
    });
    state.user = res.user;
    toast("Password updated.", "success");
    renderRoot();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

async function doLogout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {}
  setToken(null);
  state.user = null;
  state.activeTab = null;
  renderRoot();
  // Always clear credentials on logout so form is blank
  setTimeout(() => {
    const u = document.getElementById("login-username");
    const p = document.getElementById("login-password");
    if (u) u.value = "";
    if (p) p.value = "";
  }, 100);
}

document.getElementById("logout-btn").addEventListener("click", doLogout);
document.getElementById("fp-logout-btn").addEventListener("click", doLogout);

document.getElementById("chg-pw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/auth/change-password", {
      method: "POST",
      json: {
        old_password: document.getElementById("chg-old").value,
        new_password: document.getElementById("chg-new").value,
      },
    });
    document.getElementById("chg-old").value = "";
    document.getElementById("chg-new").value = "";
    toast("Password updated.", "success");
  } catch (err) {
    toast(err.message, "error");
  }
});

// ============================================================================
// APP SHELL & TABS
// ============================================================================
async function renderAppShell() {
  const u = state.user;
  document.getElementById("sb-fullname").textContent = `👤 ${u.full_name || u.username}`;
  const roleDesc =
    u.role === "admin"
      ? "Admin"
      : `${u.zone || ""} ${u.role === "recovery" ? "Recovery" : "Orders"}`.trim();
  document.getElementById("sb-rolemeta").textContent = `${u.username} · ${roleDesc}`;

  const adminSb = document.getElementById("admin-sidebar-controls");
  const recSb = document.getElementById("recovery-sidebar-controls");
  const zoneSb = document.getElementById("zone-sidebar-controls");
  const title = document.getElementById("portal-title");
  const sub = document.getElementById("portal-subtitle");

  let tabs = [];
  if (u.role === "admin") {
    adminSb.classList.remove("hidden");
    recSb.classList.add("hidden");
    zoneSb.classList.add("hidden");
    title.textContent = "📦 Inventory & Order Allocation Assistant";
    sub.textContent = "Multi-store stock, zone-wise order approval, chatbot dispatch and ITR.";
    tabs = ADMIN_TABS;
    await refreshAdminSidebar();
  } else if (u.role === "recovery") {
    adminSb.classList.add("hidden");
    recSb.classList.remove("hidden");
    zoneSb.classList.add("hidden");
    title.textContent = "💰 Recovery Portal — all zones";
    sub.textContent = `Signed in as ${u.full_name || u.username} — payment recovery of every zone in one place.`;
    tabs = RECOVERY_TABS;
    await refreshRecoverySidebar();
  } else {
    adminSb.classList.add("hidden");
    recSb.classList.add("hidden");
    zoneSb.classList.remove("hidden");
    title.textContent = `🏷️ ${u.zone} Portal`;
    sub.textContent = `Signed in as ${u.full_name || u.username} — place orders for ${u.zone} cities and manage delivery / ITR.`;
    tabs = ZONE_TABS;
    await refreshZoneSidebar();
  }

  if (!state.activeTab || !tabs.some((t) => t.id === state.activeTab)) {
    state.activeTab = tabs[0].id;
  }

  const bar = document.getElementById("tab-bar");
  bar.innerHTML = tabs
    .map(
      (t) =>
        `<button class="tab-btn ${t.id === state.activeTab ? "active" : ""}" data-tab="${t.id}">${esc(t.label)}</button>`
    )
    .join("");
  bar.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.activeTab = btn.dataset.tab;
      bar.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
      loadActiveTab();
    });
  });

  loadActiveTab();
}

async function refreshRecoverySidebar() {
  try {
    const ov = await api("/api/recovery/overview");
    document.getElementById("sb-rec-total").textContent = ov.total ?? 0;
    document.getElementById("sb-rec-pending").textContent = ov.pending ?? 0;
    document.getElementById("sb-rec-completed").textContent = ov.completed ?? 0;
  } catch (err) {
    toast(err.message, "error");
  }
}

document.getElementById("sb-rec-refresh-btn").addEventListener("click", async () => {
  await refreshRecoverySidebar();
  loadActiveTab();
  toast("Data refreshed", "success");
});

function fillDeliveryStats(prefix, dc) {
  const d = dc || {};
  document.getElementById(`${prefix}-total`).textContent      = d.total         ?? 0;
  document.getElementById(`${prefix}-awaiting`).textContent   = d.awaiting      ?? 0;
  document.getElementById(`${prefix}-dispatched`).textContent = d.dispatched    ?? 0;
  document.getElementById(`${prefix}-delivered`).textContent  = d.delivered     ?? 0;
  document.getElementById(`${prefix}-notyet`).textContent     = d.not_yet       ?? 0;
  document.getElementById(`${prefix}-returned`).textContent   = d.returned      ?? 0;
  document.getElementById(`${prefix}-partret`).textContent    = d.part_returned ?? 0;
  document.getElementById(`${prefix}-cancelled`).textContent  = d.cancelled     ?? 0;
}

async function refreshZoneSidebar() {
  try {
    const ov = await api("/api/zone/overview");
    const oc = ov.order_counts || {};
    document.getElementById("sb-z-orders-total").textContent = oc.total ?? 0;
    document.getElementById("sb-z-cnt-new").textContent       = oc["New"]        ?? 0;
    document.getElementById("sb-z-cnt-inprocess").textContent = oc["In process"] ?? 0;
    document.getElementById("sb-z-cnt-ok").textContent        = oc["OK"]         ?? 0;
    document.getElementById("sb-z-cnt-hold").textContent      = oc["Hold"]       ?? 0;
    document.getElementById("sb-z-cnt-rejected").textContent  = oc["Rejected"]   ?? 0;
    document.getElementById("sb-z-cnt-cancelled").textContent = oc["Cancelled"]  ?? 0;
    fillDeliveryStats("sb-z-del", ov.delivery_counts);
  } catch (err) {
    toast(err.message, "error");
  }
}

document.getElementById("sb-z-refresh-btn").addEventListener("click", async () => {
  await refreshZoneSidebar();
  loadActiveTab();
  toast("Data refreshed", "success");
});

async function refreshAdminSidebar() {
  try {
    const ov = await api("/api/admin/overview");
    state.adminOverview = ov;
    document.getElementById("safety-slider").value = ov.safety_pct;
    document.getElementById("safety-val-label").textContent = `${ov.safety_pct}%`;
    document.getElementById("safety-caption-pct").textContent = ov.safety_pct;
    document.getElementById("sb-stores").textContent = ov.active_stores;
    document.getElementById("sb-units").textContent = `${fmtNum(ov.total_units)} units`;

    // Order counts panel
    const oc = ov.order_counts || {};
    document.getElementById("sb-orders-total").textContent = oc.total ?? 0;
    document.getElementById("sb-cnt-new").textContent       = oc["New"]        ?? 0;
    document.getElementById("sb-cnt-inprocess").textContent = oc["In process"] ?? 0;
    document.getElementById("sb-cnt-ok").textContent        = oc["OK"]         ?? 0;
    document.getElementById("sb-cnt-hold").textContent      = oc["Hold"]       ?? 0;
    document.getElementById("sb-cnt-rejected").textContent  = oc["Rejected"]   ?? 0;
    document.getElementById("sb-cnt-cancelled").textContent = oc["Cancelled"]  ?? 0;

    // Recovery counts panel
    const rc = ov.recovery_counts || {};
    document.getElementById("sb-adm-rec-total").textContent     = rc.total     ?? 0;
    document.getElementById("sb-adm-rec-pending").textContent   = rc.pending   ?? 0;
    document.getElementById("sb-adm-rec-completed").textContent = rc.completed ?? 0;

    // Delivery counts panel
    fillDeliveryStats("sb-adm-del", ov.delivery_counts);
  } catch (err) {
    toast(err.message, "error");
  }
}

let safetyTimer = null;
document.getElementById("safety-slider").addEventListener("input", (e) => {
  const v = e.target.value;
  document.getElementById("safety-val-label").textContent = `${v}%`;
  document.getElementById("safety-caption-pct").textContent = v;
  clearTimeout(safetyTimer);
  safetyTimer = setTimeout(async () => {
    try {
      await api("/api/admin/safety", { method: "POST", json: { safety_pct: Number(v) } });
      await refreshAdminSidebar();
      if (["chat", "approvals", "itr", "stock"].includes(state.activeTab)) {
        loadActiveTab();
      }
    } catch (err) {
      toast(err.message, "error");
    }
  }, 250);
});

document.getElementById("sb-refresh-btn").addEventListener("click", async () => {
  await refreshAdminSidebar();
  loadActiveTab();
  toast("Data refreshed", "success");
});

async function loadActiveTab() {
  const root = document.getElementById("tab-content");
  root.innerHTML = `<div class="card muted">Loading...</div>`;
  try {
    switch (state.activeTab) {
      case "approvals": return await renderApprovalsTab(root);
      case "itr": return await renderAdminItrTab(root);
      case "dashboard": return await renderDashboardTab(root);
      case "stats": return await renderStatsTab(root);
      case "users": return await renderUsersTab(root);
      case "stock": return await renderAdminStockTab(root);
      case "orders": return await renderOrdersHistoryTab(root);
      case "kb": return await renderKbTab(root);
      case "z_new": return await renderZoneNewOrderTab(root);
      case "z_mine": return await renderZoneMyOrdersTab(root);
      case "z_del": return await renderZoneDeliveryTab(root);
      case "z_itr": return await renderZoneItrTab(root);
      case "z_stock": return await renderZoneStockTab(root);
      case "r_new": return await renderRecoveryNewTab(root);
      case "r_mine": return await renderRecoveryListTab(root);
    }
  } catch (err) {
    root.innerHTML = `<div class="alert alert-error">${esc(err.message)}</div>`;
  }
}

// ============================================================================
// ADMIN TAB 1: APPROVALS
// ============================================================================
async function renderApprovalsTab(root) {
  const data = await api("/api/admin/approvals");
  const items = data.items || [];
  let html = `
    <div class="card">
      <h3 class="card-title">✅ Approvals</h3>
      <p class="muted">${items.length} request(s) need a decision. Stock is deducted only when an <strong>Order</strong> is approved.</p>
    </div>
  `;

  if (!items.length) {
    html += `<div class="alert alert-info">Nothing is waiting.</div>`;
    root.innerHTML = html;
    return;
  }

  for (const r of items) {
    const what = r.is_recovery ? `Recovery Rs. ${fmtNum(r.amount)}` : `${r.item_name} × ${fmtNum(r.qty)}`;
    const title = `${r.status_label} · #${r.id} · ${r.zone} · ${r.subgroup} · ${r.party} · ${what} → ${r.city}`;
    let body = `
      <p><strong>Requested by:</strong> ${esc(r.username)} on ${esc(r.created_at)}<br>
      <strong>Group:</strong> ${esc(r.grp)} · <strong>City:</strong> ${esc(r.city)} · <strong>Type:</strong> ${esc(r.subgroup)}</p>
    `;
    if (r.is_recovery) {
      body += `<p><strong>Amount:</strong> Rs. ${fmtNum(r.amount)} · <strong>Ref No.:</strong> ${esc(r.ref_no || "—")} · <strong>Note:</strong> ${esc(r.delivery_note || "—")}</p>`;
    }
    if (r.state === "Hold" && r.admin_note) {
      body += `<div class="alert alert-warning">On hold: ${esc(r.admin_note)}</div>`;
    }
    if (r.is_order) {
      const al = r.allocation || {};
      body += `<p><strong>${esc(r.city)}</strong> belongs to <strong>${esc(al.dest_zone)}</strong> · stock available in ${esc(al.dest_zone)} stores: <strong>${fmtNum(al.zone_avail)}</strong> units</p>`;
      if (!al.has_any_stock) {
        body += `<div class="alert alert-error">This product is not in any store.</div>`;
      } else if (!al.zone_stores || !al.zone_stores.length) {
        body += `<div class="alert alert-warning">${esc(al.dest_zone)} warehouse has no stock of this product. The zone must bring it through an ITR (🔁 ITR tab). You can hold or reject this order for now.</div>`;
      } else if (al.plan) {
        const p = al.plan;
        if (p.status === "FULL") {
          body += `<div class="alert alert-success">Full quantity available (${fmtNum(p.fulfilled_qty)} units) from ${esc(al.zone_stores.join(", "))}.</div>`;
        } else if (p.status === "PARTIAL") {
          body += `<div class="alert alert-warning">These stores can release only ${fmtNum(p.fulfilled_qty)} of ${fmtNum(p.requested_qty)} units. Approve the available quantity or wait for the zone's ITR.</div>`;
        } else {
          body += `<div class="alert alert-error">No stock available in the selected store(s).</div>`;
        }
        const planRows = (p.allocations || []).map((a) => ({
          Store: a.Store_Name,
          "Dispatch Qty": a.Dispatch_Qty,
          Remaining: a.Remaining_Stock,
          "Safety Buffer": a.Safety_Buffer,
        }));
        body += renderTable(["Store", "Dispatch Qty", "Remaining", "Safety Buffer"], planRows);
      } else if (al.error) {
        body += `<div class="alert alert-error">${esc(al.error)}</div>`;
      }
    } else {
      body += `<p class="muted small">${esc(r.subgroup)} is only tracked — OK does not touch stock.</p>`;
    }

    const canOk = !r.is_order || (r.allocation && r.allocation.can_approve);
    html += `
      <details class="accordion">
        <summary><span>${esc(title)}</span><span class="muted small">Open</span></summary>
        <div class="accordion-body" data-req-id="${r.id}">
          ${body}
          <label class="field">
            <span>Note (required for Hold / Reject)</span>
            <input type="text" class="appr-note" placeholder="Optional for OK; required for Hold / Reject" />
          </label>
          <div class="grid-3">
            <button class="btn btn-primary appr-ok" ${canOk ? "" : "disabled"}>✅ OK</button>
            <button class="btn btn-secondary appr-hold">⛔ Hold</button>
            <button class="btn btn-danger appr-reject">❌ Reject</button>
          </div>
        </div>
      </details>
    `;
  }

  root.innerHTML = html;
  root.querySelectorAll(".accordion-body[data-req-id]").forEach((box) => {
    const id = box.dataset.reqId;
    const note = () => box.querySelector(".appr-note").value;
    box.querySelector(".appr-ok").addEventListener("click", async () => {
      try {
        const res = await api(`/api/admin/approvals/${id}/approve`, { method: "POST", json: { note: note() } });
        toast(res.message, "success");
        await refreshAdminSidebar();
        await renderApprovalsTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    box.querySelector(".appr-hold").addEventListener("click", async () => {
      try {
        const res = await api(`/api/admin/approvals/${id}/hold`, { method: "POST", json: { note: note() } });
        toast(res.message, "warning");
        await renderApprovalsTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    box.querySelector(".appr-reject").addEventListener("click", async () => {
      try {
        const res = await api(`/api/admin/approvals/${id}/reject`, { method: "POST", json: { note: note() } });
        toast(res.message, "info");
        await renderApprovalsTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
}

// ============================================================================
// ADMIN TAB 3: ITR (TRANSFERS)
// ============================================================================
async function renderAdminItrTab(root) {
  const data = await api("/api/admin/itr");
  const pending = data.pending || [];
  const transit = data.transit || [];
  const history = data.history || [];

  let html = `
    <div class="card">
      <h3 class="card-title">🔁 ITR — Inventory Transfer Requests</h3>
      <p class="muted">Approving an ITR takes stock out of the sending warehouse right away (<strong>In transit</strong>). Stock is added to the receiving warehouse when marked <strong>Received</strong>.</p>
    </div>
    <div class="card">
      <h3 class="card-title">⏳ Waiting for a decision (${pending.length})</h3>
      ${!pending.length ? `<div class="alert alert-info">No ITR is waiting.</div>` : ""}
      ${pending
        .map((r) => {
          const srcs = r.sources || [];
          const defSrc = srcs.find((s) => s.warehouse === r.from_store) || srcs[0];
          const maxQty = defSrc ? Math.max(defSrc.available, 1) : 1;
          const initQty = defSrc && defSrc.available > 0 ? Math.min(r.qty, defSrc.available) : 1;
          return `
            <details class="accordion">
              <summary><span>#${r.id} · ${esc(r.to_zone)} · ${esc(r.item_name)} × ${fmtNum(r.qty)} · ${esc(r.from_store)} → ${esc(r.to_store)}</span></summary>
              <div class="accordion-body" data-itr-id="${r.id}">
                <p><strong>Requested by:</strong> ${esc(r.username)} on ${esc(r.created_at)} · <strong>Note:</strong> ${esc(r.note || "—")}</p>
                ${renderTable(["Warehouse", "Zone", "Stock", "Available for ITR"], r.matrix)}
                <div class="grid-2">
                  <label class="field">
                    <span>Send from</span>
                    <select class="itr-src">
                      ${srcs.map((s) => `<option value="${esc(s.warehouse)}" data-avail="${s.available}" ${s.warehouse === (defSrc && defSrc.warehouse) ? "selected" : ""}>${esc(s.warehouse)} — available ${fmtNum(s.available)}</option>`).join("")}
                    </select>
                  </label>
                  <label class="field">
                    <span>Quantity to send</span>
                    <input type="number" class="itr-qty" min="1" max="${maxQty}" value="${initQty}" />
                  </label>
                </div>
                <label class="field">
                  <span>Note (required for Reject)</span>
                  <input type="text" class="itr-note" />
                </label>
                <div class="grid-2">
                  <button class="btn btn-primary itr-approve" ${defSrc && defSrc.available <= 0 ? "disabled" : ""}>✅ Approve &amp; send</button>
                  <button class="btn btn-danger itr-reject">❌ Reject</button>
                </div>
              </div>
            </details>
          `;
        })
        .join("")}
    </div>

    <div class="card">
      <h3 class="card-title">🚛 In transit (${transit.length})</h3>
      ${!transit.length ? `<div class="alert alert-info">Nothing is on the way.</div>` : ""}
      ${transit
        .map(
          (r) => `
            <details class="accordion">
              <summary><span>#${r.id} · ${esc(r.item_name)} × ${fmtNum(r.approved_qty)} · ${esc(r.from_store)} → ${esc(r.to_store)}</span></summary>
              <div class="accordion-body" data-transit-id="${r.id}">
                <div class="grid-2">
                  <button class="btn btn-primary itr-rcv">📥 Mark received</button>
                  <button class="btn btn-secondary itr-cancel">🚫 Cancel (stock goes back)</button>
                </div>
              </div>
            </details>
          `
        )
        .join("")}
    </div>

    <div class="card">
      <h3 class="card-title">📜 All ITRs</h3>
      ${renderTable(
        ["id", "created_at", "item_name", "from_store", "to_store", "qty", "approved_qty", "status_label", "note", "admin_note"],
        history,
        "No ITR yet."
      )}
    </div>
  `;

  root.innerHTML = html;

  root.querySelectorAll(".accordion-body[data-itr-id]").forEach((box) => {
    const id = box.dataset.itrId;
    const sel = box.querySelector(".itr-src");
    const qtyIn = box.querySelector(".itr-qty");
    const okBtn = box.querySelector(".itr-approve");
    sel.addEventListener("change", () => {
      const av = Number(sel.selectedOptions[0].dataset.avail || 0);
      qtyIn.max = Math.max(av, 1);
      if (Number(qtyIn.value) > av && av > 0) qtyIn.value = av;
      okBtn.disabled = av <= 0;
    });
    okBtn.addEventListener("click", async () => {
      try {
        const res = await api(`/api/admin/itr/${id}/approve`, {
          method: "POST",
          json: {
            from_store: sel.value,
            send_qty: Number(qtyIn.value),
            note: box.querySelector(".itr-note").value,
          },
        });
        toast(res.message, "success");
        await refreshAdminSidebar();
        await renderAdminItrTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    box.querySelector(".itr-reject").addEventListener("click", async () => {
      try {
        const res = await api(`/api/admin/itr/${id}/reject`, {
          method: "POST",
          json: { note: box.querySelector(".itr-note").value },
        });
        toast(res.message, "info");
        await renderAdminItrTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });

  root.querySelectorAll(".accordion-body[data-transit-id]").forEach((box) => {
    const id = box.dataset.transitId;
    box.querySelector(".itr-rcv").addEventListener("click", async () => {
      try {
        const res = await api(`/api/admin/itr/${id}/receive`, { method: "POST" });
        toast(res.message, "success");
        await refreshAdminSidebar();
        await renderAdminItrTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    box.querySelector(".itr-cancel").addEventListener("click", async () => {
      try {
        const res = await api(`/api/admin/itr/${id}/cancel`, { method: "POST" });
        toast(res.message, "warning");
        await refreshAdminSidebar();
        await renderAdminItrTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
}

// ============================================================================
// ADMIN TAB 4: ZONE DASHBOARD
// ============================================================================
async function renderDashboardTab(root) {
  const f = state.dashFilters;
  const qs = new URLSearchParams({
    ord_states: f.ord.join(","),
    rec_states: f.rec.join(","),
    del_states: f.del.join(","),
    zones: f.zones.join(","),
  });
  const d = await api(`/api/admin/dashboard?${qs}`);
  if (d.empty) {
    root.innerHTML = `<div class="alert alert-info">No requests yet.</div>`;
    return;
  }

  const meta = d.meta;
  const pillGroup = (cls, options, selected) =>
    `<div class="row-gap" style="margin-bottom:8px">${options
      .map(
        (opt) =>
          `<label class="pill-check"><input type="checkbox" class="${cls}" value="${esc(opt)}" ${selected.includes(opt) ? "checked" : ""}/> ${esc(opt)}</label>`
      )
      .join("")}</div>`;

  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">📊 Order Status by Zone</h3>
      <p class="muted small">New = waiting &lt; ${d.new_window_min} min · In process = waiting longer · OK = approved · Hold = payment issue.</p>
      ${renderTable(d.by_status.columns, d.by_status.rows)}
    </div>

    <div class="card">
      <h3 class="card-title">Zone × Group × Subgroup</h3>
      <div style="display:flex;flex-direction:column;gap:20px">
        <div>
          <strong>Order — statuses</strong>
          ${renderTable(d.zone_group_order.columns, d.zone_group_order.rows)}
        </div>
        <div>
          <strong>Recovery — statuses</strong>
          ${renderTable(d.zone_group_recovery.columns, d.zone_group_recovery.rows)}
        </div>
        <div>
          <strong>Delivery — statuses</strong>
          ${renderTable(d.zone_group_delivery.columns, d.zone_group_delivery.rows)}
        </div>
      </div>
    </div>

    <div style="display:flex;flex-direction:column;gap:14px">
      <div class="card">
        <h3 class="card-title">Delivery status by zone</h3>
        ${renderTable(d.delivery_by_zone.columns, d.delivery_by_zone.rows)}
      </div>
      <div class="card">
        <h3 class="card-title">Recovered amount by zone (OK entries)</h3>
        ${renderTable(d.recovered_by_zone.columns, d.recovered_by_zone.rows)}
      </div>
    </div>

    <details class="accordion" open>
      <summary><span>All requests of all zones (${d.all_requests.length})</span></summary>
      <div class="accordion-body">
        ${pillGroup("df-zone", meta.zones, f.zones)}
        ${renderTable(null, d.all_requests, "No matching requests.")}
      </div>
    </details>
  `;

  const bindChecks = (cls, key) => {
    root.querySelectorAll(`.${cls}`).forEach((cb) => {
      cb.addEventListener("change", () => {
        state.dashFilters[key] = Array.from(root.querySelectorAll(`.${cls}:checked`)).map((el) => el.value);
        renderDashboardTab(root);
      });
    });
  };
  bindChecks("df-zone", "zones");
}

// ============================================================================
// ADMIN TAB 5: PORTAL STATISTICS
// ============================================================================
async function renderStatsTab(root) {
  const data = await api(`/api/admin/stats?period=${encodeURIComponent(state.statsPeriod)}`);
  const periods = ["All time", "Today", "Last 7 days", "Last 30 days"];

  let html = `
    <div class="card space-between">
      <h3 class="card-title" style="margin:0">📈 Statistics of all portals</h3>
      <select id="stats-period" style="width:200px">
        ${periods.map((p) => `<option value="${esc(p)}" ${p === state.statsPeriod ? "selected" : ""}>${esc(p)}</option>`).join("")}
      </select>
    </div>
  `;
  if (data.empty) {
    root.innerHTML = html + `<div class="alert alert-info">No activity yet.</div>`;
  } else {
    const k = data.kpis;
    html += `
      <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">Orders</div><div class="kpi-value">${fmtNum(k.orders)}</div></div>
        <div class="kpi-card"><div class="kpi-label">Units released</div><div class="kpi-value">${fmtNum(k.units_released)}</div></div>
        <div class="kpi-card"><div class="kpi-label">Dispatched / delivered</div><div class="kpi-value">${fmtNum(k.dispatched)} / ${fmtNum(k.delivered)}</div></div>
        <div class="kpi-card"><div class="kpi-label">Recovery entries</div><div class="kpi-value">${fmtNum(k.recovery_entries)}</div></div>
        <div class="kpi-card"><div class="kpi-label">Recovered (OK)</div><div class="kpi-value">Rs. ${fmtNum(Math.round(k.recovered_ok))}</div></div>
      </div>
      <div class="card"><h3 class="card-title">All portals at a glance</h3>${renderTable(data.glance.columns, data.glance.rows)}</div>
      <div class="card"><h3 class="card-title">Order portals — units and delivery</h3>${renderTable(data.order_portals.columns, data.order_portals.rows)}</div>
      <div class="card"><h3 class="card-title">Recovery portal — by zone (Rs.)</h3>${renderTable(data.recovery_portals.columns, data.recovery_portals.rows)}</div>
      <div class="card"><h3 class="card-title">Users</h3>${renderTable(data.users_activity.columns, data.users_activity.rows)}</div>
    `;
    root.innerHTML = html;
  }

  document.getElementById("stats-period").addEventListener("change", (e) => {
    state.statsPeriod = e.target.value;
    renderStatsTab(root);
  });
}

// ============================================================================
// ADMIN TAB 6: USERS & ZONES
// ============================================================================
async function renderUsersTab(root) {
  const [uData, zData, resetPrev] = await Promise.all([
    api("/api/admin/users"),
    api("/api/admin/zones"),
    api("/api/admin/reset/preview"),
  ]);

  const manageable = (uData.users || []).filter((u) => u.username !== state.user.username);
  const selectedZone = zData.zones[0];

  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">👥 Users</h3>
      ${renderTable(["username", "full_name", "role", "zone", "active"], uData.users)}
      <div class="grid-2" style="margin-top:14px">
        <div class="card" style="margin:0">
          <h4 class="card-title">Add a zone user</h4>
          <form id="create-user-form">
            <label class="field"><span>Username</span><input type="text" id="cu-u" required /></label>
            <label class="field"><span>Full name</span><input type="text" id="cu-n" required /></label>
            <label class="field"><span>Temporary password</span><input type="password" id="cu-p" minlength="6" required /></label>
            <label class="field"><span>Zone (Order portal only)</span>
              <select id="cu-z">${uData.zones.map((z) => `<option value="${esc(z)}">${esc(z)}</option>`).join("")}</select>
            </label>
            <label class="field"><span>Portal</span>
              <select id="cu-r">
                <option value="user">Order portal</option>
                <option value="recovery">Recovery portal (all zones)</option>
              </select>
            </label>
            <button type="submit" class="btn btn-primary btn-block">Create user</button>
          </form>
        </div>

        <div class="card" style="margin:0">
          <h4 class="card-title">Manage a user</h4>
          ${manageable.length ? `
            <label class="field"><span>User</span>
              <select id="mu-who">${manageable.map((u) => `<option value="${esc(u.username)}">${esc(u.username)} (${esc(u.role)})</option>`).join("")}</select>
            </label>
            <div id="mu-controls"></div>
          ` : `<p class="muted">No other users.</p>`}
        </div>
      </div>
    </div>

    <div class="card">
      <div class="space-between">
        <div>
          <h3 class="card-title">🗺️ Cities of each zone</h3>
          <p class="muted small">Default: Zone I = Sindh + Hub · Zone II = Lower Punjab · Zone III = Central Punjab · Zone IV = Upper Punjab · Zone V = KPK.</p>
        </div>
        <button id="seed-cities-btn" class="btn btn-secondary btn-sm">🔄 Add default cities</button>
      </div>
      ${zData.unassigned.length ? `<div class="alert alert-info">Not in any zone yet: ${esc(zData.unassigned.join(", "))}</div>` : ""}
      <div class="grid-2">
        <label class="field"><span>Select Zone</span>
          <select id="zc-zone">${zData.zones.map((z) => `<option value="${esc(z)}" ${z === selectedZone ? "selected" : ""}>${esc(z)}</option>`).join("")}</select>
        </label>
        <label class="field"><span>Add custom city (optional)</span>
          <input type="text" id="zc-new-city" placeholder="City not in the list? Type and add" />
        </label>
      </div>
      <div id="zc-checks" class="check-grid"></div>
      <button id="zc-save-btn" class="btn btn-primary" style="margin-bottom:14px">💾 Save cities</button>
      ${renderTable(["Zone", "Cities", "List"], zData.summary)}
    </div>

    <div class="card">
      <h3 class="card-title">🧹 Reset — delete all orders</h3>
      <p class="muted">Deletes every order, recovery, delivery, return and ITR of all portals, and every released order and ITR file. Users, zones and cities are <strong>not</strong> touched.</p>
      <p>Right now: <strong>${fmtNum(resetPrev.requests_count)}</strong> portal entries and <strong>${fmtNum(resetPrev.files_count)}</strong> released order file(s).</p>
      <label class="check-item" style="margin-bottom:10px"><input type="checkbox" id="rst-backup" checked /> Keep a backup copy (recommended)</label>
      <div class="row-gap">
        <input type="text" id="rst-confirm" placeholder="Type DELETE ALL to confirm" style="max-width:260px" />
        <button id="rst-btn" class="btn btn-danger" disabled>🗑️ Delete all orders</button>
      </div>
    </div>
  `;

  document.getElementById("create-user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/admin/users", {
        method: "POST",
        json: {
          username: document.getElementById("cu-u").value,
          full_name: document.getElementById("cu-n").value,
          password: document.getElementById("cu-p").value,
          zone: document.getElementById("cu-z").value,
          role: document.getElementById("cu-r").value,
        },
      });
      toast(res.message, "success");
      await renderUsersTab(root);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  if (manageable.length) {
    const muWho = document.getElementById("mu-who");
    const renderManageUser = () => {
      const u = manageable.find((x) => x.username === muWho.value);
      const box = document.getElementById("mu-controls");
      box.innerHTML = `
        ${u.role === "user" ? `
          <label class="field"><span>Zone</span>
            <select id="mu-zone">${uData.zones.map((z) => `<option value="${esc(z)}" ${z === u.zone ? "selected" : ""}>${esc(z)}</option>`).join("")}</select>
          </label>` : `<p class="muted small">Recovery portal: covers all zones.</p>`}
        <label class="check-item" style="margin-bottom:10px">
          <input type="checkbox" id="mu-active" ${u.active ? "checked" : ""} /> Account active
        </label>
        <button id="mu-save-btn" class="btn btn-secondary btn-sm" style="margin-bottom:12px">Save changes</button>
        <form id="mu-reset-pw" class="row-gap">
          <input type="password" id="mu-np" placeholder="New temporary password" minlength="6" required style="flex:1" />
          <button type="submit" class="btn btn-outline btn-sm">Reset password</button>
        </form>
      `;
      document.getElementById("mu-save-btn").addEventListener("click", async () => {
        try {
          const payload = { active: document.getElementById("mu-active").checked ? 1 : 0 };
          const zEl = document.getElementById("mu-zone");
          if (zEl) payload.zone = zEl.value;
          const res = await api(`/api/admin/users/${encodeURIComponent(u.username)}`, { method: "PATCH", json: payload });
          toast(res.message, "success");
          await renderUsersTab(root);
        } catch (err) {
          toast(err.message, "error");
        }
      });
      document.getElementById("mu-reset-pw").addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const res = await api(`/api/admin/users/${encodeURIComponent(u.username)}/reset-password`, {
            method: "POST",
            json: { new_password: document.getElementById("mu-np").value },
          });
          toast(res.message, "success");
          document.getElementById("mu-np").value = "";
        } catch (err) {
          toast(err.message, "error");
        }
      });
    };
    muWho.addEventListener("change", renderManageUser);
    renderManageUser();
  }

  const zcSel = document.getElementById("zc-zone");
  const renderCityBoxes = () => {
    const info = zData.by_zone[zcSel.value] || { available: [], assigned: [] };
    const assignedSet = new Set(info.assigned);
    document.getElementById("zc-checks").innerHTML = info.available
      .map((c) => `<label class="check-item"><input type="checkbox" class="zc-cb" value="${esc(c)}" ${assignedSet.has(c) ? "checked" : ""} /> ${esc(c)}</label>`)
      .join("");
  };
  zcSel.addEventListener("change", renderCityBoxes);
  renderCityBoxes();

  document.getElementById("seed-cities-btn").addEventListener("click", async () => {
    const res = await api("/api/admin/zones/seed-defaults", { method: "POST" });
    toast(res.message, "success");
    await renderUsersTab(root);
  });

  document.getElementById("zc-save-btn").addEventListener("click", async () => {
    try {
      const cities = Array.from(root.querySelectorAll(".zc-cb:checked")).map((cb) => cb.value);
      const res = await api(`/api/admin/zones/${encodeURIComponent(zcSel.value)}/cities`, {
        method: "PUT",
        json: { cities, new_city: document.getElementById("zc-new-city").value },
      });
      toast(res.message, "success");
      await renderUsersTab(root);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  const rstIn = document.getElementById("rst-confirm");
  const rstBtn = document.getElementById("rst-btn");
  rstIn.addEventListener("input", () => {
    rstBtn.disabled = rstIn.value.trim() !== "DELETE ALL";
  });
  rstBtn.addEventListener("click", async () => {
    try {
      const res = await api("/api/admin/reset", {
        method: "POST",
        json: {
          confirm_text: rstIn.value,
          keep_backup: document.getElementById("rst-backup").checked,
        },
      });
      toast(res.message, "success");
      await refreshAdminSidebar();
      await renderUsersTab(root);
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

// ============================================================================
// ADMIN TAB 7: STOCK (DAILY REPORT)
// ============================================================================
async function renderAdminStockTab(root) {
  await refreshAdminSidebar();
  const ov = state.adminOverview;
  const storeNames = ov.store_names || [];
  const reportFiles = ov.report_files || [];
  const loadedFiles = ov.loaded_files || [];

  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">🏬 Stock — from the daily Excel report</h3>
      <p class="muted">Upload the daily stock report of each city (.xlsx / .xlsm). Files are saved in <code>data/</code> and combined automatically.</p>
      <form id="stock-upload-form" class="row-gap">
        <input type="file" id="stock-files-input" accept=".xlsx,.xlsm" multiple style="flex:1" />
        <button type="submit" class="btn btn-primary">📤 Upload Report(s)</button>
      </form>
      <div id="upload-feedback" style="margin-top:10px"></div>
    </div>

    ${ov.load_error ? `<div class="alert alert-error">The stored report could not be read: ${esc(ov.load_error)}</div>` : ""}

    <div class="card">
      <h3 class="card-title">📁 Loaded Report Files</h3>
      ${!loadedFiles.length && !reportFiles.length
        ? `<div class="alert alert-info">No stock report has been uploaded yet. Upload today's Excel file above.</div>`
        : loadedFiles
            .map(
              (fi) => `
              <div class="space-between" style="padding:8px 0;border-bottom:1px solid var(--border)">
                <div>
                  📅 <code>${esc(fi.name)}</code> — stock as on <strong>${esc(fi.date || "unknown date")}</strong> ·
                  ${esc((fi.stores && fi.stores.join(", ")) || "no office used (newer file exists)")} · uploaded ${esc(fi.uploaded_at)}
                  ${fi.age_days > 0 && fi.stores && fi.stores.length ? `<div class="alert alert-warning" style="margin-top:6px;margin-bottom:0">${esc(fi.stores.join(", "))} stock is ${fi.age_days} day(s) old. Upload today's file to refresh.</div>` : ""}
                </div>
                <a class="btn btn-outline btn-sm" href="/api/stock/download/${encodeURIComponent(fi.name)}" data-auth-dl="${esc(fi.name)}">📥 Download Excel</a>
              </div>
            `
            )
            .join("")}
      ${ov.deducted_units ? `<p class="muted small" style="margin-top:8px">ℹ️ Figures below already subtract ${fmtNum(ov.deducted_units)} units released since report upload.</p>` : ""}
      ${(ov.warnings || []).map((w) => `<div class="alert alert-warning" style="margin-top:6px">Report check: ${esc(w)}</div>`).join("")}

      ${reportFiles.length ? `
        <details class="accordion" style="margin-top:12px">
          <summary><span>🗑️ Remove Excel File from data Folder (${reportFiles.length} file(s))</span></summary>
          <div class="accordion-body">
            <label class="field">
              <span>Select Excel file to remove</span>
              <select id="del-rep-select">
                ${reportFiles.map((rf) => `<option value="${esc(rf.filename)}">${esc(rf.filename)} (${rf.size_kb} KB, ${esc(rf.modified_at)})</option>`).join("")}
              </select>
            </label>
            <div class="row-gap" style="margin-bottom:10px">
              <label class="check-item"><input type="checkbox" id="del-rep-confirm" /> I confirm permanent removal</label>
              <button id="del-rep-btn" class="btn btn-danger btn-sm" disabled>🗑️ Remove File</button>
            </div>
            ${reportFiles.length > 1 ? `
              <hr style="border:none;border-top:1px solid var(--border);margin:10px 0" />
              <div class="row-gap">
                <label class="check-item"><input type="checkbox" id="del-all-confirm" /> Delete ALL Excel report files in data/</label>
                <button id="del-all-btn" class="btn btn-danger btn-sm" disabled>🗑️ Remove All Files</button>
              </div>
            ` : ""}
          </div>
        </details>
      ` : ""}
    </div>

    ${storeNames.length ? `
      <div class="card">
        <div class="grid-2">
          <label class="field">
            <span>View stock for</span>
            <select id="stock-store-sel">
              <option value="ALL">All offices (combined)</option>
              ${storeNames.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("")}
            </select>
          </label>
          <label class="field">
            <span>🔍 Filter products</span>
            <input type="text" id="stock-search-in" placeholder="Search product name..." />
          </label>
        </div>
        <div id="stock-table-container"></div>
      </div>
    ` : ""}
  `;

  document.getElementById("stock-upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("stock-files-input");
    if (!input.files.length) return toast("Please select one or more .xlsx files.", "warning");
    const fd = new FormData();
    for (const file of input.files) fd.append("files", file);
    try {
      const res = await api("/api/stock/upload", { method: "POST", body: fd });
      if (res.ok_lines.length) toast(`Loaded ${res.ok_lines.length} file(s)`, "success");
      if (res.bad_lines.length) toast(res.bad_lines.join(" | "), "error");
      await renderAdminStockTab(root);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  if (reportFiles.length) {
    const c1 = document.getElementById("del-rep-confirm");
    const b1 = document.getElementById("del-rep-btn");
    c1.addEventListener("change", () => (b1.disabled = !c1.checked));
    b1.addEventListener("click", async () => {
      const fn = document.getElementById("del-rep-select").value;
      try {
        const res = await api(`/api/stock/file/${encodeURIComponent(fn)}`, { method: "DELETE" });
        toast(res.message, "success");
        await renderAdminStockTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    const cAll = document.getElementById("del-all-confirm");
    const bAll = document.getElementById("del-all-btn");
    if (cAll && bAll) {
      cAll.addEventListener("change", () => (bAll.disabled = !cAll.checked));
      bAll.addEventListener("click", async () => {
        try {
          const res = await api("/api/stock/files/all", { method: "DELETE" });
          toast(res.message, "success");
          await renderAdminStockTab(root);
        } catch (err) {
          toast(err.message, "error");
        }
      });
    }
  }

  if (storeNames.length) {
    const storeSel = document.getElementById("stock-store-sel");
    const searchIn = document.getElementById("stock-search-in");
    const loadStockView = async () => {
      const v = await api(`/api/stock/view?store=${encodeURIComponent(storeSel.value)}&search=${encodeURIComponent(searchIn.value)}`);
      const box = document.getElementById("stock-table-container");
      if (v.empty) {
        box.innerHTML = `<div class="alert alert-info">No stock loaded.</div>`;
        return;
      }
      if (v.mode === "ALL") {
        box.innerHTML = `
          <div class="kpi-grid">
            <div class="kpi-card"><div class="kpi-label">Products</div><div class="kpi-value">${fmtNum(v.products_count)}</div></div>
            <div class="kpi-card"><div class="kpi-label">Total Stock (all offices)</div><div class="kpi-value">${fmtNum(v.total_units)} units</div></div>
          </div>
          ${renderTable(v.columns, v.rows)}
        `;
      } else {
        box.innerHTML = `
          <div class="kpi-grid">
            <div class="kpi-card"><div class="kpi-label">Office</div><div class="kpi-value">${esc(v.office)}</div></div>
            <div class="kpi-card"><div class="kpi-label">Total Stock</div><div class="kpi-value">${fmtNum(v.total_stock)} units</div></div>
            <div class="kpi-card"><div class="kpi-label">Available for Dispatch</div><div class="kpi-value">${fmtNum(v.available_stock)} units</div></div>
          </div>
          ${renderTable(v.columns, v.rows)}
          <details class="accordion">
            <summary><span>📦 Batch &amp; expiry details (as in the uploaded report)</span></summary>
            <div class="accordion-body">
              ${renderTable(v.batch_columns, v.batch_rows)}
              <p class="muted small">Batch figures are the report's own; order deductions are applied to the office totals above.</p>
            </div>
          </details>
        `;
      }
    };
    storeSel.addEventListener("change", loadStockView);
    let tmr = null;
    searchIn.addEventListener("input", () => {
      clearTimeout(tmr);
      tmr = setTimeout(loadStockView, 200);
    });
    await loadStockView();
  }
}

// ============================================================================
// ADMIN TAB 8: RELEASED ORDERS HISTORY
// ============================================================================
async function renderOrdersHistoryTab(root) {
  const data = await api("/api/orders/history");
  if (data.empty) {
    root.innerHTML = `<div class="card"><h3 class="card-title">📄 Released Orders Audit Trail</h3><div class="alert alert-info">No orders released yet.</div></div>`;
    return;
  }
  const summaries = data.summaries || [];
  const files = data.files || [];

  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">📄 Released Orders Audit Trail</h3>
      ${renderTable(data.columns, data.rows)}
    </div>

    ${summaries.length ? `
      <div class="card">
        <h3 class="card-title">🗑️ Delete a Released Order (cancelled by party)</h3>
        <p class="muted">Deleting removes the order's Excel file from the orders folder and returns its units to stock.</p>
        <label class="field">
          <span>Select order to delete</span>
          <select id="del-ord-sel">
            ${summaries.map((o) => `<option value="${esc(o.order_id)}">${esc(o.order_id)} — ${esc(o.party_name)} — ${esc(o.item_name)} (${fmtNum(o.total_units)} units)</option>`).join("")}
          </select>
        </label>
        <div class="row-gap">
          <label class="check-item"><input type="checkbox" id="del-ord-confirm" /> I confirm I want to permanently delete this order</label>
          <button id="del-ord-btn" class="btn btn-danger btn-sm" disabled>🗑️ Delete Order</button>
        </div>
      </div>
    ` : ""}

    <div class="card">
      <h3 class="card-title">📥 Download Released Order Documents</h3>
      ${!files.length
        ? `<p class="muted">No individual release documents found in 'orders/' directory.</p>`
        : files
            .map(
              (f) => `
              <div class="space-between" style="padding:6px 0;border-bottom:1px solid var(--border)">
                <span>📄 <code>${esc(f)}</code></span>
                <a class="btn btn-outline btn-sm" href="/api/orders/download/${encodeURIComponent(f)}" data-auth-dl="${esc(f)}">Download</a>
              </div>
            `
            )
            .join("")}
    </div>
  `;

  if (summaries.length) {
    const c = document.getElementById("del-ord-confirm");
    const b = document.getElementById("del-ord-btn");
    c.addEventListener("change", () => (b.disabled = !c.checked));
    b.addEventListener("click", async () => {
      const oid = document.getElementById("del-ord-sel").value;
      try {
        const res = await api(`/api/orders/${encodeURIComponent(oid)}`, { method: "DELETE" });
        toast(res.message, "success");
        await refreshAdminSidebar();
        await renderOrdersHistoryTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }
}

// ============================================================================
// ADMIN TAB 9: KNOWLEDGE BASE
// ============================================================================
async function renderKbTab(root) {
  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">📚 System Knowledge Base &amp; Operations Guide</h3>
      <p class="muted">Browse official policies, nationwide hub locations, delivery lead times, and FAQs.</p>
      <input type="text" id="kb-search" placeholder="🔍 e.g. safety stock, delivery times, store locations, payments..." />
    </div>
    <div id="kb-results"></div>
  `;

  const loadKb = async (q = "") => {
    const data = await api(`/api/kb?q=${encodeURIComponent(q)}`);
    let html = "";
    if (q.trim()) {
      if (data.match) {
        const a = data.match.article;
        html += `<div class="alert alert-success"><strong>Found Article:</strong> ${esc(a.title)} <em>(Category: ${esc(a.category)})</em><div style="margin-top:8px">${renderMd(a.summary)}</div></div>`;
      } else {
        html += `<div class="alert alert-info">No direct keyword match found. Showing all articles below.</div>`;
      }
    }
    for (const [cat, arts] of Object.entries(data.categories || {})) {
      html += `<div class="card"><h3 class="card-title">📂 ${esc(cat)}</h3>${arts
        .map(
          (a) => `
          <details class="accordion">
            <summary><span>📌 ${esc(a.title)}</span></summary>
            <div class="accordion-body">
              ${renderMd(a.summary)}
              <p class="muted small">Tags: ${esc((a.keywords || []).join(", "))}</p>
            </div>
          </details>
        `
        )
        .join("")}</div>`;
    }
    document.getElementById("kb-results").innerHTML = html;
  };

  let tmr = null;
  document.getElementById("kb-search").addEventListener("input", (e) => {
    clearTimeout(tmr);
    tmr = setTimeout(() => loadKb(e.target.value), 200);
  });
  await loadKb();
}

// ============================================================================
// ZONE PORTAL TABS
// ============================================================================
async function renderZoneNewOrderTab(root) {
  const ov = await api("/api/zone/overview");
  const items = ov.items || [];
  const cities = ov.cities || [];

  if (!items.length) {
    root.innerHTML = `<div class="alert alert-info">No stock is loaded yet. Please try again after the admin uploads the daily stock report.</div>`;
    return;
  }
  if (!cities.length) {
    root.innerHTML = `<div class="alert alert-warning">No cities are assigned to ${esc(ov.zone)} yet. Please ask the admin.</div>`;
    return;
  }

  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">➕ New Order (${esc(ov.zone)})</h3>
      <form id="z-order-form">
        <label class="field"><span>Client / Party Name</span><input type="text" id="zo-party" required /></label>
        <label class="field"><span>Select Product</span>
          <select id="zo-prod">${items.map((i) => `<option value="${esc(i.Item_Code)}" data-grp="${esc(i.Group || "—")}">ID ${esc(i.Product_ID)} — ${esc(i.Item_Name)}</option>`).join("")}</select>
        </label>
        <label class="field"><span>Order Quantity (units, 1–5000)</span><input type="number" id="zo-qty" min="1" max="5000" required placeholder="e.g. 50" /></label>
        <div class="grid-2">
          <label class="field"><span>Group (locked)</span><input type="text" id="zo-grp" readonly disabled /></label>
          <label class="field"><span>Send to City</span>
            <select id="zo-city" required>
              <option value="">Select city...</option>
              ${cities.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}
            </select>
          </label>
        </div>
        <div id="zo-avail-box"></div>
        <button type="submit" class="btn btn-primary btn-block">📨 Submit Order</button>
      </form>
    </div>
  `;

  const prodSel = document.getElementById("zo-prod");
  const qtyIn = document.getElementById("zo-qty");
  const grpIn = document.getElementById("zo-grp");
  const availBox = document.getElementById("zo-avail-box");

  const updateMeta = async () => {
    grpIn.value = prodSel.selectedOptions[0].dataset.grp || "—";
    const q = Number(qtyIn.value || 0);
    if (!q || q < 1) {
      availBox.innerHTML = "";
      return;
    }
    try {
      const av = await api("/api/zone/availability", {
        method: "POST",
        json: { item_code: prodSel.value, qty: q },
      });
      let h = av.sufficient
        ? `<div class="alert alert-success">✅ Stock available for sale in your ${esc(av.zone)} warehouse: ${fmtNum(av.zone_avail)} units.</div>`
        : `<div class="alert alert-warning">Your ${esc(av.zone)} warehouse has only ${fmtNum(av.zone_avail)} units available for sale (other warehouses: ${fmtNum(av.other_avail)}). To bring stock to your warehouse, request a transfer from the <strong>🔁 ITR (Warehouses)</strong> tab. You can still submit this order; the admin will decide.</div>`;
      if (av.pending_units) {
        h += `<p class="muted small">⏳ ${fmtNum(av.pending_units)} units of this product are already waiting for approval (all zones).</p>`;
      }
      availBox.innerHTML = h;
    } catch {}
  };

  prodSel.addEventListener("change", updateMeta);
  qtyIn.addEventListener("input", updateMeta);
  updateMeta();

  document.getElementById("z-order-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/zone/orders", {
        method: "POST",
        json: {
          party: document.getElementById("zo-party").value,
          item_code: prodSel.value,
          qty: Number(qtyIn.value),
          city: document.getElementById("zo-city").value,
        },
      });
      toast(res.message, "success");
      await refreshZoneSidebar();
      await renderZoneNewOrderTab(root);
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

async function renderZoneMyOrdersTab(root) {
  const data = await api("/api/zone/orders");
  if (data.empty) {
    root.innerHTML = `<div class="alert alert-info">There is no order in ${esc(state.user.zone)} yet.</div>`;
    return;
  }
  const cols = ["id", "created_at", "party", "item_name", "grp", "city", "qty", "status_label", "released_qty", "challan_no", "delivery_status", "return_qty", "order_id", "admin_note"];
  root.innerHTML = `
    <div class="kpi-grid">
      ${data.kpis.map((k) => `<div class="kpi-card"><div class="kpi-label">${esc(k.state)}</div><div class="kpi-value">${fmtNum(k.count)}</div></div>`).join("")}
    </div>
    <div class="card">
      <h3 class="card-title">📋 My Orders</h3>
      ${renderTable(cols, data.rows)}
    </div>
  `;
}

async function renderZoneDeliveryTab(root) {
  const data = await api("/api/zone/delivery");
  if (data.empty) {
    root.innerHTML = `<div class="alert alert-info">No approved order is waiting for delivery.</div>`;
    return;
  }

  let html = `
    <div class="card">
      <h3 class="card-title">🚚 Delivery Overview</h3>
      <p class="muted">Enter the <strong>delivery challan no.</strong> to mark an order as <strong>Dispatched</strong>, then set the final delivery status.</p>
      ${renderTable(["id", "party", "item_name", "city", "released_qty", "challan_no", "effective_delivery_status", "return_qty"], data.overview)}
    </div>
  `;

  if (data.delivered.length) {
    html += `<div class="card"><h3 class="card-title">↩️ Return (the party sent a delivered order back)</h3>${data.delivered
      .map((r) => {
        const rel = Number(r.released_qty || r.qty || 0);
        return `
          <details class="accordion">
            <summary><span>#${r.id} · ${esc(r.party)} · ${esc(r.item_name)} × ${fmtNum(rel)} → ${esc(r.city)} · Delivered</span></summary>
            <div class="accordion-body" data-ret-id="${r.id}">
              <div class="grid-3">
                <label class="field"><span>Returned quantity</span><input type="number" class="ret-qty" min="1" max="${Math.max(rel, 1)}" value="${Math.max(rel, 1)}" /></label>
                <label class="field"><span>Reason for return</span><input type="text" class="ret-reason" placeholder="e.g. damaged / wrong item" /></label>
                <label class="field"><span>Return challan / GRN (optional)</span><input type="text" class="ret-ch" /></label>
              </div>
              <button class="btn btn-primary ret-btn">↩️ Register Return</button>
            </div>
          </details>
        `;
      })
      .join("")}</div>`;
  }

  if (data.active.length) {
    html += `<div class="card"><h3 class="card-title">Update delivery</h3>${data.active
      .map((r) => {
        const stLabel = r.delivery_status || "Awaiting challan";
        if (!r.challan_no) {
          return `
            <details class="accordion">
              <summary><span>#${r.id} · ${esc(r.party)} · ${esc(r.item_name)} × ${fmtNum(r.released_qty)} → ${esc(r.city)} · ${esc(stLabel)}</span></summary>
              <div class="accordion-body" data-del-pre="${r.id}">
                <div class="row-gap" style="margin-bottom:10px">
                  <input type="text" class="del-ch" placeholder="Enter delivery challan no." style="flex:1" />
                  <button class="btn btn-primary del-dsp-btn">🚛 Dispatch</button>
                  <button class="btn btn-outline del-ask-cx">❌ Cancel order</button>
                </div>
                <div class="del-cx-confirm alert alert-warning hidden">
                  <p>Cancel this order? It will show <strong>Order cancelled by the consumer</strong> and stock goes back.</p>
                  <div class="row-gap">
                    <button class="btn btn-danger btn-sm del-cx-yes">Yes, cancel the order</button>
                    <button class="btn btn-secondary btn-sm del-cx-no">No, keep it</button>
                  </div>
                </div>
              </div>
            </details>
          `;
        }
        return `
          <details class="accordion">
            <summary><span>#${r.id} · ${esc(r.party)} · ${esc(r.item_name)} × ${fmtNum(r.released_qty)} → ${esc(r.city)} · ${esc(stLabel)}</span></summary>
            <div class="accordion-body" data-del-post="${r.id}">
              <p><strong>Challan No.:</strong> ${esc(r.challan_no)} · <strong>Status:</strong> 🚛 ${esc(stLabel)}</p>
              <div class="row-gap">
                <select class="del-st-sel" style="max-width:240px">
                  <option value="Not delivered yet">Not delivered yet</option>
                  <option value="Delivered">Delivered</option>
                  <option value="Cancel">Cancel</option>
                </select>
                <button class="btn btn-primary del-save-btn">💾 Save</button>
              </div>
              <div class="del-post-cx alert alert-warning hidden" style="margin-top:10px">
                <label class="check-item"><input type="checkbox" class="del-post-chk" /> Yes, the consumer cancelled this order (stock goes back)</label>
              </div>
            </div>
          </details>
        `;
      })
      .join("")}</div>`;
  }

  root.innerHTML = html;

  root.querySelectorAll("[data-ret-id]").forEach((box) => {
    const id = box.dataset.retId;
    box.querySelector(".ret-btn").addEventListener("click", async () => {
      try {
        const res = await api(`/api/zone/delivery/${id}/return`, {
          method: "POST",
          json: {
            qty: Number(box.querySelector(".ret-qty").value),
            reason: box.querySelector(".ret-reason").value,
            return_challan: box.querySelector(".ret-ch").value,
          },
        });
        toast(res.message, "success");
        await refreshZoneSidebar();
        await renderZoneDeliveryTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });

  root.querySelectorAll("[data-del-pre]").forEach((box) => {
    const id = box.dataset.delPre;
    const cxBox = box.querySelector(".del-cx-confirm");
    box.querySelector(".del-dsp-btn").addEventListener("click", async () => {
      try {
        const res = await api(`/api/zone/delivery/${id}/dispatch`, {
          method: "POST",
          json: { challan_no: box.querySelector(".del-ch").value },
        });
        toast(res.message, "success");
        await refreshZoneSidebar();
        await renderZoneDeliveryTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    box.querySelector(".del-ask-cx").addEventListener("click", () => cxBox.classList.remove("hidden"));
    box.querySelector(".del-cx-no").addEventListener("click", () => cxBox.classList.add("hidden"));
    box.querySelector(".del-cx-yes").addEventListener("click", async () => {
      try {
        const res = await api(`/api/zone/delivery/${id}/cancel`, { method: "POST" });
        toast(res.message, "warning");
        await refreshZoneSidebar();
        await renderZoneDeliveryTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });

  root.querySelectorAll("[data-del-post]").forEach((box) => {
    const id = box.dataset.delPost;
    const sel = box.querySelector(".del-st-sel");
    const cxDiv = box.querySelector(".del-post-cx");
    const chk = box.querySelector(".del-post-chk");
    const btn = box.querySelector(".del-save-btn");

    const sync = () => {
      if (sel.value === "Cancel") {
        cxDiv.classList.remove("hidden");
        btn.disabled = !chk.checked;
      } else {
        cxDiv.classList.add("hidden");
        btn.disabled = false;
      }
    };
    sel.addEventListener("change", sync);
    chk.addEventListener("change", sync);
    btn.addEventListener("click", async () => {
      try {
        const url = sel.value === "Cancel" ? `/api/zone/delivery/${id}/cancel` : `/api/zone/delivery/${id}/status`;
        const opts = sel.value === "Cancel" ? { method: "POST" } : { method: "POST", json: { status: sel.value } };
        const res = await api(url, opts);
        toast(res.message, "success");
        await refreshZoneSidebar();
        await renderZoneDeliveryTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });
}

async function renderZoneItrTab(root) {
  const data = await api("/api/zone/itr");
  const items = data.items || [];
  const mine = data.warehouses || [];
  if (!items.length) {
    root.innerHTML = `<div class="alert alert-info">No stock is loaded yet.</div>`;
    return;
  }
  if (!mine.length) {
    root.innerHTML = `<div class="alert alert-warning">No main warehouse is linked to ${esc(data.zone)}. Please ask the admin.</div>`;
    return;
  }

  const wide = data.wide;
  const itrs = data.itrs || [];

  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">🏭 Main warehouses — current stock</h3>
      <p class="muted">Your warehouse: <strong>${esc(mine.join(", "))}</strong>. Stock is after all approved orders and transfers.</p>
      <div class="grid-2">
        <input type="text" id="zitr-q" placeholder="Search product name..." />
        <select id="zitr-g"><option value="All">All Groups</option>${(wide.groups || []).map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("")}</select>
      </div>
      <div id="zitr-wide-tbl" style="margin-top:10px"></div>
    </div>

    <div class="card">
      <h3 class="card-title">➕ Request stock from another warehouse</h3>
      <label class="field"><span>Product</span>
        <select id="zitr-prod">${items.map((i) => `<option value="${esc(i.Item_Code)}">ID ${esc(i.Product_ID)} — ${esc(i.Item_Name)}</option>`).join("")}</select>
      </label>
      <div id="zitr-mat"></div>
      <div class="grid-2">
        <label class="field"><span>Send to (your warehouse)</span>
          <select id="zitr-to">${mine.map((w) => `<option value="${esc(w)}">${esc(w)}</option>`).join("")}</select>
        </label>
        <label class="field"><span>Send from warehouse</span>
          <select id="zitr-src"></select>
        </label>
      </div>
      <div class="grid-2">
        <label class="field"><span>ITR quantity (units)</span><input type="number" id="zitr-qty" min="1" required /></label>
        <label class="field"><span>Note / reason (optional)</span><input type="text" id="zitr-note" /></label>
      </div>
      <button id="zitr-submit" class="btn btn-primary btn-block">📨 Submit ITR Request</button>
    </div>

    <div class="card">
      <h3 class="card-title">📋 ITRs of my zone</h3>
      ${renderTable(["id", "created_at", "direction", "item_name", "from_store", "to_store", "qty", "approved_qty", "status_label", "note", "admin_note"], itrs, "No ITR yet.")}
      ${itrs
        .filter((r) => r.status === "IN_TRANSIT" && r.to_zone === data.zone)
        .map(
          (r) => `
          <div class="alert alert-info space-between">
            <span>🚛 #${r.id} · ${esc(r.item_name)} × ${fmtNum(r.approved_qty)} · ${esc(r.from_store)} → ${esc(r.to_store)}</span>
            <button class="btn btn-primary btn-sm zitr-rcv" data-id="${r.id}">📥 Mark received</button>
          </div>`
        )
        .join("")}
      ${itrs
        .filter((r) => r.status === "PENDING" && r.username === state.user.username)
        .map(
          (r) => `
          <div class="row-gap" style="margin-top:6px">
            <button class="btn btn-outline btn-sm zitr-cx" data-id="${r.id}">🚫 Cancel my request #${r.id} (${esc(r.item_name)} × ${fmtNum(r.qty)})</button>
          </div>`
        )
        .join("")}
    </div>
  `;

  const qIn = document.getElementById("zitr-q");
  const gSel = document.getElementById("zitr-g");
  const renderWide = () => {
    const q = qIn.value.trim().toLowerCase();
    const g = gSel.value;
    const rows = (wide.rows || []).filter(
      (r) => (!q || String(r.Item_Name).toLowerCase().includes(q)) && (g === "All" || r.Group === g)
    );
    document.getElementById("zitr-wide-tbl").innerHTML = renderTable(wide.columns, rows);
  };
  qIn.addEventListener("input", renderWide);
  gSel.addEventListener("change", renderWide);
  renderWide();

  const prodSel = document.getElementById("zitr-prod");
  const toSel = document.getElementById("zitr-to");
  const srcSel = document.getElementById("zitr-src");
  const qtyIn = document.getElementById("zitr-qty");
  const subBtn = document.getElementById("zitr-submit");

  const loadMatrix = async () => {
    const res = await api("/api/zone/itr/matrix", { method: "POST", json: { item_code: prodSel.value } });
    document.getElementById("zitr-mat").innerHTML = renderTable(["Warehouse", "Zone", "Stock", "Available for ITR"], res.matrix);
    const sources = (res.matrix || []).filter((m) => m.Warehouse !== toSel.value && m["Available for ITR"] > 0);
    if (!sources.length) {
      srcSel.innerHTML = `<option value="">No warehouse has stock available</option>`;
      subBtn.disabled = true;
    } else {
      srcSel.innerHTML = sources
        .map((s) => `<option value="${esc(s.Warehouse)}" data-avail="${s["Available for ITR"]}">${esc(s.Warehouse)} — available ${fmtNum(s["Available for ITR"])}</option>`)
        .join("");
      qtyIn.max = sources[0]["Available for ITR"];
      subBtn.disabled = false;
    }
  };
  prodSel.addEventListener("change", loadMatrix);
  toSel.addEventListener("change", loadMatrix);
  srcSel.addEventListener("change", () => {
    if (srcSel.selectedOptions[0]) qtyIn.max = srcSel.selectedOptions[0].dataset.avail || 1;
  });
  await loadMatrix();

  subBtn.addEventListener("click", async () => {
    try {
      const res = await api("/api/zone/itr", {
        method: "POST",
        json: {
          item_code: prodSel.value,
          from_store: srcSel.value,
          to_store: toSel.value,
          qty: Number(qtyIn.value),
          note: document.getElementById("zitr-note").value,
        },
      });
      toast(res.message, "success");
      await renderZoneItrTab(root);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  root.querySelectorAll(".zitr-rcv").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        const res = await api(`/api/zone/itr/${b.dataset.id}/receive`, { method: "POST" });
        toast(res.message, "success");
        await renderZoneItrTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    })
  );
  root.querySelectorAll(".zitr-cx").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        const res = await api(`/api/zone/itr/${b.dataset.id}/cancel`, { method: "POST" });
        toast(res.message, "info");
        await renderZoneItrTab(root);
      } catch (err) {
        toast(err.message, "error");
      }
    })
  );
}

async function renderZoneStockTab(root) {
  const data = await api("/api/zone/stock");
  if (!data.rows || !data.rows.length) {
    root.innerHTML = `<div class="alert alert-info">No stock is loaded yet.</div>`;
    return;
  }
  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">🏬 Stock of all stores</h3>
      <p class="muted">"Available for Sale" is after the safety buffer.</p>
      <div class="grid-2" style="margin-bottom:10px">
        <input type="text" id="zs-q" placeholder="Search product name..." />
        <select id="zs-g"><option value="All">All Groups</option>${(data.groups || []).map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("")}</select>
      </div>
      <div id="zs-tbl"></div>
    </div>
  `;
  const qIn = document.getElementById("zs-q");
  const gSel = document.getElementById("zs-g");
  const draw = () => {
    const q = qIn.value.trim().toLowerCase();
    const g = gSel.value;
    const rows = data.rows.filter((r) => (!q || String(r.Item_Name).toLowerCase().includes(q)) && (g === "All" || r.Group === g));
    document.getElementById("zs-tbl").innerHTML = renderTable(data.columns, rows);
  };
  qIn.addEventListener("input", draw);
  gSel.addEventListener("change", draw);
  draw();
}

// ============================================================================
// RECOVERY PORTAL TABS
// ============================================================================
async function renderRecoveryNewTab(root) {
  const meta = await api("/api/recovery/meta");
  root.innerHTML = `
    <div class="card">
      <h3 class="card-title">➕ New Recovery</h3>
      <form id="rec-new-form">
        <label class="field"><span>Client / Party Name</span><input type="text" id="rc-party" required /></label>
        <div class="grid-2">
          <label class="field"><span>Zone</span>
            <select id="rc-zone" required>
              <option value="">Select zone...</option>
              ${meta.zones.map((z) => `<option value="${esc(z)}">${esc(z)}</option>`).join("")}
            </select>
          </label>
          <label class="field"><span>City</span>
            <select id="rc-city" required disabled><option value="">Select the zone first</option></select>
          </label>
        </div>
        <div class="grid-2">
          <label class="field"><span>Group</span>
            <select id="rc-grp">${meta.groups.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("")}</select>
          </label>
          <label class="field"><span>Recovered Amount (Rs.)</span>
            <input type="number" id="rc-amt" min="1" step="100" required />
          </label>
        </div>
        <div class="grid-2">
          <label class="field"><span>Receipt / Cheque / Transaction No.</span><input type="text" id="rc-ref" /></label>
          <label class="field"><span>Note (optional)</span><input type="text" id="rc-note" /></label>
        </div>
        <button type="submit" class="btn btn-primary btn-block">📨 Submit Recovery</button>
      </form>
    </div>
  `;

  const zSel = document.getElementById("rc-zone");
  const cSel = document.getElementById("rc-city");
  zSel.addEventListener("change", () => {
    const cities = meta.zone_cities[zSel.value] || [];
    cSel.disabled = !zSel.value;
    cSel.innerHTML = zSel.value
      ? `<option value="">Select city...</option>${cities.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}`
      : `<option value="">Select the zone first</option>`;
  });

  document.getElementById("rec-new-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/recovery/entries", {
        method: "POST",
        json: {
          party: document.getElementById("rc-party").value,
          zone: zSel.value,
          city: cSel.value,
          grp: document.getElementById("rc-grp").value,
          amount: Number(document.getElementById("rc-amt").value),
          ref_no: document.getElementById("rc-ref").value,
          note: document.getElementById("rc-note").value,
        },
      });
      toast(res.message, "success");
      await refreshRecoverySidebar();
      await renderRecoveryNewTab(root);
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

async function renderRecoveryListTab(root) {
  const data = await api(`/api/recovery/entries?zones=${encodeURIComponent(state.recZones.join(","))}`);
  const allZones = ["Zone I", "Zone II", "Zone III", "Zone IV", "Zone V"];
  if (data.empty) {
    root.innerHTML = `<div class="alert alert-info">There is no recovery entry yet.</div>`;
    return;
  }

  root.innerHTML = `
    <div class="card">
      <div class="row-gap" style="margin-bottom:12px">
        <strong>Filter Zones:</strong>
        ${allZones.map((z) => `<label class="pill-check"><input type="checkbox" class="rec-zf" value="${esc(z)}" ${state.recZones.includes(z) ? "checked" : ""} /> ${esc(z)}</label>`).join("")}
      </div>
      <div class="kpi-grid">
        ${data.kpis.map((k) => `<div class="kpi-card"><div class="kpi-label">${esc(k.state)}</div><div class="kpi-value">${fmtNum(k.count)}</div></div>`).join("")}
        <div class="kpi-card"><div class="kpi-label">Recovered (OK)</div><div class="kpi-value">Rs. ${fmtNum(Math.round(data.recovered_ok))}</div></div>
      </div>
      ${renderTable(["id", "created_at", "zone", "party", "grp", "city", "amount", "ref_no", "status_label", "admin_note"], data.rows)}
    </div>
  `;

  root.querySelectorAll(".rec-zf").forEach((cb) => {
    cb.addEventListener("change", () => {
      state.recZones = Array.from(root.querySelectorAll(".rec-zf:checked")).map((el) => el.value);
      renderRecoveryListTab(root);
    });
  });
}

// ============================================================================
// IDLE-TIMEOUT / SESSION EXPIRY
// ============================================================================
// • Browser close → cookie deleted automatically (no max_age = session cookie).
// • 30 min idle with no activity → logout.
// • Server ping every 5 min to slide the server-side idle window.
// ============================================================================
(function setupIdleGuard() {
  const IDLE_MS =  5 * 60 * 1000;          // 5 minutes idle → auto logout
  const PING_MS =  2 * 60 * 1000;          // ping server every 2 min

  let idleTimer = null;
  let pingTimer = null;

  function resetIdleTimer() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(onIdle, IDLE_MS);
  }

  async function onIdle() {
    if (!state.user) return;
    try { await api("/api/auth/logout", { method: "POST" }); } catch {}
    setToken(null);
    state.user = null;
    state.activeTab = null;
    renderRoot();
    // Clear login fields so credentials aren't visible to anyone who opens the page
    setTimeout(() => {
      const u = document.getElementById("login-username");
      const p = document.getElementById("login-password");
      if (u) u.value = "";
      if (p) p.value = "";
      toast("⏰ Session expired due to inactivity. Please sign in again.", "warning", 6000);
    }, 300);
  }

  async function pingServer() {
    if (!state.user) return;
    try {
      const res = await api("/api/auth/me");
      if (!res.authenticated) {
        setToken(null);
        state.user = null;
        state.activeTab = null;
        renderRoot();
        setTimeout(() => {
          const u = document.getElementById("login-username");
          const p = document.getElementById("login-password");
          if (u) u.value = "";
          if (p) p.value = "";
          toast("🔒 Your session has ended. Please sign in again.", "warning", 6000);
        }, 300);
      }
    } catch {}
  }

  // Authenticated file download handler (passes per-tab Bearer token)
  document.addEventListener("click", async (e) => {
    const a = e.target.closest("a[data-auth-dl]");
    if (!a) return;
    e.preventDefault();
    const url = a.getAttribute("href");
    const filename = a.getAttribute("data-auth-dl") || "download.xlsx";
    try {
      const headers = {};
      const tok = getToken();
      if (tok) headers["Authorization"] = `Bearer ${tok}`;
      const res = await fetch(url, { headers });
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const tmp = document.createElement("a");
      tmp.href = blobUrl;
      tmp.download = filename;
      document.body.appendChild(tmp);
      tmp.click();
      tmp.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (err) {
      toast(err.message, "error");
    }
  });

  // Listen to any user activity to reset the idle clock
  ["mousemove", "mousedown", "keydown", "touchstart", "scroll", "click"].forEach((evt) => {
    document.addEventListener(evt, resetIdleTimer, { passive: true });
  });

  // Start timers
  resetIdleTimer();
  pingTimer = setInterval(pingServer, PING_MS);
})();

// Start
bootstrap();
