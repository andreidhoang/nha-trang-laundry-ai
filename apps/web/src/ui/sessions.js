/**
 * "Thiết bị đang đăng nhập": which devices a person is signed in on, and signing one out
 * (`SESSION-LIST-001`, spec `COUNTER_COMPLETENESS_SPEC_V1.md` §3.7).
 *
 * One component, two places: the account sheet (the caller's own devices, GET internal/v1/sessions)
 * and a person's sheet on Nhân sự (the owner reading anyone's, GET internal/v1/staff/{id}/sessions).
 * Before it, the revoke route existed and nothing could name a session to it, so a lost phone had
 * one remedy: the owner disabling the whole account, which the console cannot undo.
 *
 * What the server decides, and this module only presents:
 *
 *   - **Which sessions are live.** A row here would authenticate at its next request; the server
 *     leaves out anything revoked, idled out, aged out or minted before a role change.
 *   - **Which row is this device.** `current` comes from the server, which compared the cookie the
 *     request carried. The principal's own `sessionId` is a second guard: that row is never offered
 *     a sign-out here, because the sign-out for this device is "Thoát", which also clears the
 *     browser's cookies.
 *   - **Who may sign out another device.** The revoke route admits the session itself, or any
 *     session for `OWNER_ADMIN`. So a member of staff sees their other devices and cannot sign them
 *     out: the control is shown shut with the reason once under the list (`SESSIONS_REVOKE_OTHER`),
 *     and the server's 403 would say the same.
 *   - **No device name.** The server stores no user agent, so a row is named by when it signed in
 *     and when it was last used. Inventing "iPhone của Lan" from a browser string would be a
 *     personal datum the shop never decided to keep.
 *
 * Every sign-out is two presses (`confirmButton`): it throws someone out mid-shift. Its key is per
 * session and kept across a failure, so a second press after a lost answer is the same intent. A
 * 404 means the device is already signed out — the goal state — and is said as such, not as an error.
 *
 * @module ui/sessions
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { ago, dateTime, shortId } from "../core/format.js";
import { errorNotice } from "./components.js";
import {
  confirmButton,
  emptyState,
  infoButton,
  list,
  listRow,
  show,
  skeletonRows,
  techDetails,
  toast,
} from "./kit.js";

/**
 * @typedef {object} SessionEntry a `StaffSessionEntryResponse`
 * @property {string} session_id
 * @property {string} issued_at
 * @property {string} last_seen_at
 * @property {string} idle_expires_at
 * @property {string} absolute_expires_at
 * @property {boolean} current
 */

/**
 * The ⓘ beside the "Thiết bị đang đăng nhập" title, in both places it appears.
 *
 * @returns {HTMLElement}
 */
export function devicesInfo() {
  return infoButton(
    "Đăng xuất một thiết bị làm gì?",
    h(
      "p",
      { class: "hint" },
      "Chỉ phiên trên thiết bị đó kết thúc, ngay ở lần nó gọi máy chủ kế tiếp; mọi thiết bị khác " +
        "của người này vẫn làm việc bình thường. Người đó đăng nhập lại được — muốn chặn hẳn thì " +
        "chủ dùng Vô hiệu hoá ở màn hình Nhân sự.",
    ),
    h(
      "p",
      { class: "hint" },
      "Máy chủ không lưu tên hay loại thiết bị, nên mỗi dòng được gọi theo lúc đăng nhập và lần " +
        "dùng gần nhất. Thiết bị đang cầm trên tay được đánh dấu “Thiết bị này”; muốn đăng xuất " +
        "nó thì bấm Thoát.",
    ),
  );
}

/**
 * The device list.
 *
 * @param {object} spec
 * @param {string} spec.path the read: the caller's own list, or one person's for the owner
 * @param {{allowed: boolean, reason: string}} spec.revokeOthers may this caller sign out a device
 *   that is not the one it is running on
 * @param {string|null} [spec.ownSessionId] the principal's own session, never offered a sign-out
 * @param {string} [spec.id] id of the host (test hook)
 * @returns {{node: HTMLElement, reload: () => Promise<void>}}
 */
