/**
 * Quotes: price a request through the deterministic engine, and read back what it decided.
 *
 * This screen is the reason the console exists, and it is also the easiest place to do harm, so a
 * few of its choices are deliberate rather than incidental:
 *
 *   - **The quantity's digits are sent exactly as typed.** Not trimmed of a trailing zero, not
 *     parsed into a number and re-serialized: `6` and `6.0` stay different strings. The one
 *     rewrite is the decimal mark -- "5,5", the way the counter writes it, is sent as "5.5",
 *     because that is the only spelling the domain's `QUANTITY_PATTERN` accepts -- and it is done by
 *     `parseQuantity`, which also refuses inline whatever the domain would refuse, so a bad weight
 *     is caught while typing and the 6 kg notice reads the same number the server does.
 *   - **No total is ever computed here.** The response carries a service subtotal and, separately,
 *     a display total that is usually null because the delivery fee is unresolved. Adding the first
 *     to a guess at the second is precisely the defect `ENGINEERING_SPEC_V1.md:530` forbids.
 *   - **`net_service_subtotal_vnd` is not shown at all on a band.** The API gives it a scalar name
 *     but the service layer fills it from the domain's range *maximum*
 *     (`operations.py` — `totals.net_service_subtotal_max_vnd`), and the same is true of
 *     `list_service_subtotal_vnd`. This module's earlier note said the two coincided "today,
 *     because range-priced services are refused outright", and named what would happen the day
 *     they did not: a bare number showing the top of a range as a settled price. `RANGE-PRICE-001`
 *     is that day. So on a `RANGE` revision both rows read `—` with the reason stated, and the
 *     band is read from `display_total_min_vnd`/`display_total_max_vnd`, which are a real pair.
 *   - **Closing a band is three steps and the console never shortens them.** Twenty of the
 *     forty-four published services carry an interval rather than a rate. The counter asks for a
 *     band revision (`present_range_as_band`), types one amount per banded line, and the server
 *     raises a `SET_RANGE_PRICE` envelope that a *second* person approves on `#/approvals`; only
 *     then does applying it write the price. The screen holds the amounts between the second and
 *     third step and says so, because nothing on the server stores them in between — the envelope
 *     binds a digest, not the content.
 *   - **The band mode is asked for, never assumed.** `GET /internal/v1/pricebook/services` carries
 *     no price kind, so this screen genuinely cannot know which services are banded before it
 *     prices one. It therefore submits the ordinary way, and when the engine answers
 *     `RANGE_PRICE_REQUIRES_HUMAN` it offers one button that re-sends the same lines asking for a
 *     band. Guessing the flag on every quote would make a total-less revision the default outcome
 *     for a caller expecting a price.
 *   - **A refusal is a result, not an error.** A 422 carrying `REQUIRE_HUMAN` means the engine
 *     declined to guess. It is rendered as an outcome with its reason codes intact.
 *   - **The service is picked by name, never typed as a code.** The picker offers the published
 *     pricebook's Vietnamese display names (`GET /internal/v1/pricebook/services`), grouped by
 *     category; the unit follows the chosen service because every service has exactly one
 *     canonical unit. If the catalog cannot be read, the form refuses too — the same digest gate
 *     that prices guards the picker, and a memorized code is not a fallback worth keeping.
 *   - **The order request is picked or prefilled, not pasted.** The common path is the Tiếp nhận
 *     screen's "Báo giá ngay" link (`#/quotes?request=<id>`, resolved against the server before
 *     the form binds it) or a pick from the recent-intake list below the form. A bare UUID field
 *     remains as a collapsed fallback for recovery, because a copied id from another system is how
 *     a quote lands on the wrong customer.
 *
 * @module screens/quotes
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { BAND, bandReadiness, bandVerdict } from "../core/bands.js";
import { h, render } from "../core/dom.js";
import {
  UNKNOWN,
  UUID,
  countdown,
  dateTime,
  matchesFilter,
  money,
  parseDong,
  parseQuantity,
  quantity as quantityText,
  shortHash,
  shortId,
} from "../core/format.js";
import { enumVi, serviceCategoryVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  amount,
  badge,
  bandInput,
  copyable,
  enumSelect,
  errorNotice,
  facts,
  gated,
  icon,
  labelled,
  listView,
  panel,
  priceStateBadge,
  pricingCliffNotice,
  reasonCodeList,
  resultLine,
  revealError,
  setResult,
  skeleton,
  warningBadges,
} from "../ui/components.js";

const BASES = ["STAFF_MEASUREMENT", "CUSTOMER_ESTIMATE", "APPROVED_MANUAL"];
// The server's FulfillmentMode enum, in the order an operator meets them: the walk-in case is
// the commonest and the only one that resolves a fee without a measured distance.
const FULFILLMENT_MODES = [
  "SELF_DROP_SELF_COLLECT",
  "PICKUP_AND_RETURN",
  "PICKUP_ONLY",
  "RETURN_ONLY",
];
const LIST_LIMIT = 100;
const MAX_LINES = 20;

/**
 * `QuoteLineRequest.service_code`, mirrored byte for byte from `main.py`'s `Field(pattern=...)`.
 *
 * It used to catch an operator's typo. Nobody can type a service code any more, so what it catches
 * now is a published pricebook offering a code the API would reject — a pricebook problem, and the
 * refusal message says so rather than blaming the person who only picked from a list.
 */
const SERVICE_CODE = /^[A-Z][A-Z0-9_]{1,62}$/;

/**
 * How many recent intakes the picker asks for. The endpoint caps at 100, so a full picker is a
 * truncation the screen discloses rather than a total it poses as.
 */
const PICKER_LIMIT = 100;

/**
 * The engine's word for "this line is priced by inspection and nobody has chosen a number yet".
 *
 * Matched on rather than interpreted: it is the one code whose presence in a pricing refusal means
 * the same lines would succeed if the caller asked for a band instead, which is the only place in
 * this screen where a refusal has a next action the console can offer.
 */
const NEEDS_A_HUMAN_PRICE = "RANGE_PRICE_REQUIRES_HUMAN";

/** One second, as on the approvals queue. An envelope's remaining time is the point. */
const TICK_MS = 1000;

/**
 * `ApprovalAction.SET_RANGE_PRICE` maps to `_OWNER_FINANCIAL`, which is a ten-minute envelope, and
 * the server enforces expiry at the decision *and* again when the price is applied — so the
 * owner's approval and the staff member's press must both land inside one window.
 *
 * Not a number this screen computes anything from: `expires_at` comes off the raised envelope and
 * drives the countdown. It is written down because the sentence the counter reads has to say ten
 * minutes, and a sentence quoting a figure nothing in the file explains is how copy goes stale.
 */
const OWNER_FINANCIAL_TTL_VI = "mười phút";

/**
 * One editable line. Kept as plain state rather than read from the DOM at submit time, so that the
 * value sent is provably the value shown.
 *
 * @typedef {{serviceCode: string, quantity: string, unit: string, basis: string}} Line
 */

/**
 * One published service, as the catalog route returns it.
 *
 * @typedef {{code: string, display_name: string, category: string, unit: string}} CatalogService
 */

/**
 * Group the catalog by category, in the order the published payload lists them.
 *
 * No sorting happens here on purpose. The payload's order is the published pricebook's order, and
 * a picker that re-sorts puts the services in a different sequence than the document the owner
 * approved — a difference nobody can see and everybody has to relearn. Grouping is the one
 * rearrangement worth making, because "Ủi" and "Giặt khô" are how the counter thinks about the
 * work; within a group the published order stands.
 *
 * @param {CatalogService[]} services
 * @returns {Array<{category: string, services: CatalogService[]}>}
 */
function serviceGroups(services) {
  /** @type {Array<{category: string, services: CatalogService[]}>} */
  const groups = [];
  for (const service of services) {
    let group = groups.find((item) => item.category === service.category);
    if (!group) {
      group = { category: service.category, services: [] };
      groups.push(group);
    }
    group.services.push(service);
  }
  return groups;
}

/**
 * Why a typed quantity cannot be sent, in the counter's words. Shared by the inline note under the
 * field and by `validate()`, so the sentence read while typing is the one read after pressing.
 *
 * @param {string} typed
 * @param {string} unit
 * @returns {string}
 */
function quantityRefusal(typed, unit) {
  const byCount = unit && unit !== "KG";
  return (
    `Không đọc được “${typed}”. Gõ một số, ví dụ 5,5 hoặc 5.5 — lớn hơn 0, tối đa 3 chữ số sau ` +
    "dấu thập phân, không kèm chữ." +
    (byCount ? " Món tính theo cái/đôi/bộ thì phải là số nguyên." : "")
  );
}

/**
 * @returns {Line}
 */
function blankLine() {
  // No unit, because the unit is the chosen service's and no service is chosen yet. The old `"KG"`
  // default was a guess the operator could neither see nor change once the unit picker was gone.
  return { serviceCode: "", quantity: "", unit: "", basis: "STAFF_MEASUREMENT" };
}

/**
 * The line editor.
 *
 * Two kinds of change happen here and they must not be confused, because the first version of this
 * screen confused them and the result was a form that dropped focus on every keystroke.
 *
 *   **Structural** — a line added or removed. The set of cards changes, so the editor is rebuilt.
 *   **Value** — a character typed, a service picked. The set of cards is unchanged, so nothing is
 *   rebuilt: the line's state is updated in place and only the nodes that actually depend on the
 *   value are refreshed — the unit echo, and that line's 6 kg notice.
 *
 * The weight field is where this still matters. Typing into it used to rebuild the whole form on
 * every keystroke and move the caret to the end after each one; a Playwright check does not catch
 * that, because `page.fill()` sets a value in one shot and only a human typing does. The service
 * field no longer has the problem at all, because picking from a list is one event rather than
 * seventeen — which is a second, quieter reason the picker replaced the typed code.
 *
 * @param {object} options
 * @param {CatalogService[]} options.catalog the published services the picker offers
 * @param {Line[]} options.lines
 * @param {() => void} options.onStructuralChange rebuild — the set of lines changed
 * @param {() => void} options.onValueChange a value changed; invalidates the idempotency key only
 * @param {() => void} options.onAddLine Enter in a quantity field — same path as "Thêm dòng"
 * @returns {HTMLElement}
 */
