/**
 * Xuất dữ liệu: the owner taking a copy of the shop's own days, with somebody accountable for it.
 *
 * `OPS-BOARD-001`. `ApprovalAction.EXPORT_SANITIZED_DATA` has existed since the first approval
 * migration with nothing behind it. This screen is the front of what is now behind it, and its
 * shape is the point rather than its convenience.
 *
 * **Three steps, deliberately not one button** — drawn as a stepper (Chọn ngày → Xin chủ tiệm
 * duyệt → Xuất tệp, spec V2 §5.8). An export moves the shop's records out of every control this
 * system has. `APPROVAL_POLICIES` maps the action to the owner-financial policy — owner role, MFA,
 * separation of duty, a ten-minute window — and that is owner policy, not a setting this console
 * may collapse. A single "Xuất" button would have to either skip the approval or hide it, and both
 * are the same lie. The one button in the action bar is always the *current* step's.
 *
 * **A window of days, since `EXPORT-RANGE-001`** (FR-RPT-003). The owner and the accountant close
 * a week or a month, not a day, so step 1 is a range picker — Hôm nay · 7 ngày · 30 ngày · Tháng
 * trước · Tự chọn — and the request carries `business_date` (first day) and `business_date_to`
 * (last day). Both are inside what the owner approves, so the server refuses to release one window
 * under another's approval; this screen only chooses. One day is sent as first == last, which the
 * server treats exactly as the one-day export it always was.
 *
 * The presets compute two calendar dates in the shop's timezone (`TIMEZONE`), never the device's:
 * a phone set to another zone must not export a different "today" from the one the server cuts on.
 * They are shown as the two dates before anything is sent, so what the person chose is what they
 * see. The 92-day bound is the server's: a longer custom window is sent and refused by name.
 *
 * **The requester cannot be the approver, and the screen says so before the refusal does**, on
 * the line beside "Xin chủ tiệm duyệt". In a shop with one owner that means the owner cannot both
 * ask and approve — the separation of duty working, and far kinder to learn here than at the
 * moment the approval is rejected.
 *
 * **"Sanitized" is shown as a list, not claimed as a word.** The server returns the exact columns
 * the file will carry and the exact things it withholds; both are rendered at step 2, uncollapsed,
 * before anything is requested. They are also what the owner's approval binds: widen the column
 * list and the rendered digest moves, so an approval already granted stops matching.
 *
 * **The window has no default.** A range nobody chose is a range nobody is accountable for having
 * exported, and "unknown means stop" is not only about prices: no preset is pressed on arrival.
 *
 * @module screens/exports
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { TIMEZONE, UNKNOWN, dateTime, integer, shortHash } from "../core/format.js";
import { REASON_NOTE } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId, subscribe } from "../core/session.js";
import {
  errorNotice,
  gated,
  gatedFields,
  labelled,
  resultLine,
  setResult,
} from "../ui/components.js";
import {
  actionBar,
  button,
  infoButton,
  keyValues,
  page,
  progress,
  section,
  segmented,
  show,
  techDetails,
  toast,
} from "../ui/kit.js";

/** `ApprovalAction.EXPORT_SANITIZED_DATA`, the one action this screen ever raises. */
const EXPORT_ACTION = "EXPORT_SANITIZED_DATA";

/** The range presets, in the order the owner reads them. */
const PRESETS = [
  { value: "today", label: "Hôm nay" },
  { value: "7d", label: "7 ngày" },
  { value: "30d", label: "30 ngày" },
  { value: "last-month", label: "Tháng trước" },
  { value: "custom", label: "Tự chọn" },
];

/**
 * The export in progress, per viewer and store, kept for the life of the page.
 *
 * Step 2 tells the owner to open `#/approvals` and come back, and until this existed coming back
 * rebuilt the screen from nothing: the window, the request and the approval id were gone, so the
 * "Xuất tệp" the instructions pointed at was not there, and the only way forward was to raise a
 * second request and a second envelope for the same days.
 *
 * Module state, in memory only -- invariant 3 of the UX refactor spec allows nothing else on a
 * shared counter phone. It survives moving between screens, which is the round trip the
 * instructions describe; it does not survive a reload, and the screen says so rather than
 * pretending otherwise. Keyed by staff user and store, so a different person signing in on the
 * same tab -- or the same person switching store -- never inherits someone else's export.
 *
 * @type {Map<string, {preset: string, from: string, to: string, created: any, approvalId: string}>}
 */
