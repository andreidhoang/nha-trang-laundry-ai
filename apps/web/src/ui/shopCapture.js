/**
 * Measuring the shop inside taps staff already make (`SHOP-CAPTURE-001`, `DEC-038`): the pieces the
 * order page borrows.
 *
 *   - **"Máy nào?"** at *Bắt đầu giặt*: the machines a load goes into, as big buttons, the last one
 *     used first, and "Bỏ qua". Which machines, and in what order, is the server's
 *     (`GET …/machines?purpose=WASH`): this module never filters or sorts them. Skipping is allowed
 *     and is recorded as "chưa ghi máy", so capture shows in the data rather than being forced here.
 *   - **Chi phí chuyến** on the delivery-leg sheet: vehicle, kilometres, money and a short note,
 *     every one optional. The owner's vehicle rule (motorbike under 20 kg, car from 20 kg) is the
 *     server's answer for this order's weight and is shown as a hint beside the choice, never picked
 *     for the person: a vehicle nobody chose is not a recorded fact.
 *   - **What the order recorded**: its wash cycles (machine, minutes) and the cost of each trip, as
 *     the server read them (`GET …/orders/{id}/capture`). Minutes and money arrive computed.
 *
 * No money arithmetic: a typed amount is parsed by `parseDong` and sent as the integer typed; the
 * server bounds it and the domain refuses a figure outside the rule.
 *
 * @module ui/shopCapture
 */

import { request } from "../core/api.js";
import { h } from "../core/dom.js";
import { UNKNOWN, dateTime, money, parseDong } from "../core/format.js";
import { VEHICLE_VI } from "../core/i18n.js";
import { button, infoButton, list, listRow, moneyInput, segmented } from "./kit.js";

/** What a cost note is for, and what it must not carry (tier 1, beside the field). */
export const TRIP_NOTE_HINT = "Ghi về chi phí (gửi xe, Grab…). Không ghi số điện thoại khách.";

/** The owner's vehicle rule (`BUSINESS_TRUTH_INTAKE.md`, confirmed), shown while no weight is known. */
const VEHICLE_RULE = "Dưới 20 kg đi xe máy, từ 20 kg đi ô tô.";

/** Why "Bỏ qua" exists and what it records (tier 2, behind the chooser's ⓘ). */
const SKIP_RULE =
  "Chọn máy giúp tiệm biết mỗi máy chạy một mẻ mất bao lâu. Không kịp chọn thì bấm Bỏ qua: đơn " +
  "vẫn bắt đầu giặt, mẻ được ghi là chưa ghi máy, và báo cáo đếm riêng số mẻ đó. Máy vừa dùng " +
  "gần nhất đứng đầu.";

/**
 * The machines a load goes into, most recently used first, as the server lists them.
 *
 * @param {string} store
 * @returns {Promise<any[]>}
 */
export async function loadWashMachines(store) {
  const found = await request(
    `/internal/v1/stores/${encodeURIComponent(store)}/machines?purpose=WASH`,
  );
  return Array.isArray(found?.machines) ? found.machines : [];
}

/**
 * "Máy nào?": one big button per machine and a quiet "Bỏ qua".
 *
 * @param {object} spec
 * @param {any[]} spec.machines as the server listed them
 * @param {(machineId: string|null, control: HTMLButtonElement) => void} spec.onPick null = skipped
 * @returns {HTMLElement}
 */
export function machinePicker(spec) {
  const buttons = spec.machines.map((machine, index) => {
    const control = button({
      label: h(
        "span",
        { class: "machine-pick__label" },
        h("strong", null, String(machine.code)),
        h("span", { class: "machine-pick__name" }, String(machine.display_name)),
        index === 0 && machine.last_used_at
          ? h("span", { class: "machine-pick__last" }, "Vừa dùng")
          : null,
      ),
      variant: index === 0 && machine.last_used_at ? "primary" : "secondary",
      block: true,
      network: true,
      data: { machineCode: String(machine.code), machineId: String(machine.machine_id) },
      onClick: () => spec.onPick(String(machine.machine_id), control),
    });
    return control;
  });
  const skip = button({
    label: "Bỏ qua",
    variant: "quiet",
    block: true,
    network: true,
    data: { machineSkip: "true" },
    onClick: () => spec.onPick(null, skip),
  });
  return h(
    "div",
    { class: "stack stack--tight" },
    h(
      "div",
      { class: "fact-line" },
      h("p", { class: "field-label" }, "Máy nào?"),
      infoButton("Vì sao hỏi máy?", h("p", null, SKIP_RULE)),
    ),
    h("div", { class: "machine-pick" }, buttons),
    skip,
  );
}

/**
 * An optional machine choice inside another sheet (the rewash sheet): chips, none selected.
 *
 * @param {object} spec
 * @param {any[]} spec.machines
 * @param {(machineId: string) => void} spec.onChange
 * @returns {HTMLElement}
 */
export function machineChips(spec) {
  return h(
    "div",
    { class: "stack stack--tight" },
    h("p", { class: "field-label" }, "Máy nào? (không bắt buộc)"),
    segmented({
      label: "Máy giặt lại",
      id: "rewash-machine",
      value: "",
      wrap: true,
      options: spec.machines.map((machine) => ({
        value: String(machine.machine_id),
        label: String(machine.code),
      })),
      onChange: spec.onChange,
    }),
  );
}

/**
 * The trip-cost fields of the delivery-leg sheet. Every field is optional; `read()` returns the
 * body fields to send, or a problem to show when a typed value cannot be read.
 *
 * The vehicle hint is filled in by `setCapture` once the order's capture read arrives, so the
 * sheet opens at once and never waits on it.
 *
 * @returns {{node: HTMLElement, read: () => {fields?: Record<string, unknown>, problem?: string}, intent: () => string, setCapture: (capture: any) => void}}
 */
