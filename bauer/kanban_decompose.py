"""Fan-out a complex task into a DAG of children via the auxiliary LLM.

Decompose is the qualitative leap of Wave 3: instead of writing every
sub-task by hand, the user drops a single goal (`"build OAuth login"`) and
the auxiliary model returns a structured plan with parallelism declared
via parent indices.

Sample call::

    from bauer.kanban_decompose import decompose_task

    outcome = decompose_task("042")
    if outcome.ok and outcome.fanout:
        print(f"Created {len(outcome.child_ids)} children for {outcome.task_id}")
    elif outcome.ok and not outcome.fanout:
        print("LLM said single-task; promoted to todo instead")

The decomposer returns `{fanout, tasks[], rationale}`:
    fanout=true:
        tasks: [{title, body, assignee, parents: [indices]}, ...]
        - `parents` is a list of indices INTO `tasks` (sibling-relative DAG)
        - root task becomes child of every leaf so it waits for the whole graph
    fanout=false:
        Treat as a single-task spec → falls back to `kanban_specify.specify_task`

Each new child task is created via `kb.create_task` and linked through
`kb.link_tasks`, which runs Kahn's algorithm to reject any cycle the LLM
might have hallucinated. The whole write is wrapped in a single transaction
so partial creation is impossible.

Inspired by Hermes Agent's `hermes_cli/kanban_decompose.py`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from . import kanban_db as kb
from .auxiliary_client import get_text_auxiliary_client
from .kanban_specify import _coerce_body, _coerce_title, _extract_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class DecomposeOutcome:
    """Result of `decompose_task()`. Inspect `.ok` and `.fanout` together."""
    task_id: str
    ok: bool
    fanout: bool = False
    child_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    reason: str = ""  # populated when ok=False


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------


# Bounds on the LLM-generated child count. < MIN → treat as single-task spec
# (fanout=false); > MAX → truncate (we'd rather lose tasks than overwhelm).
MIN_CHILDREN = 2
MAX_CHILDREN = 6

# Cap on the input body we send to the LLM. Triage descriptions can be
# multi-page; sending all of it bloats cost without helping decomposition.
_MAX_INPUT_BODY_CHARS = 4000

# Cap on LLM response so a misbehaving model can't drag the chat forever.
_MAX_RESPONSE_CHARS = 12_000


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You are a project planner. Decompose a single engineering \
task into a small DAG of independently-actionable sub-tasks.

INPUT: a task dict with `id`, `title`, `body`.

OUTPUT: a single JSON object with exactly these keys:
  {
    "fanout": true | false,
    "tasks": [
      {
        "title":   "imperative phrase, <= 80 chars",
        "body":    "1-3 sentence body for this sub-task",
        "assignee": "" or a short role name (developer, designer, ...)",
        "parents": [0, 2]   // indices into `tasks` of sibling parents
      },
      ...
    ],
    "rationale": "1-2 sentences on why you decomposed this way"
  }

DECOMPOSITION RULES:
- Prefer 2-6 sub-tasks. If the task is genuinely atomic, set fanout=false \
and return a single-element `tasks` array.
- Prefer parallelism: when two sub-tasks don't depend on each other, give \
them empty `parents` lists so they can run in parallel.
- Use the `parents` indices to express real dependencies only. Indices \
refer to positions in this same `tasks` list.
- No cycles. No self-references. Indices must be valid (>=0, < len(tasks)).
- Inherit the input's overall goal — don't introduce features that weren't \
implied.

OUTPUT RULES:
- ONLY emit JSON. No prose before or after.
- Use the literal booleans true / false (not "true").
- Body fields can use markdown but stay short.
"""


# ---------------------------------------------------------------------------
# decompose_task — main entry point
# ---------------------------------------------------------------------------