const inProgress = new Map();

// Signing out does not always reload the page (there is no issuer to navigate to on some
// deployments), so the stash is dropped the moment the session has nobody in it. That is what
// makes the screen's own sentence -- "đăng xuất thì không còn nhớ" -- true on every deployment.
subscribe(() => {
  if (!principal()) inProgress.clear();
});

/**
 * @param {any} me the session principal
 * @param {string|null} store
 * @returns {string}
 */
function progressKey(me, store) {
  return `${me?.staffUserId || ""}|${store || ""}`;
}

/**
 * Today's date in the shop's timezone, as `YYYY-MM-DD`.
 *
 * Read from the device clock but cut in `TIMEZONE`, the zone the server's `orders.created_at`
 * boundary uses. A device clock that is wrong shows a wrong window on screen before anything is
 * sent — the two dates are printed — rather than silently exporting other days.
 *
 * @param {Date} [now]
 * @returns {string}
 */
export function shopToday(now = new Date()) {
  /** @type {Record<string, string>} */
  const parts = {};
  for (const part of new Intl.DateTimeFormat("en-CA", {
    timeZone: TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now)) {
    parts[part.type] = part.value;
  }
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/**
 * A calendar date moved by whole days. Pure calendar arithmetic on the date, no clock and no zone:
 * `YYYY-MM-DD` in, `YYYY-MM-DD` out.
 *
 * @param {string} iso
 * @param {number} days
 * @returns {string}
 */
function shiftDays(iso, days) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/**
 * The first and last day a preset names, counted back from `today` (inclusive of today).
 *
 * @param {string} preset
 * @param {string} today `YYYY-MM-DD` in the shop's timezone
 * @returns {{from: string, to: string} | null} null for "Tự chọn", whose days the person types
 */
export function presetWindow(preset, today) {
  if (preset === "today") return { from: today, to: today };
  if (preset === "7d") return { from: shiftDays(today, -6), to: today };
  if (preset === "30d") return { from: shiftDays(today, -29), to: today };
  if (preset === "last-month") {
    const [year, month] = today.split("-").map(Number);
    const first = new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 10);
    const last = new Date(Date.UTC(year, month - 1, 0)).toISOString().slice(0, 10);
    return { from: first, to: last };
  }
  return null;
}

/**
 * `YYYY-MM-DD` as the day a person reads: `16/09/2026`.
 *
 * @param {unknown} value
 * @returns {string}
 */
function dayVi(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? ""));
  return match ? `${match[3]}/${match[2]}/${match[1]}` : String(value ?? UNKNOWN);
}

/**
 * A window in human form: one day as that day, several as `01/09/2026 → 07/09/2026`.
 *
 * @param {unknown} from
 * @param {unknown} to
 * @returns {string}
 */
function windowVi(from, to) {
  return !to || to === from ? dayVi(from) : `${dayVi(from)} → ${dayVi(to)}`;
}

/**
 * The window a server answer names, with its day count from the server (never recomputed here).
 *
 * @param {any} record a request, disclosure or production response
 * @returns {string}
 */
function servedWindow(record) {
  const to = record.business_date_to || record.business_date;
  const days = Number(record.window_days || 1);
  return days > 1
    ? `${windowVi(record.business_date, to)} · ${integer(days)} ngày`
    : `Ngày ${dayVi(record.business_date)}`;
}

/**
 * The reason code a refusal carried, wherever the server put it.
 *
 * A `REQUIRE_HUMAN` refusal arrives with `reasonCodes`; a conflict arrives with the bare code as
 * its message. Both are the server naming one thing that is wrong, and the screen treats them the
 * same way so that a staff member gets the same sentence either way.
 *
 * @param {any} error
 * @returns {string}
 */
function reasonCodeOf(error) {
  const fromList = Array.isArray(error?.reasonCodes) ? error.reasonCodes[0] : "";
  // `detail`, not `message`: the message is the Vietnamese title `classify` gives a refusal, and
  // the bare code the server sent (`EXPORT_ALREADY_PRODUCED`, …) is kept verbatim in `detail`.
  return fromList || (typeof error?.detail === "string" ? error.detail : "");
}

/**
 * The plain-language note for a refusal this screen recognises, beside the server's own words.
 *
 * @param {any} error
 * @returns {HTMLElement|null}
 */