function lineEditor({ catalog, lines, onStructuralChange, onValueChange, onAddLine }) {
  // Grouped once, not once per line. Twenty lines × forty-three services is a rearrangement the
  // browser would otherwise redo on every structural rebuild, for an answer that cannot differ.
  const groups = serviceGroups(catalog);

  return h(
    "div",
    { class: "stack" },
    lines.map((line, index) => {
      const prefix = `quote-line-${index}`;
      // Owned by this card and refreshed in place, so the notice can follow the typed weight across
      // the 6 kg boundary without the input losing focus.
      const cliffHost = h("div");
      // The 6 kg notice is about a *priced* weight, so it says nothing until there is a service to
      // price. Before a pick there is no unit to be near a boundary of, and an empty quantity
      // parses to NaN — which the notice treats as "near the cliff" and would announce on every
      // blank line the operator adds.
      const refreshCliff = () => {
        render(cliffHost, line.serviceCode ? pricingCliffNotice(line.quantity, line.unit) : null);
        refreshQuantity();
      };

      // What the server will receive for this line, read by the same `parseQuantity` the send path
      // and the 6 kg notice use. Silent while the box is empty or already in the server's spelling;
      // says so when a comma is about to become a dot; refuses inline when nothing can be sent.
      // `form__result` rather than a new style: it already hides when empty and turns red on
      // `data-state="danger"`, which is exactly the refused-weight case.
      const quantityNote = h("p", { class: "form__result", "aria-live": "polite" });
      const refreshQuantity = () => {
        const typed = line.quantity.trim();
        const accepted = typed ? parseQuantity(typed, line.unit) : null;
        quantityInput.setAttribute("aria-invalid", typed && accepted === null ? "true" : "false");
        if (!typed || accepted === typed) {
          quantityNote.removeAttribute("data-state");
          render(quantityNote);
        } else if (accepted === null) {
          quantityNote.dataset.state = "danger";
          render(quantityNote, quantityRefusal(typed, line.unit));
        } else {
          quantityNote.removeAttribute("data-state");
          render(quantityNote, `Máy chủ sẽ nhận: ${accepted}`);
        }
      };

      // The unit is shown, never chosen. Every published service has exactly one canonical unit,
      // so offering a second control was offering the operator a way to contradict the pricebook —
      // and the server refuses that contradiction (INCOMPATIBLE_UNIT) rather than pricing it.
      const unitEcho = h("p", { class: "hint" });
      const refreshUnit = () =>
        render(unitEcho, line.serviceCode ? `Tính theo: ${enumVi(line.unit)}` : null);

      // The picker. One tap chooses by the name the pricebook was approved with; the code travels
      // as the option's value, and the unit follows the service.
      const serviceSelect = h(
        "select",
        {
          name: "service_code",
          onChange: (event) => {
            const select = event.target;
            line.serviceCode = select.value;
            const service = catalog.find((item) => item.code === select.value);
            // Back to the placeholder clears the unit too. Keeping the previous service's unit on
            // a line with no service is a value nobody set, and the 6 kg notice reads it.
            line.unit = service ? service.unit : "";
            refreshUnit();
            refreshCliff();
            onValueChange();
          },
        },
        h("option", { value: "", selected: !line.serviceCode }, "— Chọn dịch vụ —"),
        groups.map((group) =>
          h(
            "optgroup",
            { label: serviceCategoryVi(group.category) },
            group.services.map((service) =>
              h(
                "option",
                { value: service.code, selected: service.code === line.serviceCode, title: service.code },
                service.display_name,
              ),
            ),
          ),
        ),
      );

      const quantityInput = h("input", {
        // Deliberately `text`, not `number`. A number input lets the browser normalize, step and
        // localize the value; `parseQuantity` is the only thing allowed to read it.
        type: "text",
        inputmode: "decimal",
        value: line.quantity,
        autocomplete: "off",
        maxlength: "16",
        placeholder: "6",
        onInput: (event) => {
          line.quantity = event.target.value;
          refreshCliff();
          onValueChange();
        },
        // A keydown, not an input event: it never touches the value channel above, it only takes
        // the structural path the "Thêm dòng" button takes. `preventDefault` keeps Enter inside a
        // form from submitting the quote when the operator meant another line.
        onKeydown: (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            onAddLine();
          }
        },
      });

      const basisSelect = enumSelect("quantity_basis", BASES, line.basis);
      basisSelect.addEventListener("change", (event) => {
        line.basis = /** @type {HTMLSelectElement} */ (event.target).value;
        onValueChange();
      });

      // Both derived nodes are painted once here, so a line that already has a service keeps its
      // unit and its 6 kg notice through a structural rebuild.
      refreshUnit();
      refreshCliff();

      return h(
        "div",
        { class: "card", dataLine: String(index) },
        h(
          "div",
          { class: "spread" },
          h("p", { class: "eyebrow" }, `Dòng ${index + 1}`),
          lines.length > 1
            ? h(
                "button",
                {
                  type: "button",
                  dataVariant: "quiet",
                  onClick: () => {
                    lines.splice(index, 1);
                    onStructuralChange();
                  },
                },
                "Xoá dòng",
              )
            : null,
        ),
        h(
          "div",
          { class: "stack stack--tight" },
          labelled({ id: `${prefix}-code`, label: "Dịch vụ", control: serviceSelect }),
          // Directly under the field it describes: the unit is a consequence of the service, and
          // reading it two fields away is reading it as a separate decision.
          unitEcho,
          labelled({
            id: `${prefix}-qty`,
            label: "Khối lượng / số lượng",
            hint:
              "Gõ 5,5 hay 5.5 đều được; máy chủ nhận 5.5. Màn hình này không làm tròn và không " +
              "thêm bớt chữ số nào.",
            control: quantityInput,
          }),
          quantityNote,
          labelled({
            id: `${prefix}-basis`,
            label: "Cơ sở khối lượng",
            // The domain downgrades to an estimate only for CUSTOMER_ESTIMATE
            // (`quotes.py:494`); APPROVED_MANUAL yields APPROVED_EXACT exactly as
            // STAFF_MEASUREMENT does. Saying only a weighed quantity can reach a final price sent
            // staff back to the scales for a figure the shop had already approved by hand.
            hint:
              "“Khách tự ước” không ra được giá cuối. Khối lượng nhân viên đã cân, và khối lượng " +
              "nhập tay đã được duyệt, đều ra giá cuối.",
            control: basisSelect,
          }),
        ),
        cliffHost,
      );
    }),
  );
}

/**
 * Render a successful revision.
 *
 * @param {any} result
 * @returns {HTMLElement}
 */
/**
 * The step after "Khách đã chốt giá": carry this quote into the order form.
 *
 * The counter journey used to stop dead here. Intake to an accepted quote is seven clicks and
 * about thirteen seconds — genuinely fast — and then `Tạo đơn` on the order board asked the
 * operator to hand-assemble four values: the contact id, the quote id, the revision number and a
 * 78-character `JCS-SHA256-V1:` seal. Three of those could be copied from this card. The fourth,
 * the contact id, was rendered shortened with no copy control anywhere in the console, so for any
 * intake older than the one on screen the form could not be completed at all — while its own hint
 * said "chép từ màn hình Tiếp nhận", an instruction nobody could follow.
 *
 * So the hand-off moves to where the data already is. This is the same pattern the intake picker
 * one screen earlier already uses ("Dùng yêu cầu này"): the operator picks a row, the console
 * carries the identifiers, and nothing opaque is typed. The manual form on the order board stays
 * exactly as it was, for the case where a quote was accepted in an earlier session.
 *
 * `contactId` is null when the quote was reached by typing a quote id rather than by picking an
 * intake — there is then no bound request on screen to take it from, and the link says so instead
 * of carrying three of four values and letting the order form fail on the fourth.
 *
 * @param {any} result an accepted `QuoteRevisionResponse`
 * @param {string|null} contactId
 * @returns {HTMLElement}
 */
function createOrderHandoff(result, contactId) {
  if (!contactId) {
    return h(
      "p",
      { class: "hint" },
      "Để tạo đơn ngay từ đây, hãy chọn lượt tiếp nhận của khách ở trên trước — bảng vận hành " +
        "cần mã khách, mà bản báo giá không mang theo.",
    );
  }
  return h(
    "div",
    { class: "form__actions" },
    h(
      "a",
      {
        class: "button",
        dataVariant: "primary",
        href:
          `#/orders?quote=${encodeURIComponent(result.quote_id)}` +
          `&revision=${encodeURIComponent(String(result.revision))}` +
          `&hash=${encodeURIComponent(result.snapshot_hash)}` +
          `&contact=${encodeURIComponent(contactId)}`,
      },
      "Tạo đơn từ báo giá này",
    ),
  );
}

/**
 * "Khách đã chốt giá" — the attestation control. `DEC-021`, resolved by the owner 2026-08-25.
 *
 * Shown only on a revision that can still be accepted. An already-accepted revision shows what was
 * recorded instead of a button, because the record is what the owner asked to be able to review and
 * because offering the action twice invites a second press the server would only refuse.
 *
 * The button sends the revision and its digest back. The operator is confirming a specific price on
 * their screen, and the server refuses if either has moved — a quote reprised while the customer was
 * deciding must be read to them again, not silently accepted.
 *
 * `actions` are the two ways out of a refusal the screen can offer: go back to the form to price
 * the bag again, and re-read the recorded quotes. Both are navigation; neither writes anything.
 *
 * @param {any} result
 * @param {string} store
 * @param {((accepted: any) => Promise<void>)|null} onAccepted
 * @param {{allowed: boolean, reason: string}} writeVerdict
 * @param {string|null} contactId
 * @param {{onReprice?: () => void, onReload?: () => void}} [actions]
 * @returns {HTMLElement}
 */
function acceptControl(result, store, onAccepted, writeVerdict, contactId, actions = {}) {
  // `status`, not `finality`, since RANGE-PRICE-001. This read `finality === "APPROVED_EXACT"`,
  // which was an exact test for "the customer has agreed" only while the composer could produce
  // nothing but `ESTIMATE` before acceptance. `apply_range_prices` now produces
  // `APPROVED_EXACT`/`APPROVED` for a band an owner closed -- a revision with a real single price
  // that *nobody has agreed to yet*. Under the old condition that revision rendered "Đã chốt" and
  // a "Tạo đơn" link, so the counter would have created an order against a price the customer had
  // never heard, and the order route would have refused it for having no acceptance attestation.
  // `ACCEPTED_FINAL` is the status `accept_quote_revision` writes and the one `OrderRepository`
  // requires, so it is the fact this branch always meant.
  if (result.status === "ACCEPTED_FINAL") {
    return h(
      "div",
      { class: "stack stack--tight" },
      h(
        "p",
        { class: "hint" },
        "Đã chốt. Bản này không sửa được nữa, và người chốt đã được ghi lại.",
      ),
      createOrderHandoff(result, contactId),
    );
  }
  // A band is not one price, so there is nothing for a customer to agree to yet. The server
  // refuses acceptance on a `RANGE` revision with `RANGE_PRICE_REQUIRES_HUMAN`; offering the
  // button would be offering a press that can only be refused.
  if (result.finality === "RANGE") {
    return h(
      "div",
      { class: "notice", dataState: "warn" },
      h("p", { class: "notice__title" }, "Bản này là một khoảng giá, chưa phải một số"),
      h(
        "p",
        null,
        "Đọc khoảng giá cho khách được, nhưng chưa chốt được: chưa có con số nào để khách đồng ý. " +
          "Chốt một giá trong khoảng ở ô bên dưới, chủ tiệm duyệt, rồi mới bấm “Khách đã chốt giá”.",
      ),
    );
  }
  // The key survives a failure on purpose. A press that timed out may have committed; pressing
  // again with the *same* key makes the server hand back the recorded answer instead of recording
  // a second one (`staff-quote-accept` is an idempotent command). It is replaced only on success.
  const accepting = new Submission(`quote-accept-${result.quote_id}-${result.revision}`);
  const host = resultLine();
  // Where a failure is explained. A sibling of the button, never its parent: this function used to
  // `render(host.parentElement)` on every press, which emptied the container holding the hint, the
  // button and the status line -- so the button vanished on tap, and a refusal (a 409
  // QUOTE_EXPIRED, say) was drawn into a node no longer on the page. The counter saw nothing at all.
  const failureHost = h("div");
  const blockedNote = h("p", { class: "hint" });
  const button = h(
    "button",
    {
      type: "button",
      class: "button",
      // Without this the attestation stayed lit while every other write on the screen greyed out
      // and explained itself -- so an operator reading the offline banner would see one live button
      // and press it, and the price the customer agreed would be lost with the request.
      dataRequiresNetwork: "true",
      onClick: async () => {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        setResult(host, "warn", "Đang ghi lời xác nhận…");
        render(failureHost);
        render(blockedNote);
        try {
          const accepted = await request(
            // One template literal on purpose: `test_every_path_the_console_calls_is_a_route`
            // normalises `${...}` to `{}` and reads the string it finds, so splitting the path
            // across a concatenation hides half the route from the check.
            `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(result.quote_id)}/acceptance`,
            {
              method: "POST",
              body: {
                expected_current_revision: result.revision,
                expected_snapshot_hash: result.snapshot_hash,
              },
              idempotencyKey: accepting.key(),
            },
          );
          accepting.reset();
          button.removeAttribute("aria-busy");
          setResult(host, "ok", `Đã chốt. Bản sửa đổi ${accepted.revision} là giá cuối.`);
          if (onAccepted) await onAccepted(accepted);
        } catch (error) {
          button.removeAttribute("aria-busy");
          const failure = acceptFailure(/** @type {any} */ (error));
          // A press that cannot succeed a second time leaves the button on screen, disabled, with
          // the reason beside it -- a denied control is shown with its reason, never removed.
          // Anything else re-arms it: the operator decides whether to press again.
          button.disabled = failure.final;
          render(blockedNote, failure.final ? failure.blocked : null);
          setResult(host, null, null);
          render(
            failureHost,
            errorNotice(error, {
              title: failure.title,
              actions: [
                failure.reprice && actions.onReprice
                  ? h(
                      "button",
                      { type: "button", dataVariant: "primary", onClick: actions.onReprice },
                      "Tính giá lại",
                    )
                  : null,
                actions.onReload
                  ? h(
                      "button",
                      { type: "button", dataVariant: "quiet", onClick: actions.onReload },
                      icon("refresh"),
                      "Tải lại danh sách báo giá",
                    )
                  : null,
              ].filter(Boolean),
            }),
          );
          revealError(failureHost);
        }
      },
    },
    "Khách đã chốt giá",
  );
  return h(
    "div",
    { class: "stack stack--tight" },
    h("p", { class: "hint" }, "Bấm khi khách đã nghe giá và đồng ý. Tên bạn sẽ được ghi lại."),
    gated(button, writeVerdict),
    blockedNote,
    host,
    failureHost,
  );
}

