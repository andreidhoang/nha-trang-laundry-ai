"""The remedy credits a cancellation voids, nets or reissues (`DEC-045`, `DEC-046`, `0066`).

`MONEY-LIFECYCLE-009` (review M3, M7). Every decision is `nha_trang_laundry_domain.
cancellation_money.cancellation_money_plan`'s, over two facts read here under the order's row lock:

* the credits **issued from** the order (spent since or not), each chain by its latest link -- a
  credit spent, then reissued under `DEC-046`, is represented by the reissue, so it is voided or
  netted once;
* the credits the order's accepted revision **spent** (`redeemed_quote_id`/`_revision`).

`plan_cancellation_money` reads them and asks the domain; `write_cancellation_credit_moves` carries
the plan out inside the transaction that cancels the order -- one void or reissue at a time, each
its own `REMEDY_CREDIT` event, audit row and outbox row (a savepoint, as `spend_reserved_remedy_
credits` writes the spend inside order creation). The netting is the refund row's
(`order_refunds.netted_remedy_vnd`), written by `OrderRepository` beside the refund it reduces.

`read_order_cancellation_money` is the read the order page asks before the press (the refund sheet
states what will happen) and after it (the order and the receipt state what did).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from nha_trang_laundry_domain.cancellation_money import (
    DEC_NO_DOUBLE_COMPENSATION,
    DEC_SPENT_CREDIT_REISSUED,
    REMEDY_KIND_VI,
    CancellationMoneyPlan,
    CreditState,
    RemedyCreditFact,
    cancellation_money_plan,
)
from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.remedies import RemedyKind

from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

REMEDY_CREDIT_VOIDED: Final = "REMEDY_CREDIT_VOIDED"
REMEDY_CREDIT_REISSUED: Final = "REMEDY_CREDIT_REISSUED"


class CancellationMoneyError(RuntimeError):
    """A credit moved under the cancellation's row lock; nothing is written."""


@dataclass(frozen=True, slots=True)
class _LockedCredit:
    fact: RemedyCreditFact
    row_version: int
    remedy_proposal_id: UUID
    store_id: UUID
    issued_from_order_id: UUID
    policy_version_id: UUID
    bearer_contact_id: UUID


@dataclass(frozen=True, slots=True)
class LockedCancellationMoney:
    """The plan, and the locked credit rows it was decided over."""

    plan: CancellationMoneyPlan
    credits: dict[UUID, _LockedCredit]
    #: Who a reissued credit is issued to: the cancelled order's own customer reference.
    order_contact_id: UUID | None
    #: J2: the id each reissued credit will have, by the credit it replaces -- minted here, before
    #: anything is written, so the cancellation's own event and audit row can name the reissues
    #: they record (`plan_document`), not an empty list.
    reissue_ids: dict[UUID, UUID] = field(default_factory=dict)

    def reissued_pairs(self) -> tuple[tuple[UUID, UUID], ...]:
        """`(original, reissue)` for every credit the plan reissues, in the plan's order."""

        return tuple(
            (fact.credit_id, self.reissue_ids[fact.credit_id]) for fact in self.plan.reissued
        )


_ISSUED_SQL: Final = """
    SELECT c.id, p.kind, c.amount_vnd, c.redeemed_at IS NOT NULL, c.voided_at IS NOT NULL,
           c.row_version, c.remedy_proposal_id, c.store_id, c.issued_from_order_id,
           c.policy_version_id, c.bearer_contact_id
    FROM remedy_credits c
    JOIN remedy_proposals p ON p.id = c.remedy_proposal_id
    WHERE c.issued_from_order_id = %s
      AND NOT EXISTS (SELECT 1 FROM remedy_credits r WHERE r.reissue_of = c.id)
    ORDER BY c.issued_at, c.id
"""