export function tripFields() {
  let vehicle = "";
  const km = /** @type {HTMLInputElement} */ (
    h("input", {
      id: "trip-km",
      type: "text",
      inputmode: "decimal",
      autocomplete: "off",
      maxlength: "5",
      placeholder: "Ví dụ 4,5",
    })
  );
  const cost = moneyInput({
    id: "trip-cost",
    label: "Tiền chuyến này",
    placeholder: "0",
    echo: (text) => {
      if (!text.trim()) return "";
      const amount = parseDong(text);
      return amount === null ? "Chưa đọc được số tiền" : `= ${money(amount)}`;
    },
  });
  const note = /** @type {HTMLInputElement} */ (
    h("input", {
      id: "trip-note",
      type: "text",
      maxlength: "120",
      autocomplete: "off",
      placeholder: "Không bắt buộc",
    })
  );
  const hint = h("p", { class: "hint", dataSuggestedVehicle: "" }, VEHICLE_RULE);
  const node = h(
    "details",
    { class: "trip-fields", dataTripFields: "true" },
    h("summary", null, "Chi phí chuyến (không bắt buộc)"),
    h(
      "div",
      { class: "stack stack--tight" },
      segmented({
        label: "Xe",
        id: "trip-vehicle",
        value: "",
        options: ["XE_MAY", "O_TO", "THUE_NGOAI"].map((value) => ({
          value,
          label: VEHICLE_VI[value],
        })),
        onChange: (value) => {
          vehicle = value;
        },
      }),
      hint,
      h(
        "div",
        { class: "trip-fields__row" },
        h("label", { class: "field-label", for: "trip-km" }, "Số km"),
        km,
      ),
      h("label", { class: "field-label", for: "trip-cost" }, "Tiền xăng, gửi xe hoặc thuê xe"),
      cost.node,
      h("label", { class: "field-label", for: "trip-note" }, "Ghi chú"),
      note,
      h("p", { class: "hint" }, TRIP_NOTE_HINT),
    ),
  );
  return {
    node,
    setCapture(capture) {
      const suggested = String(capture?.suggested_vehicle || "");
      if (!suggested || !capture?.weight_kg) return;
      hint.dataset.suggestedVehicle = suggested;
      hint.textContent =
        `Theo cân ${String(capture.weight_kg).replace(".", ",")} kg: ` +
        `${String(VEHICLE_VI[suggested] || suggested).toLowerCase()}.`;
    },
    intent: () => `${vehicle}|${km.value.trim()}|${cost.input.value.trim()}|${note.value.trim()}`,
    read() {
      /** @type {Record<string, unknown>} */
      const fields = {};
      if (vehicle) fields.vehicle = vehicle;
      const kmText = km.value.trim();
      if (kmText) {
        if (!/^\d{1,3}([.,]\d)?$/.test(kmText)) {
          return { problem: "Số km là một số, tối đa một chữ số sau dấu phẩy (ví dụ 4,5)." };
        }
        fields.km = kmText;
      }
      const costText = cost.input.value.trim();
      if (costText) {
        const amount = parseDong(costText);
        if (amount === null) return { problem: "Chưa đọc được số tiền chuyến. Gõ số, ví dụ 25000." };
        fields.cost_vnd = amount;
      }
      const noteText = note.value.trim();
      if (noteText) fields.note = noteText;
      return { fields };
    },
  };
}

/**
 * What the order recorded: its wash cycles and the cost of each trip, as the server read them.
 *
 * @param {any} capture an `OrderCaptureResponse`
 * @returns {HTMLElement|null}
 */
export function captureRows(capture) {
  const cycles = Array.isArray(capture?.cycles) ? capture.cycles : [];
  const legs = (Array.isArray(capture?.legs) ? capture.legs : []).filter(
    (leg) => leg.vehicle || leg.km || leg.cost_vnd !== null || leg.note,
  );
  if (!cycles.length && !legs.length) return null;
  return list(
    [
      ...cycles.map((cycle) =>
        listRow({
          leading: "clock",
          title: `${cycle.kind === "REWASH" ? "Giặt lại" : "Mẻ giặt"} · ${
            cycle.machine_code ? `${cycle.machine_code}` : "chưa ghi máy"
          }`,
          meta: cycle.machine_name
            ? `${cycle.machine_name} · bắt đầu ${dateTime(cycle.started_at)}`
            : `Bắt đầu ${dateTime(cycle.started_at)}`,
          trailing: cycle.ended_at
            ? h("span", { class: "capture__minutes" }, `${cycle.minutes} phút`)
            : h("span", { class: "muted" }, "đang giặt"),
          data: { cycleMachine: String(cycle.machine_code || "") },
        }),
      ),
      ...legs.map((leg) =>
        listRow({
          leading: "truck",
          title: `${leg.leg_kind === "PICKUP" ? "Chuyến lấy đồ" : "Chuyến giao đồ"} · ${
            leg.vehicle ? VEHICLE_VI[leg.vehicle] || leg.vehicle : "chưa ghi xe"
          }`,
          meta: [leg.km ? `${String(leg.km).replace(".", ",")} km` : null, leg.note]
            .filter(Boolean)
            .join(" · "),
          trailing: h(
            "span",
            { class: "money", dataTripCost: String(leg.cost_vnd ?? "") },
            leg.cost_vnd === null ? UNKNOWN : money(leg.cost_vnd),
          ),
        }),
      ),
    ],
    { label: "Máy và chi phí chuyến" },
  );
}