/**
 * What to tell the counter when "Khách đã chốt giá" fails, and whether pressing it again can help.
 *
 * Every failure gets a sentence and a way forward; none is left as a bare status. Three shapes:
 *
 *   - **The price itself is no longer agreeable** -- expired, or refused for a missing fact. The
 *     way forward is pricing again, so the button is disabled with the reason and "Tính giá lại"
 *     takes the operator to the form.
 *   - **The quote moved underneath the screen** -- a newer revision, already accepted, stale. The
 *     way forward is re-reading; the button stays live because the server, not this screen,
 *     decides whether the next press is refused.
 *   - **The answer was lost** -- timeout, network, a 5xx. The attestation may have landed. Pressing
 *     again is safe *here*, and only here, because the control still holds the same idempotency
 *     key: a replay returns the recorded acceptance instead of recording a second one.
 *
 * @param {any} error
 * @returns {{title: string, final: boolean, reprice: boolean, blocked: string}}
 */
function acceptFailure(error) {
  const detail = String(error?.detail || "");
  // The server marks an expired price with a prefix, so this does not match on prose (`FR-QTE-010`).
  if (detail.startsWith("QUOTE_EXPIRED")) {
    return {
      title:
        "Báo giá này đã quá hạn (mỗi báo giá có giá trị một ngày), nên không chốt được nữa. " +
        "Không có gì được ghi. Cân lại, bấm “Tính giá” để ra giá hôm nay, rồi đọc lại cho khách.",
      final: true,
      reprice: true,
      blocked: "Không bấm được: báo giá đã quá hạn. Tính giá lại trước.",
    };
  }
  if (error?.kind === "REQUIRE_HUMAN") {
    return {
      title:
        "Chưa chốt được. Máy chủ nêu lý do bên dưới — thường là khối lượng mới là khách ước " +
        "lượng, cần cân lại rồi tính giá lại. Không có gì được ghi.",
      final: true,
      reprice: true,
      blocked: "Không bấm được: cần tính giá lại trước khi khách chốt.",
    };
  }
  const unknown =
    error?.kind === "TIMEOUT" ||
    error?.kind === "NETWORK" ||
    error?.kind === "FAULT" ||
    error?.kind === "UNAVAILABLE";
  if (unknown) {
    return {
      title:
        "Chưa biết lời xác nhận đã được ghi hay chưa. Tải lại danh sách báo giá: nếu có bản " +
        "“Đã chốt” mới thì xong, đừng bấm nữa. Nếu chưa có, bấm lại “Khách đã chốt giá” — lần " +
        "bấm lại dùng cùng mã thao tác, nên máy chủ không ghi hai lần.",
      final: false,
      reprice: false,
      blocked: "",
    };
  }
  if (error?.kind === "OFFLINE") {
    return {
      title: `${error.message} Có mạng lại thì bấm lại “Khách đã chốt giá”.`,
      final: false,
      reprice: false,
      blocked: "",
    };
  }
  // A 409 of any other shape: already accepted, a newer revision, stale. `classify` has already
  // given it a Vietnamese title from the server's own words, and the raw text is collapsed below.
  return {
    title: `Chưa chốt được: ${error?.message || "máy chủ từ chối."} Không có gì được ghi.`,
    final: false,
    reprice: false,
    blocked: "",
  };
}

function revisionResult(result, store, onAccepted, writeVerdict, contactId = null, actions = {}) {
  return h(
    "div",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h("h3", null, `Bản sửa đổi ${result.revision}`),
      h("div", { class: "row" }, priceStateBadge(result.finality)),
    ),
    acceptControl(result, store, onAccepted, writeVerdict, contactId, actions),
    result.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
            "bản ghi mới nào được tạo.",
        )
      : null,
    warningBadges([...(result.reason_codes || []), ...(result.required_approvals || [])]),
    facts([
      // The order form needs this verbatim, exactly as it needs the seal below, and until
      // 2026-09-09 only the seal could be copied. The id was shortened for reading with no way to
      // get the full string out of the screen -- a `title` is a hover, and the counter is a tablet.
      // So the one hand-off the order form requires was half-supported: staff could carry the
      // seal and not the id, which is the same as not being able to create the order at all.
      [
        "Mã báo giá",
        copyable({ value: String(result.quote_id), display: shortId(result.quote_id) }),
        { mono: true },
      ],
      ["Trạng thái", enumVi(result.status)],
      ["Phiên bản dòng", `v${result.row_version}`],
      [
        "Tổng hiển thị cho khách",
        amount({
          min: result.display_total_min_vnd,
          max: result.display_total_max_vnd,
          finality: result.finality,
          unknownLabel: "chưa có — cần quãng đường đã đo hoặc phí giao khách đã đồng ý",
        }),
        { span: true },
      ],
      // Both of these rows are the domain's range *maximum* carried under a scalar name
      // (`operations.py` fills them from `net_service_subtotal_max_vnd` and
      // `list_service_subtotal_max_vnd`). On an exact revision the two ends coincide and the
      // number is the number. On a band they do not, and printing one would put the top of the
      // interval on screen as though it were the price -- the exact failure this module's header
      // has warned about since it was written. So on a band the value is withheld and the row
      // says why; the band itself is on the display-total row above, where min and max are a
      // genuine pair.
      [
        "Tiền dịch vụ (chưa phải số khách trả)",
        result.finality === "RANGE"
          ? h("span", { class: "row" }, UNKNOWN, priceStateBadge(result.finality))
          : amount({
              min: result.net_service_subtotal_vnd,
              max: result.net_service_subtotal_vnd,
              finality: result.finality,
            }),
        { span: true },
      ],
      [
        "Giá niêm yết trước giảm",
        result.finality === "RANGE"
          ? h("span", { class: "row" }, UNKNOWN, priceStateBadge(result.finality))
          : amount({
              min: result.list_service_subtotal_vnd,
              max: result.list_service_subtotal_vnd,
              finality: result.finality,
            }),
        { span: true },
      ],
      result.finality === "RANGE"
        ? [
            "Vì sao hai dòng trên là “—”",
            "Máy chủ gửi hai số này dưới một cái tên số đơn, nhưng ruột của chúng là đầu trên của " +
              "khoảng giá. Với một bản khoảng giá, in đầu trên ra sẽ đọc như giá đã chốt. Khoảng " +
              "đầy đủ nằm ở dòng “Tổng hiển thị cho khách”.",
            { span: true },
          ]
        : null,
      ["Chương trình khuyến mãi", promotionStatement(result.promotion), { span: true }],
      [
        "Mã băm ảnh chụp",
        // The order form needs this hash verbatim; the row shortens it for reading and the
        // copy button carries the full string.
        result.snapshot_hash
          ? copyable({
              value: String(result.snapshot_hash),
              display: shortHash(result.snapshot_hash),
            })
          : shortHash(result.snapshot_hash),
        { mono: true, span: true },
      ],
    ]),
    reasonCodeList(result.reason_codes || []),
    result.required_approvals?.length
      ? h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Cần duyệt trước khi trình khách"),
          h("ul", null, result.required_approvals.map((code) => h("li", { class: "mono" }, code))),
        )
      : null,
  );
}

/**
 * What the shop's promotion programme did to this revision, and when that programme runs.
 *
 * `PROMO-WIRING-001` made a 0 ₫ discount mean something specific — the programme ended on a date,
 * or it never covered this service — and then left the date on the wire with nothing drawing it.
 * A zero with no stated reason is the same failure as rendering a null total as `0`, so the
 * interval is rendered beside the figure rather than left in the response.
 *
 * The end bound is **exclusive**, which is the one thing that must not be smoothed over: the server
 * sends the first instant the programme no longer covers. It is rendered as that instant with
 * "đến trước" in front of it rather than turned into "the last day" by subtracting from it here.
 * Deriving a different date in the console would be this screen inventing a fact, and the
 * difference between the two spellings is exactly one day at the boundary.
 *
 * `null` means no programme was evaluated against the revision at all — nothing published, or a
 * band nobody has closed — which is `—` and not `0 ₫`, with the reason codes below saying which of
 * the two it is.
 *
 * @param {any} promotion a `QuotePromotionResponse`, or null/undefined
 * @returns {HTMLElement}
 */
function promotionStatement(promotion) {
  if (!promotion) {
    return h(
      "span",
      { class: "stack stack--tight" },
      h("span", null, UNKNOWN),
      h(
        "span",
        { class: "hint" },
        "Không có chương trình nào được xét trên bản báo giá này. Mã lý do bên dưới nói rõ là " +
          "chưa công bố chương trình nào, hay dòng khoảng giá chưa được chốt.",
      ),
    );
  }
  return h(
    "span",
    { class: "stack stack--tight" },
    h(
      "span",
      { class: "row" },
      h("span", { class: "mono" }, String(promotion.policy_code)),
      h("span", null, `giảm ${money(promotion.discount_amount_vnd)}`),
    ),
    // The two bounds, printed verbatim from the response and in the counter's own timezone. They
    // are a value and not a sentence, which is why they are `mono` and not `hint`: the sentences
    // below are the claims, and this is the datum they are about.
    h(
      "span",
      { class: "mono" },
      `${dateTime(promotion.interval_start_at)} → trước ${dateTime(
        promotion.interval_end_at_exclusive,
      )}`,
    ),
    promotion.inside_interval === false
      ? h(
          "span",
          { class: "hint" },
          "Thời điểm tính giá của bản báo giá này nằm ngoài khoảng trên, nên mức giảm là 0 ₫. " +
            "Đây là một con số không, có lý do đi kèm — không phải một ô bỏ trống.",
        )
      : null,
    h(
      "span",
      { class: "hint" },
      "Mốc sau là mốc kết thúc không bao gồm: chương trình chạy đến ngay trước nó, và thời điểm " +
        "đó đã ở ngoài chương trình. Cả hai mốc lấy nguyên từ máy chủ; màn hình này không tự trừ " +
        "ra một ngày nào khác.",
    ),
  );
}

/**
 * The published name of a service, from the catalog the picker already loaded.
 *
 * Falls back to the code rather than to a friendly guess: a line whose code is not in the
 * published catalog is a pricebook that moved under a stored revision, and it must look
 * unfamiliar rather than be labelled plausibly.
 *
 * @param {CatalogService[]|null} catalog
 * @param {string} code
 * @returns {string}
 */