#: The credits the order's bill spent, each with J3's fact: was the order it came from cancelled
#: (not disposed of -- `DEC-036` keeps every credit) without its refund taking the credits' whole
#: face value off? Covered means a refund row exists and it netted the whole face value of every
#: credit from that order spent by then -- the chain latest links, as `_ISSUED_SQL` reads them, at
#: that instant. Every credit from that order still spendable afterwards descends from one of
#: those (an unspent one was voided with the cancellation), so a credit reissued from a netted one
#: and spent again later is covered too: its value was taken back once.
_SPENT_SQL: Final = """
    SELECT c.id, p.kind, c.amount_vnd, c.redeemed_at IS NOT NULL, c.voided_at IS NOT NULL,
           c.row_version, c.remedy_proposal_id, c.store_id, c.issued_from_order_id,
           c.policy_version_id, c.bearer_contact_id,
           (
               issuer.commercial_status = 'CANCELLED'
               AND NOT EXISTS (SELECT 1 FROM order_disposals d WHERE d.order_id = issuer.id)
               AND NOT coalesce((
                   SELECT r.netted_remedy_vnd >= (
                          SELECT coalesce(sum(n.amount_vnd), 0)
                          FROM remedy_credits n
                          WHERE n.issued_from_order_id = issuer.id
                            AND n.redeemed_at IS NOT NULL
                            AND n.redeemed_at <= r.refunded_at
                            AND NOT EXISTS (
                                SELECT 1 FROM remedy_credits again
                                WHERE again.reissue_of = n.id AND again.issued_at <= r.refunded_at
                            )
                      )
                   FROM order_refunds r WHERE r.order_id = issuer.id
               ), FALSE)
           ) AS issuer_uncovered
    FROM orders o
    JOIN remedy_credits c
      ON c.redeemed_quote_id = o.current_quote_id
     AND c.redeemed_quote_revision = o.current_quote_revision
    JOIN remedy_proposals p ON p.id = c.remedy_proposal_id
    JOIN orders issuer ON issuer.id = c.issued_from_order_id
    WHERE o.id = %s
    ORDER BY c.issued_at, c.id
"""


#: Whether any remedy credit touches the order at all -- issued from it, or spent on its bill -- by
#: `0042`'s own columns. Nearly every cancellation answers no, and then nothing else is read.
_TOUCHED_SQL: Final = """
    SELECT EXISTS (
        SELECT 1 FROM orders o
        JOIN remedy_credits c
          ON c.issued_from_order_id = o.id
          OR (
              c.redeemed_quote_id = o.current_quote_id
              AND c.redeemed_quote_revision = o.current_quote_revision
          )
        WHERE o.id = %s
    )
"""


def credits_touch_order(cursor: Any, order_id: UUID) -> bool:
    """Whether any remedy credit was issued from the order or spent on its bill."""

    cursor.execute(_TOUCHED_SQL, (order_id,))
    row = cursor.fetchone()
    return bool(row and row[0])


def _locked(row: Sequence[object]) -> _LockedCredit:
    state = CreditState.VOIDED if row[4] else CreditState.SPENT if row[3] else CreditState.UNSPENT
    return _LockedCredit(
        fact=RemedyCreditFact(
            credit_id=_uuid(row[0]),
            kind=RemedyKind(str(row[1])),
            amount_vnd=int(str(row[2])),
            state=state,
            issuer_uncovered=len(row) > 11 and bool(row[11]),
        ),
        row_version=int(str(row[5])),
        remedy_proposal_id=_uuid(row[6]),
        store_id=_uuid(row[7]),
        issued_from_order_id=_uuid(row[8]),
        policy_version_id=_uuid(row[9]),
        bearer_contact_id=_uuid(row[10]),
    )


