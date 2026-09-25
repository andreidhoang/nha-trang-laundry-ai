/**
 * Máy giặt, sấy: the shop's machine list (`SHOP-CAPTURE-001`, `DEC-038`), under Quản trị beside
 * Hệ thống.
 *
 * The list starts as the owner-confirmed machine master (`templates/machine-master.csv`, registered
 * by `scripts/seed_machines.py`). It is what *Bắt đầu giặt* asks "Máy nào?" from. The owner adds a
 * machine, renames one to what the shop calls it, or retires one that is sold or broken for good;
 * a retired machine keeps its history and is never offered at the counter again.
 *
 * Presentation only: which machines a wash can start on (`starts_cycle`), the order (most recently
 * used first) and the counts are the server's. Every edit carries the row version read (`If-Match`)
 * and one idempotency key per intent. Only the owner writes; everyone else sees the controls shut
 * with the reason.
 *
 * @module screens/machines
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { ago, integer } from "../core/format.js";
import { MACHINE_CATEGORY_VI } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated, markUpdated } from "../ui/components.js";
import {
  actionBar,
  button,
  confirmButton,
  emptyState,
  infoButton,
  inlineAlert,
  list,
  listRow,
  page,
  section,
  segmented,
  sheet,
  skeletonRows,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";

/** Tier 2: what the list is for. */
const LIST_RULE =
  "Khi bấm Bắt đầu giặt, quầy chọn một máy trong danh sách này (máy giặt, máy giặt khô, máy giặt " +
  "giày; máy sấy và bàn là không bắt đầu mẻ giặt). Nhờ vậy báo cáo biết mỗi máy chạy một mẻ mất bao " +
  "lâu. Máy ngưng dùng vẫn giữ lịch sử nhưng không hiện ở quầy nữa, và không bật lại được: máy mới " +
  "là một dòng mới với mã mới.";

