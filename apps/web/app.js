"use strict";

const sessionStatus = document.querySelector("#session-status");
const storeForm = document.querySelector("#store-form");
const storeInput = document.querySelector("#store-id");
const orders = document.querySelector("#orders");
const approvals = document.querySelector("#approvals");
const quotes = document.querySelector("#quotes");
const incidents = document.querySelector("#incidents");
const queueRecovery = document.querySelector("#queue-recovery");
const orderCount = document.querySelector("#order-count");
const approvalCount = document.querySelector("#approval-count");
const quoteCount = document.querySelector("#quote-count");
const incidentCount = document.querySelector("#incident-count");
const incidentForm = document.querySelector("#incident-form");
const quoteForm = document.querySelector("#quote-form");
const manualPrepareForm = document.querySelector("#manual-prepare-form");
const manualAttestForm = document.querySelector("#manual-attest-form");
const shadowDrafts = document.querySelector("#shadow-drafts");
const unknownSends = document.querySelector("#unknown-sends");
const draftCount = document.querySelector("#draft-count");
const unknownCount = document.querySelector("#unknown-count");
const mutationKeys = new Map();

const cookieValue = (name) => {
  const prefix = `${encodeURIComponent(name)}=`;
  const match = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
};

const api = async (path, options = {}) => {
  if (options.method && options.method !== "GET" && !navigator.onLine) {
    throw new Error("Ngoại tuyến: chỉ đọc; thao tác không được xếp hàng.");
  }
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (options.method && options.method !== "GET") {
    const csrfToken = cookieValue("staff_csrf");
    if (!csrfToken) throw new Error("Thiếu CSRF token; hãy đăng nhập lại.");
    headers["X-CSRF-Token"] = csrfToken;
  }
  let response;
  try {
    response = await fetch(path, { ...options, credentials: "same-origin", headers });
  } catch (_error) {
    throw new Error("Không kết nối được API. Không có thao tác nào được xếp hàng.");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const fallback = response.status === 401 ? "Phiên staff không hợp lệ." : `API lỗi ${response.status}`;
    throw new Error(typeof payload.detail === "string" ? payload.detail : fallback);
  }
  return response.status === 204 ? null : response.json();
};

const idempotencyKey = (scope, payload) => {
  const fingerprint = `${scope}:${JSON.stringify(payload)}`;
  if (!mutationKeys.has(fingerprint)) mutationKeys.set(fingerprint, crypto.randomUUID());
  return mutationKeys.get(fingerprint);
};

const shortId = (value) => value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "—";
const setText = (node, selector, value) => { node.querySelector(selector).textContent = String(value); };
const vnd = new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND" });

const showError = (container, message) => {
  const error = document.createElement("p");
  error.className = "error";
  error.textContent = message;
  container.replaceChildren(error);
};

const showResult = (output, message, error = false) => {
  output.textContent = message;
  output.classList.toggle("error", error);
};

const renderOrders = (items) => {
  orders.replaceChildren();
  orderCount.textContent = String(items.length);
  if (!items.length) {
    orders.innerHTML = '<p class="empty">Không có đơn trong phạm vi này.</p>';
    return;
  }
  const template = document.querySelector("#order-template");
  for (const item of items) {
    const card = template.content.cloneNode(true);
    setText(card, '[data-field="order"]', shortId(item.order_id));
    setText(card, '[data-field="version"]', `v${item.row_version}`);
    setText(card, '[data-field="commercial"]', item.commercial);
    setText(card, '[data-field="intake"]', item.intake);
    setText(card, '[data-field="production"]', item.production);
    setText(card, '[data-field="balance"]', item.balance);
    orders.append(card);
  }
};

const renderApprovals = (items) => {
  approvals.replaceChildren();
  approvalCount.textContent = String(items.length);
  if (!items.length) {
    approvals.innerHTML = '<p class="empty">Không có approval đang chờ.</p>';
    return;
  }
  const template = document.querySelector("#approval-template");
  for (const item of items) {
    const card = template.content.cloneNode(true);
    setText(card, '[data-field="approval"]', shortId(item.approval_request_id));
    setText(card, '[data-field="status"]', item.status);
    setText(card, '[data-field="role"]', item.required_role);
    setText(card, '[data-field="expiry"]', new Date(item.expires_at).toLocaleString("vi-VN"));
    setText(card, '[data-field="hash"]', item.envelope_hash);
    approvals.append(card);
  }
};