def decompose_task(
    task_id: str,
    *,
    board: str | None = None,
    cfg=None,
    author: str = "auxiliary",
) -> DecomposeOutcome:
    """Fan-out a single task into a DAG of children via the auxiliary LLM.

    Args:
        task_id: Parent task. Should usually be in `triage` or `todo`. Tasks
            already `running`, `done`, etc. are rejected to avoid disrupting
            active work.
        board: kanban_db board. None = active board.
        cfg: BauerConfig. None = autoload from `config.yaml`.
        author: Comment / event author label for audit trail.

    Returns:
        `DecomposeOutcome`. Check `.ok` first:
            - ok=True, fanout=True, child_ids=[...]: graph created
            - ok=True, fanout=False: LLM said atomic; the parent stays put
            - ok=False, reason=...: see failure modes below

    Failure modes (`reason`):
        - "task_not_found": id missing
        - "wrong_status": parent isn't in triage/todo
        - "auxiliary_unavailable": LLM slot returned (None, None)
        - "llm_invalid_json": couldn't extract JSON
        - "llm_invalid_structure": JSON shape didn't validate
        - "cycle_detected": the proposed graph has a cycle
        - "internal_error: <msg>": DB write failed

    Side effects on success (fanout=True):
        - 1-6 new tasks inserted in status=todo
        - task_links rows connect siblings per `parents` indices
        - root task gets a `decomposed_by_<author>` event + comment
        - root task gets the leaves of the DAG as parents, so dispatcher
          won't promote it to ready until the whole subgraph completes
    """
    try:
        with kb.connect(board) as conn:
            kb.init_db(conn)
            root = kb.get_task_or_none(conn, task_id)
            if root is None:
                return DecomposeOutcome(task_id, ok=False, reason="task_not_found")

            # Only safe to decompose tasks that haven't started yet.
            if root.status not in (kb.STATUS_TRIAGE, kb.STATUS_TODO):
                return DecomposeOutcome(
                    task_id, ok=False,
                    reason=f"wrong_status: expected triage/todo, got "
                           f"{root.status!r}",
                )

            client, model = get_text_auxiliary_client("kanban_decomposer", cfg)
            if client is None or not model:
                return DecomposeOutcome(
                    task_id, ok=False, reason="auxiliary_unavailable",
                )

            raw = _call_decomposer(client, model, root)
            parsed = _extract_json(raw or "")
            if not parsed:
                logger.info("kanban_decompose: invalid JSON; raw=%r",
                            (raw or "")[:300])
                return DecomposeOutcome(task_id, ok=False,
                                         reason="llm_invalid_json")

            try:
                children_specs, rationale, want_fanout = _validate(parsed)
            except ValueError as exc:
                logger.info("kanban_decompose: invalid structure: %s", exc)
                return DecomposeOutcome(
                    task_id, ok=False, reason=f"llm_invalid_structure: {exc}",
                )

            # fanout=false: treat as a single-task spec. Delegate to
            # kanban_specify rather than reimplementing here, so the audit
            # trail / behaviour stays consistent.
            if not want_fanout:
                return _handle_single_task_spec(
                    conn, root, children_specs, rationale, author,
                )

            # Pre-check the parent indices form a DAG before we touch the DB.
            if _children_have_cycle(children_specs):
                return DecomposeOutcome(
                    task_id, ok=False, reason="cycle_detected",
                )

            child_ids = _materialise_children(
                conn, root, children_specs, model, author,
            )

            return DecomposeOutcome(
                task_id=root.id, ok=True, fanout=True,
                child_ids=child_ids, rationale=rationale,
            )
    except Exception as exc:
        logger.warning("kanban_decompose(%r): %s", task_id, exc)
        return DecomposeOutcome(task_id, ok=False,
                                 reason=f"internal_error: {exc}")


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _call_decomposer(client, model: str, task) -> str:
    """Send the decomposition request and join the streamed chunks."""
    user_payload = {
        "id": task.id,
        "title": task.title or "",
        "body": (task.body or "")[:_MAX_INPUT_BODY_CHARS],
    }
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    chunks: list[str] = []
    try:
        for chunk in client.chat_stream(model, messages):
            chunks.append(chunk)
            if sum(len(c) for c in chunks) > _MAX_RESPONSE_CHARS:
                break
    except Exception as exc:
        logger.info("kanban_decompose: chat_stream raised: %s", exc)
        return ""
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(parsed: dict) -> tuple[list[dict], str, bool]:
    """Tighten the LLM response into a reliable shape.

    Returns:
        (children_specs, rationale, want_fanout)
            children_specs: list of dicts with keys title/body/assignee/parents
            rationale: short prose from the LLM
            want_fanout: whether to actually create children or fall back to
                a single-task spec

    Raises:
        ValueError: shape is unrecoverable (wrong types, no tasks, etc.)
    """
    if not isinstance(parsed, dict):
        raise ValueError("response is not an object")

    want_fanout = bool(parsed.get("fanout", False))
    rationale = str(parsed.get("rationale") or "").strip()[:500]

    raw_tasks = parsed.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("'tasks' must be a non-empty list")

    children: list[dict] = []
    n = len(raw_tasks)
    for idx, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise ValueError(f"tasks[{idx}] is not an object")
        title = _coerce_title(raw.get("title"), "")
        if not title:
            raise ValueError(f"tasks[{idx}] missing title")
        body = _coerce_body(raw.get("body"), "")
        assignee = str(raw.get("assignee") or "").strip()
        parents_raw = raw.get("parents") or []
        if not isinstance(parents_raw, list):
            raise ValueError(f"tasks[{idx}].parents must be a list")
        parent_indices: list[int] = []
        for p in parents_raw:
            try:
                pi = int(p)
            except (TypeError, ValueError):
                raise ValueError(
                    f"tasks[{idx}].parents has non-integer index {p!r}"
                )
            if pi == idx:
                raise ValueError(f"tasks[{idx}] cannot reference itself")
            if pi < 0 or pi >= n:
                raise ValueError(
                    f"tasks[{idx}].parents index {pi} out of range [0, {n})"
                )
            parent_indices.append(pi)
        children.append({
            "title": title,
            "body": body,
            "assignee": assignee,
            "parents": parent_indices,
        })

    # fanout=false with 1 task: that's a single-task spec, accept.
    # fanout=true with 1 task: degenerate, demote to fanout=false.
    if want_fanout and len(children) < MIN_CHILDREN:
        logger.info("kanban_decompose: fanout=true but only %d task(s); "
                    "treating as single-task spec", len(children))
        want_fanout = False
    elif want_fanout and len(children) > MAX_CHILDREN:
        logger.info("kanban_decompose: fanout=true with %d tasks; "
                    "truncating to %d", len(children), MAX_CHILDREN)
        children = children[:MAX_CHILDREN]

    return children, rationale, want_fanout