function serviceName(catalog, code) {
  const found = catalog?.find((item) => item.code === code);
  return found ? found.display_name : String(code);
}

/**
 * One revision's lines, read back from the server.
 *
 * This is what an approver looks at before deciding a `SET_RANGE_PRICE` envelope, and what the
 * staff member closing a band checks their own amounts against. Every number on it came off the
 * immutable snapshot; nothing is combined and nothing is totalled here.
 *
 * @param {any} detail a `QuoteRevisionDetailResponse`
 * @param {CatalogService[]|null} catalog
 * @returns {HTMLElement}
 */
function revisionLines(detail, catalog) {
  return h(
    "div",
    { class: "stack stack--tight" },
    // Defensive `|| []` for the same reason every read on this screen has one: the console has no
    // generated client and no type checker between it and the API, so a response missing a field
    // must render an empty list rather than throw and leave a blank panel with no explanation.
    (detail.lines || []).map((line) =>
      h(
        "div",
        { class: "spread" },
        h(
          "span",
          null,
          h("strong", null, serviceName(catalog, line.service_code)),
          h("span", { class: "hint mono" }, ` ${line.service_code}`),
          h("span", { class: "hint" }, ` · ${quantityText(line.quantity)} ${enumVi(line.unit)}`),
        ),
        line.price_kind === "RANGE"
          ? amount({
              min: line.band_minimum_vnd,
              max: line.band_maximum_vnd,
              finality: "RANGE",
              unknownLabel: "chưa có khoảng giá",
            })
          : amount({
              min: line.net_amount_vnd,
              max: line.net_amount_vnd,
              finality: detail.finality,
              unknownLabel: "chưa có giá",
            }),
      ),
    ),
  );
}

/**
 * Closing a published band: the counter's three steps, on one card.
 *
 * The shape of this control is dictated by the server and is not a UX preference. Closing a band
 * is *two* commands with a second person in between — propose, approve, apply — because the
 * envelope an owner signs binds a digest of the amounts rather than storing them
 * (`approvals.py` calls comparing an unstored rendering "theatre"). Two consequences fall out of
 * that and both are stated on screen rather than discovered:
 *
 *   1. **The amounts live in this screen between step two and step three.** Nothing on the server
 *      holds them. Navigating away loses them, and the operator has to propose again — which is
 *      cheap, because a proposal writes no price.
 *   2. **The envelope dies in ten minutes** and expiry is checked again at application, so an
 *      approval that arrives late produces a refusal rather than a price. The countdown is
 *      therefore a control, not decoration.
 *
 * The bound comes from the server, per line, off the revision the customer was read — never from
 * the live pricebook and never from anything this screen assembled. A pricebook republished while
 * the customer was deciding cannot move the interval they were told.
 *
 * @param {object} spec
 * @param {any} spec.result the stored `RANGE` revision this closes
 * @param {string} spec.store
 * @param {CatalogService[]|null} spec.catalog
 * @param {{allowed: boolean, reason: string}} spec.writeVerdict
 * @param {(closed: any) => Promise<void>|void} spec.onClosed given the new exact revision
 * @returns {HTMLElement}
 */
function bandCloser(spec) {
  const root = h("div", { class: "stack" }, skeleton(2));

  /**
   * @type {{lines: any[], typed: Record<string, string>, approval: any|null, detail: any|null}}
   */
  const state = { lines: [], typed: {}, approval: null, detail: null };

  const status = resultLine();
  const errorHost = h("div");
  const clockHost = h("span", { class: "row" });

  // One key per intent, minted on first use and cleared on an edit or a commit -- the rule
  // `core/api.js` states and the reason it states it. Minting a key per press instead would make
  // a retry after a timeout a *second* command: the server hashes the body alongside the key, so
  // the same amounts under a new key raise a second envelope rather than replaying the first, and
  // the counter would be looking at two approvals for one garment.
  const proposing = new Submission(`range-price-propose-${spec.result.quote_id}`);
  const applying = new Submission(`range-price-apply-${spec.result.quote_id}`);

  // One interval for this card, stopped the moment the card leaves the document. Without the
  // connectivity check a closed quote would leave a timer ticking against a detached tree for as
  // long as the console stayed open, which on a counter tablet is all day.
  const timer = setInterval(() => {
    if (!root.isConnected) {
      clearInterval(timer);
      return;
    }
    if (state.approval) render(clockHost, expiryBadge(state.approval.expires_at));
  }, TICK_MS);

  /** The body of the request both commands take. Identical by construction, which is the point. */
  const body = () => ({
    expected_current_revision: spec.result.revision,
    expected_snapshot_hash: spec.result.snapshot_hash,
    choices: state.lines.map((line) => ({
      service_code: line.service_code,
      amount_vnd: bandVerdict(
        state.typed[line.service_code],
        line.band_minimum_vnd,
        line.band_maximum_vnd,
      ).amount,
    })),
  });

  async function load() {
    render(root, skeleton(2));
    try {
      const detail = await request(
        `/internal/v1/stores/${encodeURIComponent(spec.store)}/quotes/${encodeURIComponent(spec.result.quote_id)}?revision=${encodeURIComponent(String(spec.result.revision))}`,
      );
      state.detail = detail;
      state.lines = (detail.lines || []).filter((line) => line.price_kind === "RANGE");
      draw();
    } catch (error) {
      render(
        root,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chưa đọc được các dòng của bản báo giá này"),
          h(
            "p",
            null,
            "Không có các dòng thì không biết khoảng giá của từng món, và bảng vận hành không tự " +
              "đoán khoảng. Thử tải lại; nếu vẫn vậy thì báo kỹ thuật kèm mã theo dõi bên dưới.",
          ),
        ),
        errorNotice(error, { onRetry: () => void load() }),
        h(
          "div",
          { class: "form__actions" },
          h(
            "button",
            { type: "button", dataVariant: "quiet", onClick: () => void load() },
            icon("refresh"),
            "Tải lại các dòng",
          ),
        ),
      );
    }
  }

  /** Redraw the whole card. Called on a step change, never on a keystroke. */
  function draw() {
    if (!state.lines.length) {
      render(
        root,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Bản này không có dòng nào theo khoảng giá"),
          h(
            "p",
            null,
            "Máy chủ báo đây là bản khoảng giá nhưng không dòng nào mang khoảng. Đừng gửi số nào; " +
              "tải lại báo giá và báo kỹ thuật.",
          ),
        ),
      );
      return;
    }
    render(root, state.approval ? awaitingApproval() : chooseAmounts());
  }

  /** Step one: one amount per banded line, checked while it is typed. */
  function chooseAmounts() {
    const fields = state.lines.map((line, index) =>
      bandInput({
        id: `quote-band-${index}`,
        label: serviceName(spec.catalog, line.service_code),
        hint:
          `${quantityText(line.quantity)} ${enumVi(line.unit)} · ` +
          "giá cho cả dòng, không phải đơn giá.",
        minimum: line.band_minimum_vnd,
        maximum: line.band_maximum_vnd,
        value: state.typed[line.service_code] || "",
        onInput: (value) => {
          state.typed[line.service_code] = value;
          // An edited amount is different content, so the proposal that would carry it is a
          // different intent and may not reuse the key of the one before it.
          proposing.reset();
          refreshReadiness();
        },
      }),
    );

    const send = h(
      "button",
      { type: "button", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Gửi giá cho chủ duyệt",
    );
    send.addEventListener("click", () => void propose(send));

    const readinessHost = h("div");
    const refreshReadiness = () => render(readinessHost, readinessNotice());

    refreshReadiness();

    return h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "notice", dataState: "info" },
        h("p", { class: "notice__title" }, "Chốt một giá trong khoảng đã niêm yết"),
        h(
          "p",
          null,
          "Khoảng giá là chủ tiệm đã cho phép trước: mọi số trong khoảng đều là giá hợp lệ, và " +
            "bảng vận hành không tự chọn giúp một số nào. Bạn chọn, chủ tiệm duyệt, rồi máy chủ " +
            "mới ghi thành giá.",
        ),
        h(
          "p",
          { class: "hint" },
          `Phiếu duyệt chỉ sống ${OWNER_FINANCIAL_TTL_VI}, và máy chủ kiểm lại hạn cả lúc duyệt ` +
            "lẫn lúc áp dụng — nên gọi chủ tiệm trước khi gửi, đừng gửi rồi mới đi tìm.",
        ),
      ),
      h("div", { class: "stack stack--tight" }, fields),
      readinessHost,
      h("div", { class: "action-bar" }, gated(send, spec.writeVerdict)),
      status,
      errorHost,
    );
  }

  /**
   * What stands between the last keystroke and the button.
   *
   * The server refuses a half-closed revision outright — every band must be closed in one
   * attestation — so the console names the line that is still open instead of letting the operator
   * press and read `RANGE_PRICE_REQUIRES_HUMAN` back.
   *
   * @returns {HTMLElement|null}
   */
  function readinessNotice() {
    const readiness = bandReadiness(state.lines, state.typed);
    if (readiness.ready) return null;
    const open = readiness.blocked.filter((item) => item.state === BAND.EMPTY);
    return h(
      "div",
      { class: "notice", dataState: "warn" },
      h("p", { class: "notice__title" }, "Chưa gửi được: còn dòng chưa có giá hợp lệ"),
      h(
        "p",
        null,
        "Máy chủ chốt cả bản một lần, không chốt từng dòng. Một bản nửa quyết nửa đoán còn tệ hơn " +
          "là chưa có tổng, nên phải điền đủ mọi dòng trước khi gửi.",
      ),
      h(
        "ul",
        null,
        readiness.blocked.map((item) =>
          h(
            "li",
            null,
            h("span", null, serviceName(spec.catalog, item.serviceCode)),
            open.includes(item) ? " — chưa nhập giá" : " — số đang nhập chưa dùng được",
          ),
        ),
      ),
    );
  }

  /** Step two: the envelope is raised and a second person has to decide it. */
  function awaitingApproval() {
    const apply = h(
      "button",
      { type: "button", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Áp dụng giá đã duyệt",
    );
    apply.addEventListener("click", () => void applyPrices(apply));

    render(clockHost, expiryBadge(state.approval.expires_at));

    return h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "spread" },
        h("p", { class: "eyebrow" }, "Đang chờ chủ tiệm duyệt"),
        clockHost,
      ),
      facts([
        [
          "Mã phiếu duyệt",
          copyable({
            value: String(state.approval.approval_request_id),
            display: shortId(state.approval.approval_request_id),
          }),
          { mono: true, span: true },
        ],
        ["Ai được quyết", enumVi(state.approval.required_role)],
        ["Hết hạn lúc", dateTime(state.approval.expires_at)],
      ]),
      h(
        "div",
        { class: "stack stack--tight" },
        state.lines.map((line) =>
          h(
            "div",
            { class: "spread" },
            h("span", null, serviceName(spec.catalog, line.service_code)),
            h(
              "span",
              { class: "money" },
              money(
                bandVerdict(
                  state.typed[line.service_code],
                  line.band_minimum_vnd,
                  line.band_maximum_vnd,
                ).amount,
              ),
            ),
          ),
        ),
      ),
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Đừng rời màn hình này"),
        h(
          "p",
          null,
          "Phiếu duyệt niêm phong một mã băm của đúng những con số này, chứ máy chủ không lưu " +
            "chính những con số ấy — nên chúng chỉ còn ở màn hình này. Rời đi là phải gửi lại từ " +
            "đầu. Gửi lại không mất gì: lúc đề nghị chưa có giá nào được ghi.",
        ),
        h(
          "p",
          null,
          "Máy chủ cũng từ chối để người đề nghị tự duyệt. Người bấm “Duyệt” ở màn hình ",
          h("a", { href: "#/approvals" }, "Duyệt"),
          " phải là người khác.",
        ),
      ),
      h(
        "div",
        { class: "action-bar" },
        gated(apply, spec.writeVerdict),
        h(
          "button",
          {
            type: "button",
            dataVariant: "quiet",
            onClick: () => {
              // Deliberately not a "cancel": nothing on the server is undone. The envelope stays
              // raised and simply stops matching, because a different amount renders a different
              // digest -- which is invariant 8 doing its job, not a loose end.
              state.approval = null;
              setResult(
                status,
                "warn",
                "Đã quay lại bước chọn giá. Phiếu duyệt cũ không dùng được cho số mới — đổi số " +
                  "là đổi nội dung được niêm phong, nên phải gửi lại.",
              );
              draw();
            },
          },
          "Sửa lại số",
        ),
      ),
      status,
      errorHost,
    );
  }

  /** @param {HTMLButtonElement} button */
  async function propose(button) {
    const readiness = bandReadiness(state.lines, state.typed);
    if (!readiness.ready) {
      setResult(status, "danger", "Còn dòng chưa có giá hợp lệ; chưa gửi đi.");
      return;
    }
    button.disabled = true;
    setResult(status, "warn", "Đang gửi cho chủ tiệm duyệt…");
    render(errorHost);
    try {
      const approval = await request(
        `/internal/v1/stores/${encodeURIComponent(spec.store)}/quotes/${encodeURIComponent(spec.result.quote_id)}/range-prices`,
        { method: "POST", body: body(), idempotencyKey: proposing.key() },
      );
      proposing.reset();
      state.approval = approval;
      setResult(
        status,
        "ok",
        `Đã gửi. Chủ tiệm mở màn hình Duyệt và bấm Duyệt cho phiếu ${shortId(approval.approval_request_id)}.`,
      );
      draw();
    } catch (error) {
      button.disabled = false;
      setResult(status, error.kind === "REQUIRE_HUMAN" ? "warn" : "danger", proposeFailure(error));
      render(errorHost, errorNotice(error));
      revealError(errorHost);
    }
  }

  /** @param {HTMLButtonElement} button */
  async function applyPrices(button) {
    // Checked again, although nothing can have edited an amount while step two is on screen:
    // "Sửa lại số" is the only way back to the boxes and it drops the approval on the way. A
    // command that writes a price is the wrong place to rely on that being true.
    if (!bandReadiness(state.lines, state.typed).ready) {
      setResult(status, "danger", "Số tiền đang giữ không hợp lệ; chưa gửi đi.");
      return;
    }
    button.disabled = true;
    setResult(status, "warn", "Đang ghi giá đã duyệt…");
    render(errorHost);
    try {
      const closed = await request(
        `/internal/v1/stores/${encodeURIComponent(spec.store)}/quotes/${encodeURIComponent(spec.result.quote_id)}/range-prices/${encodeURIComponent(String(state.approval.approval_request_id))}`,
        { method: "POST", body: body(), idempotencyKey: applying.key() },
      );
      applying.reset();
      clearInterval(timer);
      setResult(status, "ok", `Đã ghi giá vào bản sửa đổi ${closed.revision}.`);
      if (spec.onClosed) await spec.onClosed(closed);
    } catch (error) {
      button.disabled = false;
      setResult(status, error.kind === "REQUIRE_HUMAN" ? "warn" : "danger", applyFailure(error));
      render(errorHost, errorNotice(error));
      revealError(errorHost);
    }
  }

  void load();
  return root;
}