export function deviceList(spec) {
  const host = h("div", { class: "stack stack--tight" }, skeletonRows(2));
  const node = h("div", { class: "devices", id: spec.id || null, dataDevices: "true" }, host);
  /** @type {Map<string, Submission>} kept per session, across a failure */
  const submissions = new Map();
  const reasonId = `${spec.id || "devices"}-revoke-reason`;
  const errorHost = h("div");
  let generation = 0;

  /** @param {string} sessionId */
  function submissionFor(sessionId) {
    let submission = submissions.get(sessionId);
    if (!submission) {
      submission = new Submission(`session-revoke-${sessionId}`);
      submissions.set(sessionId, submission);
    }
    return submission;
  }

  /** @param {SessionEntry} entry */
  async function revoke(entry) {
    const sessionId = String(entry.session_id);
    const submission = submissionFor(sessionId);
    render(errorHost);
    try {
      await request(`/internal/v1/sessions/${encodeURIComponent(sessionId)}/revoke`, {
        method: "POST",
        idempotencyKey: submission.key(),
      });
      submission.reset();
      toast("Đã đăng xuất thiết bị đó");
    } catch (error) {
      if (error?.kind === "MISSING") {
        // Already signed out -- by its own "Thoát", an idle expiry, or another press. The state the
        // person asked for is the state it is in, so this is said plainly and the list re-read.
        submission.reset();
        toast("Thiết bị đó đã đăng xuất rồi");
      } else {
        show(errorHost, errorNotice(error, { title: "Chưa đăng xuất được thiết bị đó." }));
        return;
      }
    }
    await reload();
  }

  /**
   * @param {SessionEntry} entry
   * @param {number} index
   */
  function row(entry, index) {
    const sessionId = String(entry.session_id || "");
    const mine = entry.current === true || (spec.ownSessionId && spec.ownSessionId === sessionId);
    let control = null;
    if (!mine) {
      control = confirmButton({
        label: "Đăng xuất thiết bị này",
        confirmLabel: "Bấm lần nữa để đăng xuất",
        onConfirm: () => void revoke(entry),
      });
      control.dataset.revokeSession = sessionId;
      if (!spec.revokeOthers.allowed) {
        // Shut, visible, and pointing at the one reason under the list: the same contract as
        // `gated()`, without printing the same sentence once per device.
        control.setAttribute("disabled", "");
        control.setAttribute("aria-disabled", "true");
        control.setAttribute("data-denied", "true");
        control.setAttribute("aria-describedby", reasonId);
      }
    }
    return listRow({
      title: entry.current === true ? "Thiết bị này" : `Thiết bị ${index + 1}`,
      meta: [
        // Recency first: "dùng 2 giờ trước" is what tells a person which device is the lost one.
        h("span", null, `Dùng ${ago(entry.last_seen_at)} · đăng nhập ${dateTime(entry.issued_at)}`),
        control ? h("span", { class: "devices__action" }, control) : null,
      ],
      data: { sessionId, sessionCurrent: mine ? "true" : "false" },
    });
  }

  async function reload() {
    const mine = ++generation;
    show(host, skeletonRows(2));
    try {
      const body = await request(spec.path);
      if (mine !== generation) return;
      const entries = /** @type {SessionEntry[]} */ (
        Array.isArray(body?.sessions) ? body.sessions : []
      );
      // This device first, then the rest as the server ordered them (most recent use first).
      const ordered = [
        ...entries.filter((entry) => entry.current === true),
        ...entries.filter((entry) => entry.current !== true),
      ];
      const others = ordered.filter((entry) => entry.current !== true).length;
      // The refusal and the truncation are said before the rows, not after them: with several
      // devices the list runs off a phone screen, and a reason under it is a reason nobody reads.
      render(
        host,
        others && !spec.revokeOthers.allowed
          ? h("p", { class: "hint", id: reasonId }, spec.revokeOthers.reason)
          : null,
        body?.truncated
          ? h(
              "p",
              { class: "hint", role: "status" },
              `Máy chủ cắt danh sách ở ${ordered.length} thiết bị; có thể còn nữa.`,
            )
          : null,
        errorHost,
        ordered.length
          ? list(
              ordered.map((entry, index) => row(entry, index)),
              { label: "Thiết bị đang đăng nhập" },
            )
          : emptyState({ icon: "user", title: "Không có thiết bị nào đang đăng nhập." }),
        ordered.length
          ? techDetails(
              ordered.map((entry) => [
                entry.current === true ? "Mã phiên thiết bị này" : "Mã phiên",
                shortId(String(entry.session_id)),
                { copy: String(entry.session_id) },
              ]),
            )
          : null,
      );
    } catch (error) {
      if (mine !== generation) return;
      show(host, errorNotice(error, { onRetry: () => void reload() }));
    }
  }

  return { node, reload };
}