/** The categories the owner can give a machine they add. */
const CATEGORIES = [
  "washer",
  "dryer",
  "dry_cleaner",
  "shoe_washer_dryer",
  "vacuum_ironing_table",
  "boiler_iron_set",
  "other",
];

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const me = principal();
  const writeVerdict = can(me, "MACHINES_WRITE");
  const host = h("div", { class: "stack stack--tight" }, skeletonRows(4));
  const truncation = h("div");
  const techHost = h("div");
  const stamp = h("span", { class: "updated", role: "status" });
  const sheetsHost = h("div");

  async function load() {
    render(host, skeletonRows(4));
    try {
      const found = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/machines?include_retired=true`,
      );
      markUpdated(stamp);
      const machines = Array.isArray(found?.machines) ? found.machines : [];
      render(
        host,
        machines.length
          ? list(machines.map(row), { label: "Máy của tiệm" })
          : emptyState({
              icon: "washer",
              title: "Chưa có máy nào",
              body: "Chủ tiệm chạy scripts/seed_machines.py một lần, hoặc bấm “Thêm máy”.",
            }),
      );
      render(
        truncation,
        found?.truncated
          ? inlineAlert({ state: "warn", title: "Chỉ hiện 100 máy đầu tiên" })
          : null,
      );
      render(techHost, techDetails([["Cửa hàng", String(found?.store_id || store)]]));
    } catch (error) {
      render(host, errorNotice(error, { onRetry: () => void load() }));
    }
  }

  /** @param {any} machine a `MachineResponse` */
  function row(machine) {
    const retired = Boolean(machine.retired_at);
    return listRow({
      leading: "washer",
      title: String(machine.code),
      meta: [
        String(machine.display_name),
        machine.starts_cycle
          ? machine.cycles
            ? `${integer(machine.cycles)} mẻ${machine.last_used_at ? `, dùng ${ago(machine.last_used_at)}` : ""}`
            : "chưa có mẻ nào"
          : "không chọn khi bắt đầu giặt",
      ].join(" · "),
      trailing: retired
        ? statusPill({ state: "neutral", text: "Ngưng dùng", token: "RETIRED" })
        : null,
      onClick: retired ? undefined : () => openMachine(machine),
      data: { machineCode: String(machine.code) },
    });
  }

  /** @type {ReturnType<typeof sheet>|null} */
  let open = null;

  /** @param {{title: string, body: unknown, actions?: unknown, id?: string}} spec */
  function openFresh(spec) {
    open?.close();
    const made = sheet({ ...spec, onClose: () => made.node.remove() });
    render(sheetsHost, made.node);
    open = made;
    made.open();
    return made;
  }

  /** Đổi tên / Ngưng dùng. @param {any} machine */
  function openMachine(machine) {
    const alertHost = h("div");
    const renameSubmission = new Submission("machine-rename");
    const retireSubmission = new Submission("machine-retire");
    const name = /** @type {HTMLInputElement} */ (
      h("input", {
        id: "machine-name",
        type: "text",
        maxlength: "60",
        value: String(machine.display_name),
        onInput: () => renameSubmission.reset(),
      })
    );
    /** @param {Record<string, unknown>} body @param {Submission} submission @param {string} done */
    async function send(body, submission, done) {
      render(alertHost);
      try {
        const target = encodeURIComponent(String(machine.machine_id));
        await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/machines/${target}`,
          {
            method: "PATCH",
            body,
            idempotencyKey: submission.key(),
            ifMatch: machine.row_version,
          },
        );
        submission.reset();
        toast(done);
        made.close();
        void load();
      } catch (error) {
        render(alertHost, errorNotice(error));
      }
    }
    const save = button({
      label: "Lưu tên",
      variant: "primary",
      block: true,
      network: true,
      id: "machine-rename",
      onClick: () =>
        void send({ display_name: name.value }, renameSubmission, `Đã đổi tên ${machine.code}`),
    });
    const retire = confirmButton({
      label: "Ngưng dùng máy này",
      confirmLabel: "Bấm lần nữa để ngưng dùng",
      block: true,
      onConfirm: () =>
        void send({ retire: true }, retireSubmission, `Đã ngưng dùng ${machine.code}`),
    });
    retire.id = "machine-retire";
    const made = openFresh({
      id: "machine-edit",
      title: String(machine.code),
      body: h(
        "div",
        { class: "stack" },
        h("label", { class: "field-label", for: "machine-name" }, "Tên máy"),
        name,
        alertHost,
      ),
      actions: h(
        "div",
        { class: "btn-stack" },
        gated(save, writeVerdict),
        gated(retire, writeVerdict),
      ),
    });
  }

  /** Thêm máy: a code, a name, a kind. */
  function openAdd() {
    const alertHost = h("div");
    const submission = new Submission("machine-create");
    let category = "";
    const code = /** @type {HTMLInputElement} */ (
      h("input", {
        id: "machine-code",
        type: "text",
        maxlength: "32",
        autocomplete: "off",
        autocapitalize: "characters",
        placeholder: "Ví dụ WASH-03",
        onInput: () => submission.reset(),
      })
    );
    const name = /** @type {HTMLInputElement} */ (
      h("input", {
        id: "machine-new-name",
        type: "text",
        maxlength: "60",
        placeholder: "Ví dụ Máy giặt Electrolux 10 kg",
        onInput: () => submission.reset(),
      })
    );
    const save = button({
      label: "Thêm máy",
      variant: "primary",
      block: true,
      network: true,
      id: "machine-create",
      onClick: async () => {
        render(alertHost);
        if (!code.value.trim() || !name.value.trim() || !category) {
          render(
            alertHost,
            inlineAlert({ state: "warn", title: "Cần mã máy, tên máy và loại máy." }),
          );
          return;
        }
        try {
          await request(`/internal/v1/stores/${encodeURIComponent(store)}/machines`, {
            method: "POST",
            idempotencyKey: submission.key(),
            body: { code: code.value.trim(), display_name: name.value.trim(), category },
          });
          submission.reset();
          toast(`Đã thêm ${code.value.trim().toUpperCase()}`);
          made.close();
          void load();
        } catch (error) {
          render(alertHost, errorNotice(error));
        }
      },
    });
    const made = openFresh({
      id: "machine-add",
      title: "Thêm máy",
      body: h(
        "div",
        { class: "stack" },
        h("label", { class: "field-label", for: "machine-code" }, "Mã máy (dán trên máy)"),
        code,
        h("label", { class: "field-label", for: "machine-new-name" }, "Tên máy"),
        name,
        h("p", { class: "field-label" }, "Loại máy"),
        segmented({
          label: "Loại máy",
          id: "machine-category",
          value: "",
          wrap: true,
          options: CATEGORIES.map((value) => ({ value, label: MACHINE_CATEGORY_VI[value] })),
          onChange: (value) => {
            category = value;
            submission.reset();
          },
        }),
        alertHost,
      ),
      actions: gated(save, writeVerdict),
    });
  }

  if (!store) {
    render(
      host,
      emptyState({ icon: "store", title: "Chưa chọn cửa hàng", body: "Chọn cửa hàng ở thanh trên." }),
    );
  } else {
    void load();
  }

  return h(
    "section",
    { class: "screen machines" },
    page({
      title: "Máy giặt, sấy",
      info: infoButton("Danh sách máy dùng để làm gì?", h("p", null, LIST_RULE)),
      action: stamp,
    }),
    section({ title: "Máy của tiệm", card: false, children: [truncation, host] }),
    techHost,
    sheetsHost,
    actionBar(
      gated(
        button({
          label: "Thêm máy",
          icon: "plus",
          variant: "primary",
          block: true,
          network: true,
          data: { machineAdd: "true" },
          onClick: () => openAdd(),
        }),
        writeVerdict,
      ),
    ),
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/machines",
  title: "Máy giặt, sấy",
  capability: "MACHINES_READ",
  needsStore: true,
  render: render_,
};