/**
 * The sentence above a failed proposal.
 *
 * Every branch says the same load-bearing thing in different words — nothing was written — because
 * the one mistake this step invites is pressing again in case the first press "half worked". A
 * proposal writes no price at all, and an out-of-band amount is refused before any approval row
 * exists, so there is never anything to clean up.
 *
 * @param {any} error
 * @returns {string}
 */
function proposeFailure(error) {
  if (error.kind === "TIMEOUT" || error.kind === "NETWORK") {
    return (
      "Chưa biết lệnh có tới máy chủ hay không. Phiếu duyệt có thể đã được tạo — mở màn hình " +
      "Duyệt xem có phiếu nào của bản báo giá này trước khi gửi lại. Chưa có giá nào được ghi."
    );
  }
  if (error.kind === "REQUIRE_HUMAN") {
    return "Máy chủ từ chối số này. Không có phiếu duyệt nào được tạo và không có giá nào được ghi.";
  }
  if (error.kind === "STALE" || error.kind === "CONFLICT") {
    return (
      "Bản báo giá đã đổi từ lúc bạn mở màn hình, nên đề nghị này không còn gắn đúng bản nữa. " +
      "Tải lại danh sách báo giá và làm lại trên bản mới. Chưa có gì được ghi."
    );
  }
  return "Không gửi được đề nghị. Chưa có giá nào được ghi.";
}

/**
 * The sentence above a failed application.
 *
 * The common failure here is not a bad number: it is an envelope that is not approved yet, was
 * approved for different amounts, or has run out its ten minutes. The server answers all three
 * with one deliberately opaque message — telling a caller *which* would tell a member of any store
 * whether a given UUID is a real approval somewhere else — so the console lists the three
 * possibilities rather than guessing between them.
 *
 * @param {any} error
 * @returns {string}
 */
function applyFailure(error) {
  if (error.kind === "TIMEOUT" || error.kind === "NETWORK") {
    return (
      "Chưa biết lệnh có tới máy chủ hay không, nên chưa biết giá đã được ghi hay chưa. Tải lại " +
      "danh sách báo giá và xem bản mới nhất trước khi bấm lại."
    );
  }
  return (
    "Chưa ghi được giá. Thường là một trong ba: chủ tiệm chưa bấm Duyệt, phiếu duyệt niêm phong " +
    "những con số khác với số đang ở đây, hoặc phiếu đã quá hạn. Máy chủ trả lời chung một câu " +
    "cho cả ba. Kiểm màn hình Duyệt, và nếu quá hạn thì gửi lại từ đầu."
  );
}

/**
 * Remaining time on the raised envelope.
 *
 * Copied in spirit from the approvals queue rather than shared with it: the badge there belongs to
 * a card in a list that reloads, this one belongs to a form the operator is standing in front of.
 * Rebuilt whole on each tick because the warn → danger flip is carried by `data-state`, not by the
 * words, and a tick that patched only the text would leave an expired envelope wearing a live
 * envelope's colour.
 *
 * @param {string|null|undefined} expiresAt
 * @returns {HTMLElement}
 */
function expiryBadge(expiresAt) {
  const left = countdown(expiresAt);
  return badge({
    token: left.text,
    gloss: left.expired
      ? "đã hết hạn — gửi lại từ đầu, không xin gia hạn được"
      : "thời gian còn lại để chủ duyệt và bạn áp dụng",
    state: left.expired ? "danger" : "warn",
  });
}

/**
 * One quote on the list.
 *
 * @param {any} item
 * @param {(item: any) => void} onPickRevision
 * @returns {HTMLElement}
 */
function quoteCard(item, onPickRevision) {
  return h(
    "article",
    { class: "card" },
    h(
      "div",
      { class: "spread" },
      // Copyable for the same reason as on the result card: an operator coming back to a quote
      // taken earlier in the day needs the id itself, not a hover.
      copyable({ value: String(item.quote_id), display: shortId(item.quote_id) }),
      priceStateBadge(item.finality),
    ),
    facts([
      ["Bản", `r${item.revision} · dòng v${item.row_version}`],
      ["Trạng thái", enumVi(item.status)],
      [
        "Tổng hiển thị",
        amount({
          min: item.display_total_min_vnd,
          max: item.display_total_max_vnd,
          finality: item.finality,
          unknownLabel: "chưa có tổng",
        }),
        { span: true },
      ],
      ["Hiệu lực đến", dateTime(item.valid_until)],
      [
        "Ảnh chụp",
        item.snapshot_hash
          ? copyable({
              value: String(item.snapshot_hash),
              display: shortHash(item.snapshot_hash),
            })
          : shortHash(item.snapshot_hash),
        { mono: true, span: true },
      ],
    ]),
    h(
      "div",
      { class: "form__actions" },
      h(
        "button",
        { type: "button", onClick: () => onPickRevision(item) },
        "Thêm bản sửa đổi cho báo giá này",
      ),
    ),
  );
}

/**
 * @param {import("../core/router.js").RouteContext} [context]
 * @returns {HTMLElement}
 */