def plan_cancellation_money(
    connection: Any,
    *,
    order_id: UUID,
    resolution: CustodyResolution | None,
    refundable_vnd: int,
    lock: bool = True,
) -> LockedCancellationMoney:
    """Read (and lock) the credits the cancellation touches, and ask the domain what happens."""

    suffix = " FOR UPDATE OF c" if lock else ""
    with connection.cursor() as cursor:
        if not credits_touch_order(cursor, order_id):
            return LockedCancellationMoney(
                plan=cancellation_money_plan(
                    resolution=resolution,
                    refundable_vnd=refundable_vnd,
                    issued_from_order=(),
                    spent_on_order=(),
                ),
                credits={},
                order_contact_id=None,
            )
        cursor.execute(_ISSUED_SQL + suffix, (order_id,))
        issued = [_locked(row) for row in cursor.fetchall()]
        cursor.execute(_SPENT_SQL + suffix, (order_id,))
        spent = [_locked(row) for row in cursor.fetchall()]
        cursor.execute("SELECT bound_contact_id FROM orders WHERE id = %s", (order_id,))
        contact = cursor.fetchone()
    plan = cancellation_money_plan(
        resolution=resolution,
        refundable_vnd=refundable_vnd,
        issued_from_order=tuple(credit.fact for credit in issued),
        spent_on_order=tuple(credit.fact for credit in spent),
    )
    return LockedCancellationMoney(
        plan=plan,
        credits={credit.fact.credit_id: credit for credit in (*issued, *spent)},
        order_contact_id=None if contact is None or contact[0] is None else _uuid(contact[0]),
        reissue_ids={fact.credit_id: uuid4() for fact in plan.reissued},
    )