function refusalNote(error) {
  const note = REASON_NOTE[reasonCodeOf(error)];
  if (!note) return null;
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Máy chủ từ chối vì một điều kiện, không phải vì lỗi hệ thống"),
    h("p", null, note),
  );
}

/**
 * What the file will and will not carry, from the server rather than from this file's memory.
 *
 * Rendered from the request's own answer, so it cannot drift from what the export actually does:
 * the same two lists are hashed into the document the owner approves. It stays on screen (tier 1)
 * at step 2, beside "Xin chủ tiệm duyệt", because it is exactly what the requester is asking
 * somebody to release.
 *
 * The day boundary matters for a narrower and sharper reason. This console shows two numbers under
 * the words *tiền đã thu*: the takings box on `#/today`, summed over when money was taken, and the
 * money columns in this file, which belong to the orders OPENED on the named days. `statement_vi`
 * is the server's own sentence — the string hashed into `rendered_hash` — and is printed verbatim
 * rather than paraphrased, because a paraphrase is a description of a document nobody signed. The
 * raw boundary column and the query version sit in the technical drawer below it.
 *
 * @param {any} created
 * @returns {HTMLElement}
 */
function contentsNotice(created) {
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Bản xuất này mang gì, và cố ý không mang gì"),
    h(
      "p",
      null,
      "Có: ",
      h("span", { class: "mono" }, created.columns.join(", ")),
      ".",
    ),
    h(
      "p",
      null,
      "Không có: ",
      h("span", { class: "mono" }, created.excludes.join(", ")),
      ". Lời khách phàn nàn và mô tả bằng chứng nằm trong lịch xoá dữ liệu của hệ thống; một bản " +
        "sao mang ra ngoài sẽ không còn được lịch đó bảo vệ, nên nó không được mang ra.",
    ),
    h("p", null, created.statement_vi || UNKNOWN),
  );
}

/**
 * Hand the produced file to the browser, once, without keeping it anywhere.
 *
 * An object URL over an in-memory blob: nothing is written to the device by this console, which is
 * the same rule every other screen obeys. The URL is revoked immediately after the click, so the
 * bytes do not outlive the download the operator asked for.
 *
 * @param {any} produced
 */
