"""Version identifiers for the deterministic queries an operational figure comes from.

Invariant 18: *dashboard numbers, SLA flags, and operational priorities are computed by versioned
deterministic queries/rules*. "Versioned" is the part that is easy to write down and hard to keep
true. A figure on a printout is only traceable if the identifier beside it names the rule that
produced it *and* that identifier is forced to move when the rule moves.

A bare string constant does not do that: somebody edits the SQL, the constant still reads `v1`, and
every figure ever printed under `v1` now names two different rules. So a version here is a pair —
the identifier a human quotes, and a digest of the rule text itself. A test pins both. Editing the
rule changes the digest, the pinned digest fails, and the only way to make the suite pass again is
to read what changed and publish a new identifier. That is the behaviour `OPS-BOARD-001` asks for:
"changing the rule fails the test until the version is changed".

Whitespace is collapsed before hashing. Re-indenting a SQL string is not a change of rule, and a
digest that moved on a reformat would train people to bump the pinned value without reading it,
which is the exact habit this is built to prevent. Anything that can change an answer — a column, a
predicate, an ORDER BY, a timezone, a policy identifier passed alongside — survives the collapse.

Nothing here computes a business figure. It labels the code that does.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from dataclasses import dataclass
from hashlib import sha256

#: Runs of whitespace are not part of a rule; see the module docstring.
_WHITESPACE = re.compile(r"\s+")

#: Sixteen hex characters of SHA-256. This digest is a change detector read by a human in a test
#: failure, not a security boundary, and a full 64-character digest in a pinned assertion is a
#: wall of hex nobody proofreads.
_DIGEST_LENGTH = 16


@dataclass(frozen=True, slots=True)
class QueryVersion:
    """What produced a figure: a published identifier, and a digest of the rule behind it."""

    identifier: str
    digest: str

    @property
    def label(self) -> str:
        """The single string that travels with a result, e.g. `sla-risk-board-v1:6f2a…`.

        One field rather than two on every response: the identifier is what a person quotes and the
        digest is what proves the identifier was not left behind, and separating them across a wire
        format invites a consumer to render only the half that never changes.
        """
        return f"{self.identifier}:{self.digest}"


def query_version(identifier: str, *rule_parts: str) -> QueryVersion:
    """Bind an identifier to the rule text that must change with it.

    `rule_parts` is everything that can change the answer and is not already inside the SQL: the
    business timezone a day boundary is taken in, the policy identifier an evaluation runs under,
    the column list an export publishes. Leaving one out does not make the version wrong today; it
    makes it unable to notice the day that part changes, which is worse.
    """
    if not identifier.strip():
        raise ValueError("a query version needs an identifier")
    if not rule_parts:
        raise ValueError("a query version needs the rule text it is a version of")
    normalized = "\n".join(_WHITESPACE.sub(" ", part).strip() for part in rule_parts)
    digest = sha256(normalized.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    return QueryVersion(identifier=identifier, digest=digest)


def rule_source(subject: object) -> str:
    """The behaviour of a Python rule, as text `query_version` can hash.

    Not every rule behind a figure is SQL. The production SLA engine is Python, and a version that
    hashed only the statement that selected the rows would sit unchanged beside numbers the engine
    had started computing differently — which is the same defect as an edited query under an
    unchanged `v1`.

    The digest is taken over the *structure*: `ast.dump` of the parsed source with every docstring
    removed. That draws the line in the same place the whitespace collapse above draws it. Comments,
    docstrings, line breaks and indentation are how people explain a rule and do not change an
    answer, so they are excluded; a renamed local, a reordered branch, a changed constant and a
    flipped comparison all change an answer, so they survive. Rewriting a docstring must not force a
    version bump, because a bump nobody had to think about is the habit this module exists to break.

    Pass a module rather than a function when the answer is produced by more than the entry point:
    reading `evaluate_production_sla` alone would miss a change to the validator it calls or to a
    published policy constant it is applied with.
    """
    try:
        source = inspect.getsource(subject)  # type: ignore[arg-type]
    except (OSError, TypeError) as error:
        # Fail closed rather than publish an identifier that names a rule nobody could read. A
        # figure whose version cannot be derived is a figure with no traceable rule behind it, and
        # invariant 18 has nothing left to mean at that point.
        message = f"the source of {subject!r} is not readable, so it cannot be versioned"
        raise ValueError(message) from error
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            node.body = _without_docstring(node.body)
    return ast.dump(tree)


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Drop a leading string expression, which is the only thing a docstring can be."""
    if not body:
        return body
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return body[1:]
    return body


__all__ = ["QueryVersion", "query_version", "rule_source"]