def write_cancellation_credit_moves(
    connection: Any,
    locked: LockedCancellationMoney,
    *,
    order_id: UUID,
    refund_id: UUID | None,
    resolution: CustodyResolution | None,
    actor_id: UUID,
    correlation_id: UUID,
    occurred_at: datetime,
) -> tuple[tuple[UUID, UUID], ...]:
    """Void (`DEC-045`) then reissue (`DEC-046`), each with its event, audit row and outbox row.

    Called inside the transaction that cancels the order, after the order's own write, so the
    deferred checks of `0066` see the order cancelled at commit. Returns `(original, reissue)`
    pairs for the reissued credits.
    """

    plan = locked.plan
    context: dict[str, object] = {
        "order_id": str(order_id),
        "refund_id": None if refund_id is None else str(refund_id),
        "custody_resolution": None if resolution is None else resolution.value,
    }
    for fact in plan.voided:
        credit = locked.credits[fact.credit_id]

        def void(cursor: Any, credit: _LockedCredit = credit) -> None:
            cursor.execute(
                """
                UPDATE remedy_credits
                SET voided_at = %s, voided_by_staff_id = %s, voided_with_order_id = %s,
                    row_version = row_version + 1
                WHERE id = %s AND row_version = %s AND redeemed_at IS NULL AND voided_at IS NULL
                RETURNING id
                """,
                (occurred_at, actor_id, order_id, credit.fact.credit_id, credit.row_version),
            )
            if cursor.fetchone() is None:
                raise CancellationMoneyError("STALE_VERSION: the credit moved during the cancel")

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="REMEDY_CREDIT",
                aggregate_id=fact.credit_id,
                aggregate_version=credit.row_version + 1,
                event_type=REMEDY_CREDIT_VOIDED,
                event_payload={
                    **context,
                    "amount_vnd": fact.amount_vnd,
                    "decision_ref": DEC_NO_DOUBLE_COMPENSATION,
                },
                audit_action="REMEDY_CREDIT_VOID",
                actor_type="STAFF",
                actor_id=actor_id,
                correlation_id=correlation_id,
                audit_details={
                    **context,
                    "reason": "ORDER_CANCELLED_WITHOUT_CHARGE",
                    "decision_ref": DEC_NO_DOUBLE_COMPENSATION,
                },
                outbox_events=(
                    OutboxEvent(
                        "remedy.credit_voided.v1",
                        {"credit_id": str(fact.credit_id), "order_id": str(order_id)},
                        f"remedy-credit:{fact.credit_id}:voided",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            void,
        )
    reissued: list[tuple[UUID, UUID]] = []
    for fact in plan.reissued:
        original = locked.credits[fact.credit_id]
        new_id = locked.reissue_ids[fact.credit_id]
        bearer = locked.order_contact_id or original.bearer_contact_id

        def reissue(
            cursor: Any,
            original: _LockedCredit = original,
            new_id: UUID = new_id,
            bearer: UUID = bearer,
        ) -> None:
            cursor.execute(
                """
                INSERT INTO remedy_credits (
                    id, remedy_proposal_id, store_id, bearer_contact_id, issued_from_order_id,
                    amount_vnd, direction, policy_version_id, issued_at, reissue_of,
                    reissued_for_order_id
                ) VALUES (%s, %s, %s, %s, %s, %s, 'CREDIT', %s, %s, %s, %s)
                """,
                (
                    new_id,
                    original.remedy_proposal_id,
                    original.store_id,
                    bearer,
                    original.issued_from_order_id,
                    original.fact.amount_vnd,
                    original.policy_version_id,
                    occurred_at,
                    original.fact.credit_id,
                    order_id,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="REMEDY_CREDIT",
                aggregate_id=new_id,
                aggregate_version=1,
                event_type=REMEDY_CREDIT_REISSUED,
                event_payload={
                    **context,
                    "reissue_of": str(fact.credit_id),
                    "amount_vnd": fact.amount_vnd,
                    "decision_ref": DEC_SPENT_CREDIT_REISSUED,
                },
                audit_action="REMEDY_CREDIT_REISSUE",
                actor_type="STAFF",
                actor_id=actor_id,
                correlation_id=correlation_id,
                audit_details={
                    **context,
                    "reissue_of": str(fact.credit_id),
                    "decision_ref": DEC_SPENT_CREDIT_REISSUED,
                },
                outbox_events=(
                    OutboxEvent(
                        "remedy.credit_reissued.v1",
                        {
                            "credit_id": str(new_id),
                            "reissue_of": str(fact.credit_id),
                            "order_id": str(order_id),
                        },
                        f"remedy-credit:{fact.credit_id}:reissued",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            reissue,
        )
        reissued.append((fact.credit_id, new_id))
    return tuple(reissued)


def plan_document(
    plan: CancellationMoneyPlan, reissued: Sequence[tuple[UUID, UUID]] = ()
) -> dict[str, object]:
    """What the order's cancellation event and audit row record about the remedy money."""

    return {
        "voided_credit_ids": [str(c.credit_id) for c in plan.voided],
        "netted_credit_ids": [str(c.credit_id) for c in plan.netted],
        "netted_remedy_vnd": plan.netted_vnd,
        "reissued_credits": [
            {"reissue_of": str(original), "credit_id": str(new)} for original, new in reissued
        ],
    }


# --- the read the order page asks -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CancellationMoneyView:
    """What a cancellation without charge does, or did, to the remedy money on one order."""

    #: `PREVIEW` before the cancellation (what the refund sheet states), `DONE` after it.
    stage: Literal["PREVIEW", "DONE"]
    #: What would be (or was) paid back before netting, and what the netting takes off.
    refundable_vnd: int
    netted_vnd: int
    refund_vnd: int
    voided_vnd: int
    reissued_vnd: int
    lines_vi: tuple[str, ...]
    #: J3: before the cancellation, why it would be refused over remedy money (`lines_vi` then
    #: holds that sentence alone), or `None`.
    refusal: str | None = None


def read_order_cancellation_money(
    cursor: Any, *, order_id: UUID, commercial: str, balance: str, paid_vnd: int
) -> CancellationMoneyView | None:
    """`None` when no remedy credit touches the order; otherwise the preview or the record.

    Before the order is cancelled: the plan for a refunding cancellation of what the ledger holds
    now (the sheet names the resolutions that refund; `NOT_RECEIVED` refunds nothing, which the
    plan for 0 states). After: what the refund row and the credits record -- the stored figures,
    not a recomputation.
    """

    if not credits_touch_order(cursor, order_id):
        # A netting or a move needs a credit from the order or on its bill; none, nothing to say.
        return None
    if commercial == "CANCELLED":
        cursor.execute(
            """
            SELECT r.refunded_amount_vnd, r.netted_remedy_vnd
            FROM order_refunds r WHERE r.order_id = %s
            """,
            (order_id,),
        )
        refund = cursor.fetchone()
        cursor.execute(
            """
            SELECT c.id, p.kind, c.amount_vnd,
                   CASE WHEN c.voided_with_order_id = %(order)s THEN 'VOIDED'
                        WHEN c.reissued_for_order_id = %(order)s THEN 'REISSUED'
                        ELSE 'NETTED' END
            FROM remedy_credits c
            JOIN remedy_proposals p ON p.id = c.remedy_proposal_id
            WHERE c.voided_with_order_id = %(order)s OR c.reissued_for_order_id = %(order)s
            ORDER BY c.issued_at, c.id
            """,
            {"order": order_id},
        )
        moved = cursor.fetchall()
        netted = 0 if refund is None else int(str(refund[1]))
        if not moved and netted == 0:
            return None
        voided = [row for row in moved if row[3] == "VOIDED"]
        again = [row for row in moved if row[3] == "REISSUED"]
        refunded = 0 if refund is None else int(str(refund[0]))
        lines = [f"Khoản {_credit_vi(row)} khách chưa dùng đã được huỷ cùng đơn." for row in voided]
        if netted:
            lines.append(
                f"Đã trừ {_vnd(netted)} (khoản khách đã dùng ở đơn khác): hoàn "
                f"{_vnd(refunded)} thay vì {_vnd(refunded + netted)}."
            )
        lines.extend(f"Đã cấp lại cho khách khoản {_credit_vi(row)}." for row in again)
        return CancellationMoneyView(
            stage="DONE",
            refundable_vnd=refunded + netted,
            netted_vnd=netted,
            refund_vnd=refunded,
            voided_vnd=sum(int(str(row[2])) for row in voided),
            reissued_vnd=sum(int(str(row[2])) for row in again),
            lines_vi=tuple(lines),
        )
    refundable = paid_vnd if balance in {"PAID", "PARTIALLY_PAID"} else 0
    locked = plan_cancellation_money(
        cursor.connection,
        order_id=order_id,
        resolution=CustodyResolution.SHOP_FAULT_NO_CHARGE,
        refundable_vnd=refundable,
        lock=False,
    )
    plan = locked.plan
    if not plan.moves_remedy_money:
        return None
    figures = plan.figures()
    return CancellationMoneyView(
        stage="PREVIEW",
        refundable_vnd=plan.refundable_vnd,
        netted_vnd=figures.netted_vnd,
        refund_vnd=figures.refund_vnd,
        voided_vnd=figures.voided_vnd,
        reissued_vnd=figures.reissued_vnd,
        lines_vi=plan.lines_vi,
        refusal=None if plan.refusal is None else plan.refusal.value,
    )


def _credit_vi(row: Sequence[object]) -> str:
    kind = RemedyKind(str(row[1]))
    return f"{REMEDY_KIND_VI.get(kind, kind.value)} {_vnd(int(str(row[2])))}"


def _vnd(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " ₫"


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "REMEDY_CREDIT_REISSUED",
    "REMEDY_CREDIT_VOIDED",
    "CancellationMoneyError",
    "CancellationMoneyView",
    "LockedCancellationMoney",
    "credits_touch_order",
    "plan_cancellation_money",
    "plan_document",
    "read_order_cancellation_money",
    "write_cancellation_credit_moves",
]