const renderQuotes = (items) => {
  quotes.replaceChildren();
  quoteCount.textContent = String(items.length);
  if (!items.length) {
    quotes.innerHTML = '<p class="empty">Không có báo giá trong phạm vi này.</p>';
    return;
  }
  const template = document.querySelector("#quote-template");
  for (const item of items) {
    const card = template.content.cloneNode(true);
    setText(card, '[data-field="quote"]', shortId(item.quote_id));
    setText(card, '[data-field="status"]', item.status);
    setText(card, '[data-field="revision"]', `r${item.revision} · row v${item.row_version}`);
    setText(card, '[data-field="finality"]', item.finality);
    // A quote with an unresolved delivery fee has no display total, and the schema requires that.
    // Showing a placeholder rather than a number is the point: staff must not read a service
    // subtotal as the amount a customer pays.
    const total = item.display_total_min_vnd === null
      ? "Chưa có tổng · phí giao hàng chưa chốt"
      : item.display_total_min_vnd === item.display_total_max_vnd
        ? vnd.format(item.display_total_min_vnd)
        : `${vnd.format(item.display_total_min_vnd)} – ${vnd.format(item.display_total_max_vnd)}`;
    setText(card, '[data-field="total"]', total);
    setText(card, '[data-field="hash"]', item.snapshot_hash);
    quotes.append(card);
  }
};

const renderIncidents = (items) => {
  incidents.replaceChildren();
  incidentCount.textContent = String(items.length);
  if (!items.length) {
    incidents.innerHTML = '<p class="empty">Không có sự cố trong phạm vi này.</p>';
    return;
  }
  const template = document.querySelector("#incident-template");
  for (const item of items) {
    const card = template.content.cloneNode(true);
    setText(card, '[data-field="incident"]', shortId(item.incident_id));
    setText(card, '[data-field="status"]', item.status);
    setText(card, '[data-field="order"]', shortId(item.order_id));
    setText(card, '[data-field="category"]', item.category);
    setText(card, '[data-field="fault"]', item.fault_decided ? "YES" : "NO");
    setText(card, '[data-field="remedy"]', item.remedy_decided ? "YES" : "NO");
    incidents.append(card);
  }
};

const renderQueue = (item) => {
  const list = document.createElement("dl");
  const fields = [
    ["Internal pending", item.pending_internal], ["Internal processing", item.processing_internal],
    ["Internal expired", item.expired_internal], ["Internal dead", item.dead_internal],
    ["Agent pending", item.pending_agent], ["Agent processing", item.processing_agent],
    ["Agent expired", item.expired_agent], ["Agent failed", item.failed_agent],
    ["Replay available", item.replay_available ? "YES" : "NO"],
  ];
  for (const [label, value] of fields) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = String(value);
    row.append(term, detail);
    list.append(row);
  }
  queueRecovery.replaceChildren(list);
};

const loadStore = async (storeId) => {
  const encoded = encodeURIComponent(storeId);
  const results = await Promise.allSettled([
    api(`/internal/v1/stores/${encoded}/orders?limit=100`),
    api(`/internal/v1/stores/${encoded}/quotes?limit=100`),
    api(`/internal/v1/stores/${encoded}/incidents?limit=100`),
  ]);
  const renderers = [[orders, renderOrders], [quotes, renderQuotes], [incidents, renderIncidents]];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") renderers[index][1](result.value);
    else showError(renderers[index][0], result.reason.message);
  });
};

const loadApprovals = async () => {
  try { renderApprovals(await api("/internal/v1/approvals?limit=100")); }
  catch (error) { showError(approvals, error.message); }
};

const loadQueue = async () => {
  try { renderQueue(await api("/internal/v1/queue-recovery")); }
  catch (error) { showError(queueRecovery, error.message); }
};

const setMutationAvailability = () => {
  document.querySelectorAll(".mutation-form button").forEach((button) => {
    button.disabled = !navigator.onLine;
  });
};

storeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const storeId = storeInput.value.trim();
  localStorage.setItem("staff_store_id", storeId);
  await loadStore(storeId);
});

incidentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const storeId = storeInput.value.trim();
  if (!storeId) return showResult(document.querySelector("#incident-result"), "Chọn cửa hàng trước.", true);
  const data = new FormData(incidentForm);
  const payload = {
    order_id: data.get("order_id"),
    contact_scope_hash: data.get("contact_scope_hash"),
    evidence_summary_hash: data.get("evidence_summary_hash"),
  };
  const output = document.querySelector("#incident-result");
  try {
    const result = await api(`/internal/v1/stores/${encodeURIComponent(storeId)}/incidents`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey("incident", payload) },
      body: JSON.stringify(payload),
    });
    showResult(output, `Đã mở ${result.incident_id}; fault/remedy vẫn chưa quyết định.`);
    await loadStore(storeId);
  } catch (error) { showResult(output, error.message, true); }
});

// QUOTE-COMMAND-001. The form collects facts and renders what the server computed. It does not
// total, round, convert or default anything — the quantity is sent as the staff member typed it,
// because normalising "4.0" or "6" in the browser would put a second opinion about money in the
// one place nobody reviews.
quoteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const storeId = storeInput.value.trim();
  const output = document.querySelector("#quote-result");
  if (!storeId) return showResult(output, "Chọn cửa hàng trước.", true);
  const data = new FormData(quoteForm);
  const payload = {
    bound_order_request_id: data.get("bound_order_request_id"),
    lines: [{
      service_code: data.get("service_code"),
      quantity: String(data.get("quantity")).trim(),
      unit: data.get("unit"),
      quantity_basis: data.get("quantity_basis"),
    }],
  };
  try {
    const result = await api(`/internal/v1/stores/${encodeURIComponent(storeId)}/quotes`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey("quote", payload) },
      body: JSON.stringify(payload),
    });
    const codes = result.reason_codes.join(" · ");
    showResult(output,
      `Bản ${result.revision}: ${vnd.format(result.net_service_subtotal_vnd)} tiền dịch vụ. `
      + `Chưa có tổng cuối vì ${codes}.`);
    await loadStore(storeId);
  } catch (error) {
    showResult(output, error.message, true);
  }
});

manualPrepareForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(manualPrepareForm);
  const approvalId = data.get("approval_id");
  const payload = {
    observed_resource_version: Number(data.get("resource_version")),
    observed_snapshot_hash: data.get("snapshot_hash"),
    observed_rendered_hash: data.get("rendered_hash"),
    recipient_binding_id: data.get("recipient_id"),
    channel: "INTERNAL_TEST",
  };
  const output = document.querySelector("#manual-prepare-result");
  try {
    const result = await api(`/internal/v1/approvals/${encodeURIComponent(approvalId)}/manual-send`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey("manual-prepare", payload) },
      body: JSON.stringify(payload),
    });
    document.querySelector("#manual-envelope-id").value = result.manual_send_envelope_id;
    document.querySelector("#manual-attest-resource-version").value = payload.observed_resource_version;
    document.querySelector("#manual-exact-hash").value = result.rendered_hash;
    showResult(output, `Envelope ${result.manual_send_envelope_id} đã khóa; hệ thống chưa gửi.`);
  } catch (error) { showResult(output, error.message, true); }
});

manualAttestForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(manualAttestForm);
  const envelopeId = data.get("envelope_id");
  const envelopeVersion = Number(data.get("envelope_version"));
  const payload = {
    observed_resource_version: Number(data.get("resource_version")),
    exact_rendered_hash: data.get("exact_rendered_hash"),
    sent_at: new Date(data.get("sent_at")).toISOString(),
  };
  const output = document.querySelector("#manual-attest-result");
  try {
    const result = await api(`/internal/v1/manual-sends/${encodeURIComponent(envelopeId)}/attest`, {
      method: "POST",
      headers: {
        "Idempotency-Key": idempotencyKey("manual-attest", { ...payload, envelopeVersion }),
        "If-Match": `\"${envelopeVersion}\"`,
      },
      body: JSON.stringify(payload),
    });
    showResult(output, `Đã ghi nhận ${result.status}; row version ${result.row_version}.`);
  } catch (error) { showResult(output, error.message, true); }
});


// --- SHADOW-CONSOLE-001 ---------------------------------------------------------------------
// Read-only rendering plus three attributed decisions. Nothing here computes money, ranks work or
// sends anything: every value shown was computed by the server.

