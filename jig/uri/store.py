"""Resolver for ``project://store/...`` URIs (runtime JSONL stores).

Reads the ``.jig/store/*.jsonl`` op-logs. PR B (#216) wires the canonical
``tickets`` collection — ``_id``-keyed, the simplest shape. Threads (keyed by
ticket-id, in ``comments.jsonl``), messages, events, and checkpoints have
different keying/filtering and land in a follow-on; they raise
``UnimplementedAuthorityError`` for now.

This read path is synchronous (the resolver is sync and may run inside an event
loop), so it replays the JSONL op-log directly rather than going through the
async store classes. The op-log format mirrors ``jig.store.core.JsonlStore``:
each line is ``{"_op": insert|update|delete, "_id": ..., ...}``.

See ``docs/v2.0/uri-scheme/design.md`` §"`project://store/...`".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import ProjectUri

# Store collection -> ``.jig/store/<file>.jsonl``. PR B wires ``tickets`` only.
_WIRED_FILES: dict[str, str] = {"tickets": "tickets"}


def _replay_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    """Replay a JSONL op-log to the current ``{_id: doc}`` state. Missing file ->
    empty (a store that hasn't been written yet)."""
    docs: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return docs
    with path.open("r") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            record = json.loads(line)
            op, doc_id = record["_op"], record["_id"]
            if op == "insert":
                docs[doc_id] = {k: v for k, v in record.items() if k != "_op"}
            elif op == "update":
                docs[doc_id].update(
                    {k: v for k, v in record.items() if k not in ("_op", "_id")}
                )
            elif op == "delete":
                docs.pop(doc_id, None)
    return docs


def resolve_store_uri(uri: ProjectUri, project_root: Path) -> dict[str, Any]:
    collection = uri.path[0] if uri.path else None
    if collection not in _WIRED_FILES:
        raise UnimplementedAuthorityError(
            f"store collection {collection!r} not yet wired; got {uri!r}"
        )
    # Only the exact wired shapes resolve: `tickets` and `tickets/<id>`. Deeper
    # paths (sub-document addressing), fragments, and @revision pins are a
    # follow-on — reject them rather than silently resolving the parent / the
    # latest state (which would let a caller believe they pinned a revision).
    if uri.fragment is not None or len(uri.path) > 2:
        raise UnimplementedAuthorityError(
            f"store sub-addressing not yet wired; got {uri!r}"
        )
    if uri.revision is not None:
        raise UnimplementedAuthorityError(
            f"store @revision pinning not yet wired; got {uri!r}"
        )

    path = project_root / ".jig" / "store" / f"{_WIRED_FILES[collection]}.jsonl"
    docs = _replay_jsonl(path)

    if len(uri.path) == 1:  # project://store/tickets -> the list
        return {"kind": "ticket_list", "data": list(docs.values())}

    doc_id = uri.path[1]  # project://store/tickets/<id> -> one (or None)
    return {"kind": "ticket", "data": docs.get(doc_id)}