export function render_(context) {
  const store = storeId();
  // QUOTES-GATING. Until 2026-09-09 this screen consulted no capability at all, and neither did the
  // assistant. It was never exploitable: a session without QUOTES_WRITE also lacks QUOTES_READ, so
  // the catalog fetch fails and the form never renders -- the protection was real but incidental,
  // resting on two capabilities happening to hold the same roles. Making it explicit costs one
  // call and removes the silent failure mode where a read is widened and a write follows it.
  const writeVerdict = can(principal(), "QUOTES_WRITE");
  const submission = new Submission("quote-create");
  // The Tiếp nhận screen's "Báo giá ngay" lands here. The id is a claim until the server confirms
  // it — `prefill` below resolves it before the form is allowed to rely on it.
  const prefillId = String(context?.query?.get("request") || "").trim();
  // `#/quotes?quote=<id>` — the approvals queue's "Mở nội dung này trước khi quyết" link for a
  // `QUOTE_REVISION` envelope. An approver arrives here to read the lines and the bands behind a
  // digest they are about to sign, so the panel it opens is a read and carries no controls.
  const openId = String(context?.query?.get("quote") || "").trim();
  // `&revision=<n>` — which revision the approvals queue's envelope actually binds.
  //
  // `RANGE-APPROVAL-VISIBILITY-001`. Without it this panel opened whichever revision is newest,
  // and an approver who arrived from a `QUOTE_REVISION` envelope could read revision 3 while
  // signing the digest of revision 1. Optional, because a person opening a quote by hand has no
  // envelope in mind and the newest revision is the right answer for them.
  const openRevision = String(context?.query?.get("revision") || "").trim();

  /** @type {{lines: Line[], fulfillmentMode: string, verifiedDistanceM: string, manualFeeVnd: string, customerAcknowledgedFee: boolean, orderRequestId: string, quoteId: string, quoteRequestId: string, expectedRevision: string, rowVersion: string, requestSummary: any|null}} */
  const draft = {
    lines: [blankLine()],
    fulfillmentMode: "SELF_DROP_SELF_COLLECT",
    verifiedDistanceM: "",
    manualFeeVnd: "",
    customerAcknowledgedFee: false,
    orderRequestId: "",
    quoteId: "",
    // The intake the tracked quote was written for, when this screen wrote it; "" when the quote
    // was picked from the list and its intake is not known here. Lets a switch to a *different*
    // intake drop revision mode instead of sending one customer's lines as another's revision.
    quoteRequestId: "",
    expectedRevision: "0",
    rowVersion: "",
    requestSummary: null,
  };

  /**
   * Hold the revision the server just returned as the one the next "Tính giá" revises.
   *
   * Before this, the form never learned about its own writes. After the first quote it stayed in
   * create mode, so the second press omitted `quote_id` and the server answered "this order request
   * already has a quote; add a revision instead"; and after a revision it kept the *old*
   * `expected_current_revision` and `If-Match`, so the next revision was refused as stale while the
   * card above it read "Bản sửa đổi 2 / v2". Every successful write -- a price, an acceptance, a
   * closed band -- now advances the draft to exactly what the server said is current.
   *
   * @param {any} revision a `QuoteRevisionResponse`
   */
  function track(revision) {
    if (!revision?.quote_id || !Number.isInteger(revision.revision)) return;
    if (draft.quoteId !== revision.quote_id) draft.quoteRequestId = draft.orderRequestId.trim();
    draft.quoteId = String(revision.quote_id);
    draft.expectedRevision = String(revision.revision);
    draft.rowVersion = String(revision.row_version);
    invalidateKey();
    refreshRevisionBanner();
  }

  /** Leave revision mode: the next "Tính giá" opens a new quote. */
  function untrack() {
    draft.quoteId = "";
    draft.quoteRequestId = "";
    draft.expectedRevision = "0";
    draft.rowVersion = "";
    invalidateKey();
    refreshRevisionBanner();
  }

  /**
   * Binding the form to another intake while a quote is tracked for a different one ends revision
   * mode: that quote belongs to the other customer.
   *
   * @param {string} requestId
   */
  function followRequest(requestId) {
    if (draft.quoteId && draft.quoteRequestId && draft.quoteRequestId !== requestId.trim()) {
      untrack();
    }
  }

  // The four modes the server's FulfillmentMode enum accepts. Changing it invalidates the
  // idempotency key for the same reason a line edit does: the server hashes the payload with it.
  const fulfillmentSelect = enumSelect("fulfillment_mode", FULFILLMENT_MODES, draft.fulfillmentMode);
  fulfillmentSelect.addEventListener("change", (event) => {
    draft.fulfillmentMode = /** @type {HTMLSelectElement} */ (event.target).value;
    syncDistance();
    invalidateKey();
  });

  // Distance, for the three modes that have a delivery job. Without it `evaluate_delivery` returns
  // REQUIRE_HUMAN, the quote carries no total, and the quote can then never be accepted -- so
  // shipping the mode picker without this field made three of its four options dead ends. Hidden
  // for the walk-in case because there is no journey to measure.
  const distanceInput = h("input", {
    type: "number",
    min: "0",
    max: "100000",
    step: "1",
    inputmode: "numeric",
    placeholder: "4000",
    value: draft.verifiedDistanceM,
    onInput: (event) => {
      draft.verifiedDistanceM = /** @type {HTMLInputElement} */ (event.target).value;
      invalidateKey();
    },
  });
  const distanceField = labelled({
    id: "quote-distance",
    label: "Quãng đường đã đo (mét)",
    hint:
      // `delivery.py:159` is `verified_distance_m <= 2_000` -> fee 0, so exactly 2.000 m is free
      // and the paid band starts above it. "dưới 2km" put the boundary itself in the paid band.
      "Đo thật, không ước lượng. Với đơn có cả lấy và trả: từ 2.000m trở xuống miễn phí, " +
      "trên 2.000m đến 6.000m thu 10.000đ, " +
      "trên 6.000m nhân viên và khách thỏa thuận rồi nhập ở ô dưới. Bỏ trống thì báo giá không ra " +
      "tổng tiền và không chốt được. Đơn “Chỉ lấy” hoặc “Chỉ trả” không theo bảng quãng đường " +
      "này — xem ô phí bên dưới.",
    control: distanceInput,
  });

  // Over 6km the fee is negotiated (DEC-003), and the customer must have agreed to it before the
  // shop proceeds. Both facts travel together or the engine keeps refusing.
  const manualFeeInput = h("input", {
    type: "number",
    min: "0",
    step: "1",
    inputmode: "numeric",
    placeholder: "45000",
    value: draft.manualFeeVnd,
    onInput: (event) => {
      draft.manualFeeVnd = /** @type {HTMLInputElement} */ (event.target).value;
      invalidateKey();
    },
  });
  const manualAckInput = h("input", {
    type: "checkbox",
    checked: draft.customerAcknowledgedFee,
    onChange: (event) => {
      draft.customerAcknowledgedFee = /** @type {HTMLInputElement} */ (event.target).checked;
      invalidateKey();
    },
  });
  // The label used to read "chỉ khi trên 6km", which is false for half the modes this field is
  // shown for. `evaluate_delivery` branches on mode BEFORE it looks at distance
  // (packages/domain/.../delivery.py:139-146): PICKUP_ONLY and RETURN_ONLY always take
  // ONE_LEG_HUMAN_PRICE, at any distance. A one-leg pickup 500m away needs a negotiated fee just
  // as much as a six-kilometre one, and staff reading "only over 6km" would leave it blank and get
  // a quote with no total.
  const manualFeeField = labelled({
    id: "quote-manual-fee",
    label: "Phí giao đã thỏa thuận (₫)",
    hint:
      "Nhập số hai bên đã đồng ý, và tích ô bên dưới xác nhận khách đã đồng ý. Bắt buộc khi đơn " +
      "chỉ có một chiều (“Chỉ lấy” hoặc “Chỉ trả”) — ở mọi quãng đường — và khi đơn có cả hai " +
      "chiều mà xa hơn 6km.",
    control: manualFeeInput,
  });
  const manualAckField = labelled({
    id: "quote-manual-ack",
    label: "Khách đã đồng ý mức phí giao này",
    control: manualAckInput,
  });

  function syncDistance() {
    const carries = draft.fulfillmentMode !== "SELF_DROP_SELF_COLLECT";
    for (const node of [distanceField, manualFeeField, manualAckField]) node.hidden = !carries;
  }
  syncDistance();

  const builderBody = h("div");
  const resultHost = h("div", { class: "stack" });
  const result = resultLine();
  const summaryHost = h("div");
  const openHost = h("div");
  const pickerHost = h("div", null, skeleton(2));
  // Owned by the screen, not by one build of the form, so a successful write can switch the form
  // into revision mode -- and keep its revision and row version current -- without rebuilding the
  // line editor under the operator's thumb.
  const revisionBanner = h("div");

  function refreshRevisionBanner() {
    render(
      revisionBanner,
      draft.quoteId
        ? h(
            "div",
            { class: "notice", dataState: "info", id: "quote-revision-banner" },
            h("p", { class: "notice__title" }, `Thêm bản sửa đổi cho ${shortId(draft.quoteId)}`),
            h(
              "p",
              null,
              `Sẽ gửi kèm bản hiện tại r${draft.expectedRevision} và phiên bản dòng v${draft.rowVersion}. ` +
                "Nếu ai đó vừa sửa báo giá này, máy chủ sẽ từ chối và bạn tải lại.",
            ),
            h(
              "div",
              { class: "form__actions" },
              h(
                "button",
                {
                  type: "button",
                  dataVariant: "quiet",
                  onClick: () => {
                    untrack();
                    redrawBuilder();
                  },
                },
                "Bỏ, tạo báo giá mới",
              ),
            ),
          )
        : null,
    );
  }

  // Any edit invalidates the idempotency key: the server hashes the payload alongside it, so
  // replaying the old key with changed content is a 409 rather than a replay. This is the whole
  // response to a typed character — no rebuild.
  const invalidateKey = () => submission.reset();

  // Reserved for changes to the *set* of lines, or to which quote is being revised. Anything that
  // only changes a value must call `invalidateKey` instead, or the caret moves as the operator types.
  const redrawBuilder = () => {
    invalidateKey();
    // Without a catalog there is no form to redraw. Saying so matters because `pickRevision` is
    // reachable from the recorded-quotes list, which loads independently: without this branch the
    // draft would silently take on a quote id and then scroll the operator to an error notice.
    if (catalog && catalog.length) render(builderBody, buildForm());
    else render(builderBody, catalogRefusal());
  };

  // The one structural path for adding a line — the button below and Enter in a quantity field
  // both come through here.
  const addLine = () => {
    if (draft.lines.length >= MAX_LINES) return;
    draft.lines.push(blankLine());
    redrawBuilder();
  };

  /** @type {CatalogService[]|null} the picker's data, once the server has answered */
  let catalog = null;

  /**
   * What stands where the form would be when the catalog is unusable.
   *
   * `errorNotice` withholds its retry button unless the error is retryable, and the likeliest
   * failure here — a 503 `PRICEBOOK_UNAVAILABLE` from an unpublished or digest-failing pricebook —
   * is deliberately not. That rule is right for a write; here it would leave the operator with a
   * dead screen and no control anywhere on it, because the form's host has no reload of its own
   * the way the intake picker does. So the refusal carries its own "Thử tải lại" — which asks the
   * server again and changes nothing if the answer is the same, unlike retrying a write.
   *
   * @param {unknown} [error] the failure, when there was one; absent means the catalog was empty
   * @returns {HTMLElement}
   */
  function catalogRefusal(error) {
    return h(
      "div",
      { class: "stack stack--tight" },
      error
        ? errorNotice(error)
        : h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Bảng giá chưa có dịch vụ nào"),
            h(
              "p",
              null,
              "Máy chủ trả về một bảng giá rỗng, nên không có dịch vụ nào để chọn và không thể " +
                "tính giá. Đây là vấn đề của bảng giá đã công bố, không phải của thao tác này.",
            ),
          ),
      h(
        "div",
        { class: "form__actions" },
        h(
          "button",
          { type: "button", dataVariant: "quiet", onClick: () => void loadCatalog() },
          icon("refresh"),
          "Thử tải lại bảng giá",
        ),
      ),
    );
  }

  /**
   * The form cannot be built before the picker has its names, and it must not fall back to typed
   * codes when the catalog is unreadable — the same published payload prices and labels, so one
   * refusal covers both. An empty catalog counts as unreadable: a picker offering nothing but
   * "— Chọn dịch vụ —" is a form that cannot be completed, and it should say so rather than look
   * available.
   */
  async function loadCatalog() {
    render(builderBody, skeleton(2));
    try {
      const services = await request("/internal/v1/pricebook/services");
      catalog = Array.isArray(services) ? services : null;
      render(builderBody, catalog && catalog.length ? buildForm() : catalogRefusal());
    } catch (error) {
      catalog = null;
      render(builderBody, catalogRefusal(error));
    }
  }

  const pickRevision = (item) => {
    draft.quoteId = item.quote_id;
    // Picked from the list: which intake it belongs to is not in the row, so it is not guessed.
    draft.quoteRequestId = "";
    draft.expectedRevision = String(item.revision);
    draft.rowVersion = String(item.row_version);
    refreshRevisionBanner();
    redrawBuilder();
    builderBody.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  // The recorded-quotes list. The filter narrows the rows already fetched and computes nothing —
  // string matching over raw field values only (id, revision, finality); money fields are never
  // read by it. While it narrows, the status line keeps both counts so a shortened list never
  // reads as lost data.
  const list = listView({
    limit: LIST_LIMIT,
    fetch: () =>
      request(`/internal/v1/stores/${encodeURIComponent(store)}/quotes?limit=${LIST_LIMIT}`),
    renderItem: (item) => quoteCard(item, pickRevision),
    emptyText: "Chưa có báo giá nào trong cửa hàng này.",
    skeletonRows: 2,
    filterStatusHiddenWhenInactive: true,
    filter: {
      placeholder: "Lọc theo mã, bản, trạng thái…",
      noun: "báo giá",
      matches: (item, needle) =>
        matchesFilter([item.quote_id, item.revision, item.finality], needle),
    },
  });

  /**
   * The intake the form is bound to, re-rendered in place rather than through `redrawBuilder` —
   * a typed character in a line editor must never rebuild the form, and picking an intake is not
   * a structural change to the lines.
   */
  function refreshSummary() {
    const item = draft.requestSummary;
    render(
      summaryHost,
      item
        ? h(
            "div",
            { class: "notice", dataState: "info", id: "quote-request-summary" },
            h(
              "p",
              { class: "notice__title" },
              `Đang báo giá cho yêu cầu ${shortId(item.order_request_id)}`,
            ),
            h(
              "p",
              null,
              `${enumVi(item.status)} · tiếp nhận lúc ${dateTime(item.created_at)} · ` +
                `liên hệ ${shortId(item.contact_binding_id)}`,
            ),
            h(
              "div",
              { class: "form__actions" },
              h(
                "button",
                {
                  type: "button",
                  dataVariant: "quiet",
                  onClick: () => {
                    draft.orderRequestId = "";
                    draft.requestSummary = null;
                    invalidateKey();
                    refreshSummary();
                  },
                },
                "Bỏ chọn yêu cầu này",
              ),
            ),
          )
        : null,
    );
  }

  /** Bind the form to an intake the server already returned. No fetch needed — the row is real. */
  const pickRequest = (item) => {
    draft.orderRequestId = item.order_request_id;
    followRequest(item.order_request_id);
    draft.requestSummary = item;
    invalidateKey();
    refreshSummary();
    summaryHost.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  /**
   * The recent-intake picker. Its rows come from the same list endpoint the Tiếp nhận screen
   * shows, so a row here is never a guess at an identifier.
   */
  function pickerList(items) {
    if (!items.length) {
      return h(
        "div",
        { class: "notice", dataState: "info" },
        h(
          "p",
          null,
          "Chưa có lượt tiếp nhận nào trong cửa hàng này. Tạo một lượt ở màn hình ",
          h("a", { href: "#/order-requests" }, "Tiếp nhận"),
          " rồi quay lại đây.",
        ),
      );
    }
    return h(
      "div",
      { class: "stack stack--tight" },
      items.map((item) =>
        h(
          "div",
          { class: "spread" },
          h(
            "span",
            null,
            h(
              "strong",
              { class: "mono", title: item.order_request_id },
              shortId(item.order_request_id),
            ),
            ` · ${enumVi(item.status)} · ${dateTime(item.created_at)}`,
          ),
          h(
            "button",
            { type: "button", dataVariant: "quiet", onClick: () => pickRequest(item) },
            "Dùng yêu cầu này",
          ),
        ),
      ),
    );
  }

  async function loadPicker() {
    render(pickerHost, skeleton(2));
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/order-requests?limit=${PICKER_LIMIT}`,
      );
      render(
        pickerHost,
        h(
          "div",
          { class: "stack stack--tight" },
          pickerList(items),
          isTruncated(items, PICKER_LIMIT)
            ? h(
                "p",
                { class: "hint" },
                `Chỉ ${PICKER_LIMIT} lượt tiếp nhận gần nhất hiện ở đây; máy chủ có thể còn nữa.`,
              )
            : null,
        ),
      );
    } catch (error) {
      render(pickerHost, errorNotice(error, { onRetry: () => void loadPicker() }));
    }
  }

  /**
   * Resolve a `?request=` claim against the server before binding the form to it. A 404 here is
   * honest: the id may belong to another store or may have been mistyped, and the console cannot
   * tell which — so it says exactly that and fills nothing in.
   */
  async function prefill(id) {
    if (!UUID.test(id)) {
      render(
        summaryHost,
        h(
          "div",
          { class: "notice", dataState: "warn", id: "quote-request-summary" },
          h("p", { class: "notice__title" }, "Mã yêu cầu trong đường dẫn không hợp lệ"),
          h("p", null, "Không có dữ kiện nào được điền sẵn; hãy chọn từ danh sách bên dưới."),
        ),
      );
      return;
    }
    try {
      const item = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/order-requests/${encodeURIComponent(id)}`,
      );
      draft.orderRequestId = item.order_request_id;
      followRequest(item.order_request_id);
      draft.requestSummary = item;
      invalidateKey();
      refreshSummary();
    } catch (error) {
      if (/** @type {any} */ (error).kind === "MISSING") {
        render(
          summaryHost,
          h(
            "div",
            { class: "notice", dataState: "warn", id: "quote-request-summary" },
            h("p", { class: "notice__title" }, "Không tìm thấy yêu cầu này trong cửa hàng đang chọn"),
            h(
              "p",
              null,
              "Mã có thể thuộc cửa hàng khác hoặc đã bị gõ sai — máy chủ trả lời cùng một cách cho " +
                "cả hai, nên màn hình này cũng không đoán. Không có dữ kiện nào được điền sẵn; " +
                "hãy chọn từ danh sách bên dưới.",
            ),
          ),
        );
      } else {
        render(summaryHost, errorNotice(/** @type {any} */ (error)));
      }
    }
  }

  /**
   * Open one stored revision for reading, by id.
   *
   * This exists so that an approver can see what a `SET_RANGE_PRICE` envelope is about before
   * deciding it. `screens/approvals.js` refuses to enable its decision controls for a resource
   * type the console cannot render — approving a digest of content nobody was shown is blind
   * approval — and `QUOTE_REVISION` was on the wrong side of that line for as long as no route
   * returned a quote's lines. One does now, so the link is real and this is where it lands.
   *
   * Read-only on purpose, including for a staff member who could write: the person who proposed
   * the amounts may not approve them, and a panel that offered a control here would be inviting
   * the one press the server is guaranteed to refuse.
   *
   * @param {string} id
   * @param {string} [revision] the revision an envelope binds, when the caller arrived from one
   */
  async function openQuote(id, revision = "") {
    if (!UUID.test(id)) {
      render(
        openHost,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Mã báo giá trong đường dẫn không hợp lệ"),
          h("p", null, "Không mở được bản nào; chọn báo giá từ danh sách bên dưới."),
        ),
      );
      return;
    }
    // Refused rather than silently dropped. A caller that asked for a revision and got the newest
    // one instead is the exact failure this parameter exists to prevent, so a malformed value
    // opens nothing at all.
    if (revision && !/^[1-9][0-9]{0,8}$/.test(revision)) {
      render(
        openHost,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Số bản sửa đổi trong đường dẫn không hợp lệ"),
          h("p", null, "Không mở bản nào, vì mở nhầm bản còn tệ hơn không mở. Kiểm lại đường dẫn."),
        ),
      );
      return;
    }
    render(openHost, skeleton(2));
    try {
      const detail = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(id)}` +
          (revision ? `?revision=${encodeURIComponent(revision)}` : ""),
      );
      render(
        openHost,
        panel({
          eyebrow: "Đang xem",
          title: `Bản sửa đổi ${detail.revision}`,
          guardrail:
            "Bảng này chỉ để đọc. Nó cho thấy đúng những dòng và những khoảng giá mà một phiếu " +
            "duyệt niêm phong, để không ai phải duyệt một mã băm mà chưa nhìn thấy nội dung.",
          children: h(
            "div",
            { class: "stack" },
            h(
              "div",
              { class: "spread" },
              copyable({ value: String(detail.quote_id), display: shortId(detail.quote_id) }),
              priceStateBadge(detail.finality),
            ),
            facts([
              ["Trạng thái", enumVi(detail.status)],
              ["Phiên bản dòng", `v${detail.row_version}`],
              ["Hiệu lực đến", dateTime(detail.valid_until)],
              [
                "Tổng hiển thị cho khách",
                amount({
                  min: detail.display_total_min_vnd,
                  max: detail.display_total_max_vnd,
                  finality: detail.finality,
                  unknownLabel: "chưa có tổng",
                }),
                { span: true },
              ],
              [
                "Mã băm ảnh chụp",
                copyable({
                  value: String(detail.snapshot_hash),
                  display: shortHash(detail.snapshot_hash),
                }),
                { mono: true, span: true },
              ],
            ]),
            revisionLines(detail, catalog),
            reasonCodeList(detail.reason_codes || []),
          ),
        }),
      );
    } catch (error) {
      // A 404 here is most often store scope rather than a wrong id: the approvals queue spans
      // every store the approver is assigned to, while this read is scoped to the one selected in
      // the app bar. The server answers the same way for "another store's quote" and "no such
      // quote", so the console does not guess between them either -- it names both and says which
      // control fixes the first.
      render(
        openHost,
        /** @type {any} */ (error).kind === "MISSING"
          ? h(
              "div",
              { class: "notice", dataState: "warn" },
              h(
                "p",
                { class: "notice__title" },
                "Không tìm thấy bản báo giá này trong cửa hàng đang chọn",
              ),
              h(
                "p",
                null,
                "Báo giá có thể thuộc cửa hàng khác, hoặc mã đã sai — máy chủ trả lời giống nhau " +
                  "cho cả hai nên màn hình này cũng không đoán. Đổi cửa hàng ở thanh trên rồi mở " +
                  "lại đường dẫn. Đừng duyệt một phiếu mà bạn chưa xem được nội dung.",
              ),
            )
          : errorNotice(/** @type {any} */ (error), {
              onRetry: () => void openQuote(id, revision),
            }),
      );
    }
  }

  function validate() {
    if (!draft.orderRequestId.trim()) {
      return "Chọn một yêu cầu từ danh sách tiếp nhận, hoặc mở mục nâng cao để nhập mã.";
    }
    if (draft.lines.length === 0) return "Cần ít nhất một dòng.";
    for (const [index, line] of draft.lines.entries()) {
      if (!line.serviceCode) return `Dòng ${index + 1}: chưa chọn dịch vụ.`;
      // The picker can only yield a published code, so this can fail only if the catalog itself
      // carries a malformed one. That is a pricebook problem, and it says so rather than blaming
      // the operator for a field they cannot type into.
      if (!SERVICE_CODE.test(line.serviceCode)) {
        return `Dòng ${index + 1}: bảng giá trả về mã dịch vụ không hợp lệ (${line.serviceCode}).`;
      }
      if (!line.quantity.trim()) return `Dòng ${index + 1}: chưa nhập khối lượng.`;
      if (line.quantity.length > 16) return `Dòng ${index + 1}: khối lượng quá 16 ký tự.`;
      // Refused here rather than sent: the engine answers an unreadable quantity with
      // MISSING_REQUIRED_FACT, which reads as "a fact is missing" to somebody who typed one.
      if (parseQuantity(line.quantity, line.unit) === null) {
        return `Dòng ${index + 1}: ${quantityRefusal(line.quantity.trim(), line.unit)}`;
      }
    }
    if (draft.quoteId && !draft.rowVersion) {
      return "Thêm bản sửa đổi cần phiên bản dòng hiện tại; hãy chọn lại báo giá từ danh sách.";
    }
    // The fee is money the customer is held to, so it is refused here rather than rounded on the
    // way out. Anything with a comma or a fraction is a mistake to report, not a number to fix.
    if (draft.manualFeeVnd.trim() && parseDong(draft.manualFeeVnd) === null) {
      return "Phí giao phải là số nguyên đồng. “10.000” đọc là 10000; không nhận dấu phẩy hay số lẻ.";
    }
    return "";
  }

  /**
   * Paint one revision, and everything that revision makes possible.
   *
   * One function rather than three call sites, because the screen reaches this state three ways —
   * a fresh quote, an accepted one, and a band an owner just closed — and the earlier version
   * duplicated the render inside the acceptance callback. A band revision additionally gets the
   * closing card below the result; an exact one does not, and neither is ever both.
   *
   * @param {any} revision
   */
  function paintRevision(revision) {
    const contactId = draft.requestSummary?.contact_binding_id ?? null;
    // Every revision painted here is one the server just wrote, so it is the current one.
    track(revision);
    render(
      resultHost,
      revisionResult(
        revision,
        store,
        async (accepted) => {
          // Repaint from the accepted revision so the screen shows the final price and the
          // control is replaced by the record, rather than leaving a button that would only
          // be refused a second time. The contact id is read again on each paint rather than
          // captured once: an operator can unbind the intake while the customer is deciding, and
          // a stale capture would build a hand-off link pointing at somebody else.
          //
          // The confirmation is moved to the form's result line for the same reason as below:
          // `acceptControl` writes "Đã chốt" into a host that this repaint then destroys, so the
          // attestation used to be recorded and never announced.
          setResult(result, "ok", `Đã chốt. Bản sửa đổi ${accepted.revision} là giá cuối.`);
          paintRevision(accepted);
          await list.reload();
        },
        writeVerdict,
        contactId,
        {
          // Back to the form, which `track` has already put in revision mode for this quote, so
          // the next "Tính giá" writes a new revision at today's price. Nothing is sent from here.
          onReprice: () => {
            builderBody.scrollIntoView({ block: "start", behavior: "smooth" });
            const first = builderBody.querySelector("#quote-line-0-qty");
            if (first instanceof HTMLElement) first.focus({ preventScroll: true });
          },
          onReload: () => {
            void list.reload();
            list.host.scrollIntoView({ block: "nearest", behavior: "smooth" });
          },
        },
      ),
      revision.finality === "RANGE"
        ? bandCloser({
            result: revision,
            store,
            catalog,
            writeVerdict,
            onClosed: async (closed) => {
              // Reported to the form's own result line, not to the card, because the next
              // statement replaces the card. Without this the operator watches the closing form
              // vanish with no sentence saying the price was written -- which is the one thing
              // they need before they read the number back to the customer.
              setResult(result, "ok", `Đã ghi giá vào bản sửa đổi ${closed.revision}.`);
              paintRevision(closed);
              await list.reload();
            },
          })
        : null,
    );
  }

  /**
   * The one refusal on this screen that has a next action the console can offer.
   *
   * `GET /internal/v1/pricebook/services` carries no price kind, so nothing here knows which of
   * the forty-four published services are banded until the engine says so. When it does, the same
   * lines would be accepted as a band — and asking for one is a deliberate act with a person
   * behind it, which is why it is a button and not a flag this screen sets on every quote.
   *
   * @returns {HTMLElement}
   */
  function bandOffer() {
    const button = h(
      "button",
      { type: "button", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Lập bản khoảng giá",
    );
    button.addEventListener("click", () => {
      // Re-validated rather than trusted: the refusal this button answers leaves the form live,
      // so a line can have been emptied between the press that failed and this one.
      const problem = validate();
      if (problem) {
        setResult(result, "danger", problem);
        return;
      }
      // A different payload needs a different key: the server hashes the body alongside it, so
      // reusing the key with `present_range_as_band` added is a 409 rather than a second command.
      submission.reset();
      void send(true);
    });
    return h(
      "div",
      { class: "notice", dataState: "warn" },
      h("p", { class: "notice__title" }, "Món này niêm yết theo khoảng giá"),
      h(
        "p",
        null,
        "Bảng giá công bố một khoảng cho món này chứ không công bố một đơn giá, nên bộ tính giá " +
          "không tự chọn một số — đó là việc của người, và của chủ tiệm duyệt.",
      ),
      h(
        "p",
        null,
        "Bấm nút dưới đây để ghi lại đúng những dòng vừa nhập thành một bản khoảng giá. Đọc " +
          "khoảng cho khách nghe được ngay, và chốt một giá trong khoảng ở ngay bản đó.",
      ),
      h("div", { class: "form__actions" }, gated(button, writeVerdict)),
    );
  }

  async function submit(event) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setResult(result, "danger", problem);
      return;
    }
    await send(false);
  }

  /**
   * Price the drafted lines.
   *
   * @param {boolean} asBand whether to ask the server to store the band instead of refusing it
   */
  async function send(asBand) {
    const revisionMode = Boolean(draft.quoteId);
    const payload = {
      bound_order_request_id: draft.orderRequestId.trim(),
      lines: draft.lines.map((line) => ({
        service_code: line.serviceCode,
        // The typed digits, with "," read as the decimal mark. See the module note; `validate()`
        // has already refused anything this would turn into null.
        quantity: parseQuantity(line.quantity, line.unit),
        unit: line.unit,
        quantity_basis: line.basis,
      })),
      fulfillment_mode: draft.fulfillmentMode,
      // Absent is not zero: the server treats a missing distance as REQUIRE_HUMAN rather than as
      // a free delivery, so an empty box must stay empty rather than become 0.
      ...(draft.verifiedDistanceM.trim()
        ? { verified_distance_m: Number.parseInt(draft.verifiedDistanceM, 10) }
        : {}),
      // `parseDong`, not `parseInt`: a fractional entry used to be truncated silently, and the fee
      // it produced is the one the customer is held to. `validate()` refuses a null before this.
      ...(draft.manualFeeVnd.trim()
        ? { approved_manual_fee_vnd: parseDong(draft.manualFeeVnd) }
        : {}),
      ...(draft.customerAcknowledgedFee ? { customer_acknowledged_manual_fee: true } : {}),
      // Sent only when it is true, and only because somebody pressed "Lập bản khoảng giá". The
      // field is part of the idempotency payload on the server, so the two modes can never share
      // a key -- which is the behaviour wanted: they are two different intents.
      ...(asBand ? { present_range_as_band: true } : {}),
      ...(revisionMode
        ? {
            quote_id: draft.quoteId,
            expected_current_revision: Number.parseInt(draft.expectedRevision, 10) || 0,
          }
        : {}),
    };

    setResult(
      result,
      "warn",
      asBand ? "Đang ghi bản khoảng giá…" : "Đang gửi cho bộ tính giá…",
    );
    render(resultHost);

    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/quotes`, {
        method: "POST",
        body: payload,
        idempotencyKey: submission.key(),
        ...(revisionMode ? { ifMatch: Number.parseInt(draft.rowVersion, 10) } : {}),
      });
      // Confirmed exactly once, in one place. The next submission is a new intent.
      submission.reset();
      setResult(result, "ok", `Đã ghi bản sửa đổi ${created.revision}.`);
      paintRevision(created);
      await list.reload();
    } catch (error) {
      // A lost answer is not a refusal. Same rule as the order screen: on TIMEOUT or NETWORK the
      // quote may well exist, and saying it does not sends the operator to price the bag again --
      // which is how a customer ends up hearing two numbers for one bag.
      const unknown = error.kind === "TIMEOUT" || error.kind === "NETWORK";
      // The engine refused because a line is priced by inspection, and the same lines would be
      // stored as a band. Offered only on the ordinary path: a refusal that survives asking for a
      // band is a different problem, and re-offering the same button would loop.
      const bandable =
        !asBand &&
        error.kind === "REQUIRE_HUMAN" &&
        (error.reasonCodes || []).includes(NEEDS_A_HUMAN_PRICE);
      setResult(
        result,
        error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        unknown
          ? "Chưa biết lệnh có tới máy chủ hay không, nên chưa biết báo giá đã được ghi hay chưa. " +
            "Tải lại danh sách báo giá và kiểm tra trước khi tính lại."
          : bandable
            ? "Bộ tính giá không tự chọn số trong khoảng giá. Không có bản ghi nào được tạo."
            : error.kind === "REQUIRE_HUMAN"
              ? "Bộ tính giá từ chối đoán. Không có bản ghi nào được tạo."
              : "Không tạo được bản sửa đổi.",
      );
      render(resultHost, bandable ? bandOffer() : null, errorNotice(error));
      revealError(resultHost);
    }
  }

  function buildForm() {
    const orderRequestInput = h("input", {
      type: "text",
      value: draft.orderRequestId,
      autocomplete: "off",
      dataFormat: "id",
      placeholder: "00000000-0000-0000-0000-000000000000",
      onInput: (event) => {
        draft.orderRequestId = event.target.value;
        followRequest(event.target.value);
        draft.requestSummary = null;
        refreshSummary();
        submission.reset();
      },
    });

    return h(
      "form",
      { class: "form", onSubmit: submit },
      // Screen-owned and refreshed by `track`, so it always names the revision and row version the
      // next press will actually send.
      revisionBanner,
      // The bare-UUID path survives as a collapsed recovery hatch, not the default: picking from
      // Tiếp nhận or arriving with `?request=` binds a row the server returned, while a typed id
      // is a claim nobody checked. Typing here clears any resolved summary, because the claim and
      // the summary would no longer be about the same request.
      h(
        "details",
        null,
        h(
          "summary",
          { id: "quote-manual-toggle" },
          "Nhập mã yêu cầu thủ công (nâng cao — thường không cần)",
        ),
        h(
          "div",
          { class: "stack stack--tight" },
          labelled({
            id: "quote-order-request",
            label: "Mã yêu cầu đơn hàng (UUID)",
            hint: "Mỗi yêu cầu đơn hàng chỉ có đúng một báo giá. Lần thứ hai máy chủ báo trùng và yêu cầu thêm bản sửa đổi.",
            control: orderRequestInput,
          }),
        ),
      ),
      h(
        "div",
        { class: "stack stack--tight" },
        labelled({
          id: "quote-fulfillment",
          label: "Khách nhận đồ thế nào?",
          hint:
            "Khách tự mang đến và tự lấy về thì không có phí giao, và báo giá ra tổng tiền ngay. " +
            "Đơn có cả lấy và trả thì cần quãng đường đã đo: dưới 2km miễn phí, 2–6km 10.000đ, " +
            "trên 6km nhân viên và khách thỏa thuận. Đơn “Chỉ lấy” hoặc “Chỉ trả” không dùng bảng " +
            "quãng đường — phí luôn do nhân viên và khách thỏa thuận, ở mọi quãng đường.",
          control: fulfillmentSelect,
        }),
        distanceField,
        manualFeeField,
        manualAckField,
      ),
      lineEditor({
        catalog,
        lines: draft.lines,
        onStructuralChange: redrawBuilder,
        onValueChange: invalidateKey,
        onAddLine: addLine,
      }),
      h(
        "div",
        { class: "form__actions" },
        draft.lines.length < MAX_LINES
          ? h(
              "button",
              {
                type: "button",
                onClick: addLine,
              },
              "Thêm dòng",
            )
          : h("p", { class: "hint" }, `Tối đa ${MAX_LINES} dòng cho một báo giá.`),
      ),
      h(
        "div",
        { class: "action-bar" },
        gated(
          h(
            "button",
            { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
            "Tính giá",
          ),
          writeVerdict,
        ),
      ),
      result,
    );
  }

  // The opened revision waits for the catalog rather than racing it: its lines carry service
  // codes and the reader needs the published names, and a panel that paints codes and then
  // relabels itself a moment later reads as two different answers to one question.
  void loadCatalog().then(() => {
    if (openId) void openQuote(openId, openRevision);
  });
  void loadPicker();
  void list.reload();
  if (prefillId) void prefill(prefillId);

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Giá do máy chủ quyết"),
      h("h1", null, "Báo giá"),
      h(
        "p",
        { class: "screen__lede" },
        "Chọn yêu cầu, chọn dịch vụ, nhập khối lượng — máy chủ tính theo bảng giá đã chốt. Món " +
          "niêm yết theo khoảng giá thì nhân viên chốt một số trong khoảng và chủ tiệm duyệt. " +
          "Màn hình này không tự cộng tiền, không làm tròn, và không tự chọn số nào trong khoảng.",
      ),
    ),
    openHost,
    panel({
      eyebrow: "Lệnh",
      title: "Tính giá cho một yêu cầu",
      guardrail:
        "Thiếu dữ kiện thì máy chủ từ chối đoán và không ghi gì cả — người quyết, không phải máy.",
      children: h(
        "div",
        { class: "stack" },
        summaryHost,
        h(
          "div",
          { class: "card" },
          h(
            "div",
            { class: "spread" },
            h("p", { class: "eyebrow" }, "Chọn từ tiếp nhận gần đây"),
            h(
              "button",
              { type: "button", dataVariant: "quiet", onClick: () => void loadPicker() },
              icon("refresh"),
              "Tải lại",
            ),
          ),
          pickerHost,
        ),
        builderBody,
        resultHost,
      ),
    }),
    panel({
      eyebrow: "Đã ghi",
      title: "Báo giá của cửa hàng",
      count: list.count,
      children: h(
        "div",
        { class: "stack" },
        list.bar.node,
        list.filterStatus,
        list.truncation,
        list.host,
      ),
    }),
  );
}

export const screen = {
  path: "/quotes",
  title: "Báo giá",
  capability: "QUOTES_READ",
  needsStore: true,
  render: render_,
};