const renderDrafts = (items) => {
  draftCount.textContent = String(items.length);
  if (!items.length) {
    shadowDrafts.replaceChildren(Object.assign(document.createElement("p"),
      { className: "empty", textContent: "Không có bản nháp nào chờ duyệt." }));
    return;
  }
  shadowDrafts.replaceChildren(...items.map((item) => {
    const card = document.createElement("div");
    card.className = "card";
    const heading = document.createElement("p");
    heading.className = "eyebrow";
    heading.textContent = `${shortId(item.agent_run_id)} · ${item.terminal_code} · ${item.tool_call_count} tool`;
    const body = document.createElement("p");
    body.textContent = item.draft_text;
    const edit = document.createElement("textarea");
    edit.rows = 3;
    edit.value = item.draft_text;
    edit.setAttribute("aria-label", "Sửa bản nháp");
    const reason = document.createElement("input");
    reason.placeholder = "REASON_CODE khi từ chối";
    reason.setAttribute("aria-label", "Mã lý do từ chối");
    const output = document.createElement("p");
    const actions = document.createElement("div");
    actions.className = "actions";
    const decide = async (decision) => {
      try {
        await api(`/internal/v1/shadow/drafts/${item.agent_run_id}/decision`, {
          method: "POST",
          body: JSON.stringify({
            decision,
            reason_code: decision === "REJECT" ? reason.value.trim() || null : null,
            edited_text: decision === "EDIT" ? edit.value : null,
          }),
        });
        showResult(output, `Đã ghi nhận ${decision}.`);
        await loadShadowDrafts(storeInput.value.trim());
      } catch (error) {
        showResult(output, error.message, true);
      }
    };
    for (const [label, decision] of [["Duyệt", "APPROVE"], ["Sửa & duyệt", "EDIT"], ["Từ chối", "REJECT"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", () => decide(decision));
      actions.append(button);
    }
    card.append(heading, body, edit, reason, actions, output);
    return card;
  }));
};

const renderUnknownSends = (items) => {
  unknownCount.textContent = String(items.length);
  if (!items.length) {
    unknownSends.replaceChildren(Object.assign(document.createElement("p"),
      { className: "empty", textContent: "Không có lần gửi nào chưa rõ kết quả." }));
    return;
  }
  unknownSends.replaceChildren(...items.map((item) => {
    const card = document.createElement("div");
    card.className = "card";
    const heading = document.createElement("p");
    heading.className = "eyebrow";
    heading.textContent = `${shortId(item.receipt_id)} · ${item.provider} · lần ${item.attempt_number}`;
    const state = document.createElement("p");
    state.textContent = `${item.message_kind} · ${item.reconciliation_state}`;
    const output = document.createElement("p");
    const actions = document.createElement("div");
    actions.className = "actions";
    const resolve = async (resolution) => {
      try {
        await api(`/internal/v1/shadow/unknown-sends/${item.receipt_id}/reconcile`, {
          method: "POST",
          body: JSON.stringify({ resolution, note: null }),
        });
        showResult(output, "Đã đối soát.");
        await loadUnknownSends();
      } catch (error) {
        showResult(output, error.message, true);
      }
    };
    for (const [label, resolution] of [["Đã gửi", "CONFIRMED_SENT"], ["Chưa gửi", "CONFIRMED_NOT_SENT"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", () => resolve(resolution));
      actions.append(button);
    }
    card.append(heading, state, actions, output);
    return card;
  }));
};

const loadShadowDrafts = async (storeId) => {
  if (!storeId) return;
  try {
    renderDrafts(await api(`/internal/v1/stores/${storeId}/shadow/drafts`));
  } catch (error) {
    showError(shadowDrafts, error.message);
  }
};

const loadUnknownSends = async () => {
  try {
    renderUnknownSends(await api("/internal/v1/shadow/unknown-sends"));
  } catch (error) {
    showError(unknownSends, error.message);
  }
};

const start = async () => {
  setMutationAvailability();
  try {
    const session = await api("/internal/v1/session");
    sessionStatus.textContent = `${session.roles.join(" · ")} · MFA ${session.mfa_verified ? "OK" : "NO"}`;
    const savedStore = localStorage.getItem("staff_store_id");
    if (savedStore) { storeInput.value = savedStore; await loadStore(savedStore); await loadShadowDrafts(savedStore); }
    await Promise.all([loadApprovals(), loadQueue(), loadUnknownSends()]);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/staff/sw.js");
  } catch (error) {
    sessionStatus.textContent = "Chưa xác thực";
    [orders, approvals, quotes, incidents, queueRecovery, shadowDrafts, unknownSends].forEach((node) => showError(node, error.message));
  }
};

window.addEventListener("online", setMutationAvailability);
window.addEventListener("offline", setMutationAvailability);
start();
