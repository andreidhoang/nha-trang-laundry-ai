"use strict";

const sessionStatus = document.querySelector("#session-status");
const storeForm = document.querySelector("#store-form");
const storeInput = document.querySelector("#store-id");
const orders = document.querySelector("#orders");
const approvals = document.querySelector("#approvals");
const orderCount = document.querySelector("#order-count");
const approvalCount = document.querySelector("#approval-count");

const api = async (path) => {
  const response = await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(response.status === 401 ? "Phiên staff không hợp lệ." : `API lỗi ${response.status}`);
  return response.json();
};

const shortId = (value) => `${value.slice(0, 8)}…${value.slice(-4)}`;
const setText = (node, selector, value) => { node.querySelector(selector).textContent = String(value); };

const renderOrders = (items) => {
  orders.replaceChildren();
  orderCount.textContent = String(items.length);
  if (!items.length) {
    const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "Không có đơn trong phạm vi này."; orders.append(empty); return;
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
    const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "Không có approval đang chờ."; approvals.append(empty); return;
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

const showError = (container, message) => {
  const error = document.createElement("p"); error.className = "error"; error.textContent = message; container.replaceChildren(error);
};

const loadApprovals = async () => {
  try { renderApprovals(await api("/internal/v1/approvals?limit=100")); }
  catch (error) { showError(approvals, error.message); }
};

storeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const storeId = storeInput.value.trim();
  localStorage.setItem("staff_store_id", storeId);
  try { renderOrders(await api(`/internal/v1/stores/${encodeURIComponent(storeId)}/orders?limit=100`)); }
  catch (error) { showError(orders, error.message); }
});

const start = async () => {
  try {
    const session = await api("/internal/v1/session");
    sessionStatus.textContent = `${session.roles.join(" · ")} · MFA ${session.mfa_verified ? "OK" : "NO"}`;
    const savedStore = localStorage.getItem("staff_store_id");
    if (savedStore) { storeInput.value = savedStore; storeForm.requestSubmit(); }
    await loadApprovals();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/staff/sw.js");
  } catch (error) {
    sessionStatus.textContent = "Chưa xác thực";
    showError(orders, error.message);
    showError(approvals, error.message);
  }
};

start();
