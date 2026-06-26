"""DAGRenderer — visualização em tempo real do grafo de tarefas do orchestrator.

Produz duas representações:
- JSON: snapshot do grafo com status por nó (para /dag/<session> REST)
- Rich Tree: renderização terminal em tempo real (para bauer orch status --live)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DAGNode:
    id: int
    goal: str
    depends_on: List[int] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    result_preview: str = ""
    priority: int = 5

    def elapsed_s(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end = self.finished_at or time.time()
        return round(end - self.started_at, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal[:120],
            "depends_on": self.depends_on,
            "status": self.status.value,
            "priority": self.priority,
            "elapsed_s": self.elapsed_s(),
            "error": self.error,
            "result_preview": self.result_preview[:200],
        }


class DAGGraph:
    """Grafo de dependências de tarefas com rastreamento de status em tempo real."""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self.nodes: Dict[int, DAGNode] = {}
        self.created_at = time.time()

    def add_node(
        self,
        node_id: int,
        goal: str,
        depends_on: Optional[List[int]] = None,
        priority: int = 5,
    ) -> DAGNode:
        node = DAGNode(
            id=node_id,
            goal=goal,
            depends_on=depends_on or [],
            priority=priority,
        )
        self.nodes[node_id] = node
        return node

    def update_status(
        self,
        node_id: int,
        status: NodeStatus,
        error: Optional[str] = None,
        result_preview: str = "",
    ) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            return
        node.status = status
        if status == NodeStatus.RUNNING and node.started_at is None:
            node.started_at = time.time()
        if status in (NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.SKIPPED):
            node.finished_at = time.time()
        if error:
            node.error = error
        if result_preview:
            node.result_preview = result_preview

    def to_dict(self) -> Dict[str, Any]:
        """Snapshot JSON do grafo — adequado para /dag/<session>."""
        nodes = [n.to_dict() for n in sorted(self.nodes.values(), key=lambda x: x.id)]
        total = len(nodes)
        done = sum(1 for n in nodes if n["status"] == NodeStatus.DONE.value)
        failed = sum(1 for n in nodes if n["status"] == NodeStatus.FAILED.value)
        running = sum(1 for n in nodes if n["status"] == NodeStatus.RUNNING.value)
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "snapshot_at": time.time(),
            "summary": {
                "total": total,
                "done": done,
                "running": running,
                "failed": failed,
                "pending": total - done - failed - running,
            },
            "nodes": nodes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    # ------------------------------------------------------------------
    # Rich rendering
    # ------------------------------------------------------------------

    def to_rich_tree(self) -> "rich.tree.Tree":  # type: ignore[name-defined]  # noqa: F821
        """Gera Rich Tree para exibição no terminal."""
        try:
            from rich.tree import Tree
            from rich.text import Text
        except ImportError:
            raise RuntimeError("rich não instalado")

        _STATUS_ICON = {
            NodeStatus.PENDING:  "○",
            NodeStatus.RUNNING:  "◎",
            NodeStatus.DONE:     "●",
            NodeStatus.FAILED:   "✗",
            NodeStatus.SKIPPED:  "–",
        }
        _STATUS_STYLE = {
            NodeStatus.PENDING:  "dim",
            NodeStatus.RUNNING:  "bold yellow",
            NodeStatus.DONE:     "bold green",
            NodeStatus.FAILED:   "bold red",
            NodeStatus.SKIPPED:  "dim",
        }

        snap = self.to_dict()
        s = snap["summary"]
        header = (
            f"DAG [dim]{self.session_id}[/dim] — "
            f"[green]{s['done']}✓[/green] "
            f"[yellow]{s['running']}▶[/yellow] "
            f"[red]{s['failed']}✗[/red] "
            f"[dim]{s['pending']} pending[/dim]"
        )
        tree = Tree(header)

        # Build id→node lookup and children list
        node_map = {n["id"]: n for n in snap["nodes"]}
        children_of: Dict[int, List[int]] = {n["id"]: [] for n in snap["nodes"]}
        roots: List[int] = []
        for n in snap["nodes"]:
            if not n["depends_on"]:
                roots.append(n["id"])
            else:
                for dep in n["depends_on"]:
                    children_of.setdefault(dep, []).append(n["id"])

        def _add(tree_node, node_id: int, depth: int = 0) -> None:
            if depth > 20:
                return
            n = node_map[node_id]
            status = NodeStatus(n["status"])
            icon = _STATUS_ICON[status]
            style = _STATUS_STYLE[status]
            elapsed = f" [{n['elapsed_s']}s]" if n["elapsed_s"] is not None else ""
            prio = f" p{n['priority']}" if n["priority"] != 5 else ""
            label = f"[{style}]{icon} #{n['id']}{prio}[/{style}] {n['goal'][:60]}{elapsed}"
            if n.get("error"):
                label += f"\n  [red dim]{n['error'][:80]}[/red dim]"
            branch = tree_node.add(label)
            for child_id in children_of.get(node_id, []):
                _add(branch, child_id, depth + 1)

        for root_id in roots:
            _add(tree, root_id)

        return tree

    def render_text(self) -> str:
        """Fallback texto puro sem Rich."""
        snap = self.to_dict()
        lines = [f"DAG {self.session_id}"]
        for n in snap["nodes"]:
            icon = {"pending": "○", "running": "◎", "done": "●",
                    "failed": "✗", "skipped": "–"}.get(n["status"], "?")
            deps = f" (deps: {n['depends_on']})" if n["depends_on"] else ""
            elapsed = f" [{n['elapsed_s']}s]" if n["elapsed_s"] is not None else ""
            lines.append(f"  {icon} #{n['id']} {n['goal'][:60]}{deps}{elapsed}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Priority-aware ready-set helper
# ---------------------------------------------------------------------------

def ready_nodes(graph: DAGGraph) -> List[DAGNode]:
    """Retorna nós prontos para execução (todos deps DONE), ordenados por prioridade desc."""
    completed = {
        nid for nid, n in graph.nodes.items()
        if n.status == NodeStatus.DONE
    }
    ready = [
        n for n in graph.nodes.values()
        if n.status == NodeStatus.PENDING
        and all(dep in completed for dep in n.depends_on)
    ]
    return sorted(ready, key=lambda n: n.priority, reverse=True)