def _children_have_cycle(specs: list[dict]) -> bool:
    """Kahn's algorithm pre-check before touching the DB.

    The DB-level cycle check (kb.link_tasks → _has_cycle) would catch it,
    but we'd already have partial state. Front-loading the check keeps the
    whole decompose atomic.
    """
    n = len(specs)
    in_deg = [0] * n
    out_adj: dict[int, list[int]] = {}
    for child, spec in enumerate(specs):
        for parent in spec["parents"]:
            in_deg[child] += 1
            out_adj.setdefault(parent, []).append(child)
    queue = [i for i in range(n) if in_deg[i] == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for nb in out_adj.get(node, []):
            in_deg[nb] -= 1
            if in_deg[nb] == 0:
                queue.append(nb)
    return seen != n


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def _materialise_children(
    conn,
    root,
    specs: list[dict],
    model: str,
    author: str,
) -> list[str]:
    """Create the new task rows + link them. Single logical operation.

    Steps:
    1. Insert each child task in `todo` status, returning its new ID.
    2. Wire sibling parents from `parents` indices.
    3. Wire the root task as child of every LEAF (children with no
       outgoing sibling edges) so the dispatcher doesn't promote the root
       to `ready` until the full subgraph finishes.
    4. Drop a `decomposed_by` event on the root + a comment summarising
       the rationale (the model's own words).
    """
    n = len(specs)
    child_ids: list[str] = []

    # Pass 1: insert. We do this outside link_tasks because link_tasks
    # opens its own transaction; nesting BEGIN IMMEDIATE would fail.
    for spec in specs:
        cid = kb.create_task(
            conn,
            spec["title"],
            body=spec["body"],
            status=kb.STATUS_TODO,
            assignee=spec["assignee"],
        )
        child_ids.append(cid)

    # Pass 2: sibling links per spec.parents.
    has_outgoing = [False] * n
    for idx, spec in enumerate(specs):
        for parent_idx in spec["parents"]:
            kb.link_tasks(conn, child_ids[parent_idx], child_ids[idx])
            has_outgoing[parent_idx] = True

    # Pass 3: root depends on every leaf. A "leaf" here is a child that
    # nobody else depends on (has_outgoing[idx] == False).
    for idx, leaf in enumerate(has_outgoing):
        if not leaf:
            kb.link_tasks(conn, child_ids[idx], root.id)

    # Pass 4: audit trail on the root.
    kb.add_event(
        conn, root.id, kind="task.decomposed",
        payload={
            "author": author,
            "model": model,
            "children": child_ids,
            "child_count": len(child_ids),
        },
    )
    kb.add_comment(
        conn, root.id,
        f"Decomposed into {len(child_ids)} children by {model} "
        f"(author={author})",
        author=author,
    )
    return child_ids


def _handle_single_task_spec(
    conn,
    root,
    specs: list[dict],
    rationale: str,
    author: str,
) -> DecomposeOutcome:
    """Decomposer said `fanout=false`. Rewrite the parent in place.

    No children are created. We update the parent's title and body using the
    LLM's single-task suggestion, then flip status `triage → todo` if it was
    still in triage. This mirrors what `kanban_specify.specify_task` would
    have done — keeping behaviour consistent for the caller.
    """
    spec = specs[0]
    new_title = _coerce_title(spec.get("title"), root.title)
    new_body = _coerce_body(spec.get("body"), root.body)

    kb.update_task_metadata(conn, root.id, title=new_title, body=new_body)
    if root.status == kb.STATUS_TRIAGE:
        kb.update_status(
            conn, root.id, kb.STATUS_TODO,
            expected_status=kb.STATUS_TRIAGE,
        )
    kb.add_event(
        conn, root.id, kind="task.decomposed",
        payload={"author": author, "fanout": False, "rationale": rationale},
    )
    kb.add_comment(
        conn, root.id,
        f"Decomposer judged atomic; promoted in place (author={author})",
        author=author,
    )
    return DecomposeOutcome(
        task_id=root.id, ok=True, fanout=False,
        child_ids=[], rationale=rationale,
    )