function download(produced) {
  const blob = new Blob([produced.content_csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const to = produced.business_date_to || produced.business_date;
  const days = to === produced.business_date ? produced.business_date : `${produced.business_date}_${to}`;
  const anchor = h("a", {
    href: url,
    download: `ntl-${days}-${String(produced.export_id).slice(0, 8)}.csv`,
  });
  anchor.click();
  URL.revokeObjectURL(url);
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const me = principal();
  const verdict = can(me, "EXPORT_DATA");
  const requestSubmission = new Submission("export-request");
  const approvalSubmission = new Submission("export-approval");
  const executeSubmission = new Submission("export-execute");
  const key = progressKey(me, store);
  const saved = inProgress.get(key) || null;

  /** @type {{preset: string, from: string, to: string}} */
  const draft = { preset: saved?.preset || "", from: saved?.from || "", to: saved?.to || "" };

  /**
   * The request this screen is working on, the envelope raised for it, and the file once made.
   * In memory only. Restored from `inProgress` so the conversation survives the trip to
   * `#/approvals` and back; the produced file is deliberately not restored.
   *
   * @type {{created: any, approvalId: string, produced: any}}
   */
  const stage = { created: saved?.created || null, approvalId: saved?.approvalId || "", produced: null };

  /** Record where this viewer is, or forget it once there is nothing in progress. */
  function remember() {
    if (stage.created && !stage.produced) {
      inProgress.set(key, {
        preset: draft.preset,
        from: draft.from,
        to: draft.to,
        created: stage.created,
        approvalId: stage.approvalId,
      });
    } else {
      inProgress.delete(key);
    }
  }

  const result = resultLine();
  const errorHost = h("div", { class: "stack" });
  const stepsHost = h("div");
  const stageHost = h("div", { class: "stack" });
  const customHost = h("div");
  const chosenHost = h("p", { class: "export-range__chosen", id: "export-window", "aria-live": "polite" });
  const barHost = actionBar();

  /**
   * The window changed. Everything staged for the previous one goes, envelope included: leaving
   * the old approval on screen beside a new window is how somebody exports days nobody approved.
   */
  function windowChanged() {
    // Every key belongs to the intent it was minted for. A new window is a new request, so an
    // envelope or release key left over from a failed attempt on the old one must not be reused.
    requestSubmission.reset();
    approvalSubmission.reset();
    executeSubmission.reset();
    stage.created = null;
    stage.approvalId = "";
    stage.produced = null;
    remember();
    render(errorHost);
    setResult(result, null, null);
    paintChosen();
    paint();
  }

  /** @param {"from"|"to"} end @param {string} id @param {string} label */
  function dateField(end, id, label) {
    return labelled({
      id,
      label,
      control: h("input", {
        type: "date",
        autocomplete: "off",
        value: draft[end],
        onInput: (/** @type {any} */ event) => {
          draft[end] = event.target.value;
          windowChanged();
        },
      }),
    });
  }

  function paintCustom() {
    render(
      customHost,
      draft.preset === "custom"
        ? h(
            "div",
            { class: "export-range__custom" },
            dateField("from", "export-from", "Từ ngày"),
            dateField("to", "export-to", "Đến hết ngày"),
          )
        : null,
    );
  }

  function paintChosen() {
    render(chosenHost, draft.from && draft.to ? windowVi(draft.from, draft.to) : null);
  }

  const picker = segmented({
    label: "Khoảng ngày cần xuất",
    id: "export-range",
    wrap: true,
    value: draft.preset,
    options: PRESETS,
    onChange: (value) => {
      draft.preset = value;
      const chosen = presetWindow(value, shopToday());
      // "Tự chọn" keeps whatever the two fields already hold, so switching to it from a preset
      // lets the person adjust the preset's days rather than start again from nothing.
      if (chosen) {
        draft.from = chosen.from;
        draft.to = chosen.to;
      }
      paintCustom();
      windowChanged();
    },
  });

  /**
   * @param {unknown} error
   * @param {string} title
   */
  function refused(error, title) {
    setResult(result, "danger", title);
    show(errorHost, errorNotice(error), refusalNote(error));
  }

  /**
   * Step 1. Record what is wanted. Nothing leaves the system on this call.
   */
  async function createRequest() {
    render(errorHost);
    if (!draft.from || !draft.to) {
      setResult(result, "danger", "Chưa chọn khoảng ngày. Bản xuất luôn thuộc về những ngày cụ thể.");
      return;
    }
    setResult(result, "warn", "Đang ghi yêu cầu xuất…");
    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/exports`, {
        method: "POST",
        body: { business_date: draft.from, business_date_to: draft.to },
        idempotencyKey: requestSubmission.key(),
      });
      requestSubmission.reset();
      stage.created = created;
      stage.approvalId = "";
      stage.produced = null;
      remember();
      setResult(result, null, null);
      toast(
        created.replayed
          ? "Yêu cầu cho khoảng này đã được ghi trước đó; không có yêu cầu mới nào được tạo."
          : "Đã ghi yêu cầu xuất. Chưa có dữ liệu nào ra khỏi hệ thống.",
      );
      paint();
    } catch (error) {
      refused(error, "Không ghi được yêu cầu xuất.");
    }
  }

  /**
   * Step 2. Raise the owner envelope from the binding the server handed back.
   *
   * The digests are sent back exactly as they arrived. A console that derived its own would be
   * asking the owner to approve a document the server never stored, and invariant 8 would then be
   * binding an approval to content nobody can produce again.
   */
  async function raiseApproval() {
    const created = stage.created;
    if (!created) return;
    render(errorHost);
    setResult(result, "warn", "Đang tạo phong bì duyệt…");
    try {
      const approval = await request("/internal/v1/approvals", {
        method: "POST",
        body: {
          store_id: created.store_id,
          action: EXPORT_ACTION,
          resource_type: created.resource_type,
          resource_id: created.export_request_id,
          resource_version: created.resource_version,
          snapshot_hash: created.snapshot_hash,
          rendered_hash: created.rendered_hash,
          policy_version: created.policy_version,
        },
        idempotencyKey: approvalSubmission.key(),
      });
      approvalSubmission.reset();
      stage.approvalId = approval.approval_request_id;
      remember();
      setResult(result, null, null);
      toast("Đã xin duyệt. Chờ một chủ tiệm khác bấm Duyệt.");
      paint();
    } catch (error) {
      refused(error, "Không tạo được phong bì duyệt.");
    }
  }

  /**
   * Step 3. Release the file, once.
   *
   * The server refuses a second attempt by name. That refusal is not a failure to handle quietly:
   * one approval releases one file, and an operator who taps twice must be told the first file is
   * the one they were given rather than handed a second copy nobody approved.
   */
  async function produce() {
    const created = stage.created;
    if (!created || !stage.approvalId) return;
    render(errorHost);
    setResult(result, "warn", "Đang xuất…");
    try {
      const produced = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/exports/${encodeURIComponent(created.export_request_id)}/execution`,
        {
          method: "POST",
          body: { approval_id: stage.approvalId },
          idempotencyKey: executeSubmission.key(),
        },
      );
      executeSubmission.reset();
      // One approval releases one file, and this one has. Nothing is in progress any more, so a
      // later visit starts clean instead of offering "Xuất tệp" for an envelope already spent.
      stage.produced = produced;
      remember();
      setResult(result, null, null);
      toast(
        `Đã xuất ${produced.row_count} dòng cho ${windowVi(produced.business_date, produced.business_date_to)}. ` +
          "Bản ghi việc xuất này đã vào sổ kiểm toán kèm phong bì duyệt.",
      );
      paint();
    } catch (error) {
      setResult(result, "warn", "Máy chủ chưa cho xuất bản này.");
      show(errorHost, errorNotice(error), refusalNote(error));
    }
  }

  /** @returns {number} 0 choose the days · 1 ask for approval · 2 produce · 3 done */
  function currentStep() {
    if (stage.produced) return 3;
    if (stage.approvalId) return 2;
    if (stage.created) return 1;
    return 0;
  }

  /** The stepper, the staged content, and the one button that is next. */
  function paint() {
    const step = currentStep();
    const labels = ["Chọn ngày", "Xin chủ tiệm duyệt", "Xuất tệp"];
    render(
      stepsHost,
      progress(
        labels.map((label, index) => ({
          label,
          state: index < step ? "done" : index === step ? "current" : "todo",
        })),
        { label: "Các bước xuất dữ liệu" },
      ),
    );

    const created = stage.created;
    const produced = stage.produced;
    render(
      stageHost,
      created
        ? section({
            title: servedWindow(created),
            children: h(
              "div",
              { class: "stack" },
              stage.approvalId && !produced
                ? h(
                    "div",
                    { class: "notice", dataState: "warn" },
                    h("p", { class: "notice__title" }, "Chờ chủ tiệm duyệt"),
                    h(
                      "p",
                      null,
                      "Phong bì có hiệu lực 10 phút và phải do một chủ tiệm khác với người đã tạo " +
                        "yêu cầu xuất này quyết định. Mở ",
                      h("a", { href: "#/approvals" }, "màn hình Duyệt"),
                      " để chủ tiệm xử lý, rồi quay lại bấm Xuất tệp.",
                    ),
                    h(
                      "p",
                      { class: "hint" },
                      "Màn hình này nhớ yêu cầu và phong bì khi bạn chuyển sang màn hình khác rồi quay " +
                        "lại. Tải lại trang hoặc đăng xuất thì không còn nhớ.",
                    ),
                  )
                : null,
              contentsNotice(created),
              step === 1
                ? h(
                    "p",
                    { class: "hint", dataState: "danger" },
                    "Người tạo yêu cầu không tự duyệt được — cần một chủ tiệm khác bấm Duyệt.",
                  )
                : null,
              produced
                ? h(
                    "div",
                    { class: "stack stack--tight" },
                    keyValues([
                      ["Số dòng", integer(produced.row_count)],
                      ["Khoảng ngày", windowVi(produced.business_date, produced.business_date_to)],
                      ["Xuất lúc", dateTime(produced.produced_at)],
                    ]),
                    h(
                      "p",
                      { class: "hint" },
                      "Hệ thống không giữ lại nội dung tệp — chỉ giữ vân tay, số dòng và phong bì duyệt. " +
                        "Tệp đã tải về nằm ngoài mọi lịch xoá dữ liệu của hệ thống; giữ nó ở đâu là trách " +
                        "nhiệm của người đã tải.",
                    ),
                  )
                : null,
              techDetails([
                ["Mã yêu cầu", created.export_request_id, { copy: String(created.export_request_id) }],
                ["Bộ dữ liệu", created.dataset],
                [
                  "Khoảng ngày",
                  `${created.business_date} … ${created.business_date_to || created.business_date}`,
                ],
                ["Vân tay nội dung được duyệt", shortHash(created.rendered_hash)],
                ["Cắt ngày theo", created.day_boundary || UNKNOWN],
                ["Truy vấn", created.query_version],
                stage.approvalId
                  ? ["Phong bì duyệt", stage.approvalId, { copy: stage.approvalId }]
                  : ["Phong bì duyệt", `${UNKNOWN} chưa tạo`],
                produced ? ["Vân tay tệp", shortHash(produced.content_hash)] : null,
              ]),
            ),
          })
        : null,
    );

    /** @type {HTMLElement[]} */
    const next = [];
    if (step === 0) {
      next.push(
        gated(
          button({
            label: "Tạo yêu cầu xuất",
            variant: "primary",
            network: true,
            block: true,
            id: "export-create",
            onClick: () => void createRequest(),
          }),
          verdict,
        ),
      );
    } else if (step === 1) {
      next.push(
        gated(
          button({
            label: "Xin chủ tiệm duyệt",
            variant: "primary",
            network: true,
            block: true,
            id: "export-approval",
            onClick: () => void raiseApproval(),
          }),
          verdict,
        ),
      );
    } else if (step === 2) {
      next.push(
        gated(
          button({
            label: "Xuất tệp",
            variant: "primary",
            network: true,
            block: true,
            id: "export-execute",
            onClick: () => void produce(),
          }),
          verdict,
        ),
      );
    } else if (produced) {
      next.push(
        button({
          label: "Tải tệp CSV",
          variant: "primary",
          block: true,
          icon: "download",
          id: "export-download",
          onClick: () => download(produced),
        }),
      );
    }
    render(barHost, ...next);
  }

  const form = gatedFields(
    h(
      "form",
      {
        class: "form export-range",
        onSubmit: (/** @type {Event} */ event) => {
          event.preventDefault();
          void createRequest();
        },
      },
      picker,
      customHost,
      chosenHost,
      h(
        "p",
        { class: "hint" },
        "Đơn tính theo ngày mở đơn, giờ Việt Nam — không theo lúc thu tiền. Tối đa 92 ngày.",
      ),
    ),
    verdict,
  );

  // Coming back from `#/approvals`: put the viewer exactly where they left off.
  if (stage.created) {
    setResult(
      result,
      "ok",
      `Đang tiếp tục yêu cầu xuất cho ${windowVi(stage.created.business_date, stage.created.business_date_to)}` +
        (stage.approvalId ? " · đã xin duyệt." : "."),
    );
  }
  paintCustom();
  paintChosen();
  paint();

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Xuất dữ liệu",
      subtitle: "Hồ sơ đơn theo ngày · chủ tiệm duyệt",
      info: infoButton(
        "Bản xuất này là gì?",
        h(
          "p",
          { class: "hint" },
          "Lấy bản sao hồ sơ của chính cửa hàng cho một ngày hoặc một khoảng ngày (tối đa 92 ngày): " +
            "mã đơn, trạng thái, mốc thời gian và số tiền đã thu của những đơn mở trong những ngày đó. " +
            "Ngày cắt theo lúc mở đơn, nên tổng tiền trong tệp không bằng các ô “tiền đã thu hôm nay” " +
            "ở màn hình Hôm nay — ô đó cộng theo lúc thu. Mỗi lần xuất đều cần chủ tiệm duyệt và đều " +
            "được ghi lại.",
        ),
        h(
          "p",
          { class: "hint" },
          "Không có khoảng ngày mặc định — bạn chọn khoảng nào thì bạn chịu trách nhiệm khoảng đó. " +
            "Chủ tiệm duyệt đúng hai ngày đầu và cuối đã chọn; duyệt khoảng này không xuất được " +
            "khoảng khác.",
        ),
        h(
          "p",
          { class: "hint" },
          "Người tạo yêu cầu xuất không được tự duyệt yêu cầu của mình — kể cả khi phong bì duyệt " +
            "do người khác mở. Quy tắc tính theo người đã chọn dữ liệu nào rời khỏi hệ thống, và máy " +
            "chủ từ chối; đó là tách trách nhiệm chứ không phải lỗi. Một lần duyệt cho đúng một tệp.",
        ),
      ),
    }),
    stepsHost,
    section({ title: "Khoảng ngày", children: form }),
    stageHost,
    result,
    errorHost,
    barHost,
  );
}

export const screen = {
  path: "/exports",
  title: "Xuất dữ liệu",
  capability: "EXPORT_DATA",
  needsStore: true,
  render: render_,
};
