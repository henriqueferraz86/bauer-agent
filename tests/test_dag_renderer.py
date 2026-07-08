"""Testes do DAGRenderer: DAGGraph, NodeStatus, ready_nodes, Rich Tree."""

from __future__ import annotations

import time

import pytest

from bauer.dag_renderer import (
    DAGGraph,
    DAGNode,
    NodeStatus,
    ready_nodes,
)


# ---------------------------------------------------------------------------
# TestDAGNode
# ---------------------------------------------------------------------------

class TestDAGNode:
    def test_default_status_pending(self):
        node = DAGNode(id=1, goal="test")
        assert node.status == NodeStatus.PENDING

    def test_elapsed_none_when_not_started(self):
        node = DAGNode(id=1, goal="test")
        assert node.elapsed_s() is None

    def test_elapsed_increases_when_running(self):
        node = DAGNode(id=1, goal="test", started_at=time.time() - 1.0)
        assert node.elapsed_s() >= 1.0

    def test_elapsed_frozen_when_done(self):
        t0 = time.time() - 2.0
        node = DAGNode(id=1, goal="test", started_at=t0, finished_at=t0 + 1.5)
        assert abs(node.elapsed_s() - 1.5) < 0.01

    def test_to_dict_has_required_keys(self):
        node = DAGNode(id=1, goal="meu goal")
        d = node.to_dict()
        assert set(d.keys()) >= {"id", "goal", "depends_on", "status", "priority", "elapsed_s"}

    def test_to_dict_truncates_goal(self):
        long_goal = "x" * 200
        node = DAGNode(id=1, goal=long_goal)
        assert len(node.to_dict()["goal"]) <= 120

    def test_default_priority(self):
        node = DAGNode(id=1, goal="t")
        assert node.priority == 5


# ---------------------------------------------------------------------------
# TestDAGGraph
# ---------------------------------------------------------------------------

class TestDAGGraph:
    def test_add_node(self):
        g = DAGGraph(session_id="s1")
        n = g.add_node(1, "goal A")
        assert n.id == 1
        assert 1 in g.nodes

    def test_add_node_with_deps(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.add_node(2, "B", depends_on=[1])
        assert g.nodes[2].depends_on == [1]

    def test_update_status_done(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.DONE)
        assert g.nodes[1].status == NodeStatus.DONE

    def test_update_status_running_sets_started_at(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.RUNNING)
        assert g.nodes[1].started_at is not None

    def test_update_status_done_sets_finished_at(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.RUNNING)
        g.update_status(1, NodeStatus.DONE)
        assert g.nodes[1].finished_at is not None

    def test_update_status_error_stored(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.FAILED, error="boom")
        assert g.nodes[1].error == "boom"

    def test_update_status_result_preview(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.DONE, result_preview="hello")
        assert g.nodes[1].result_preview == "hello"

    def test_update_status_nonexistent_node(self):
        g = DAGGraph()
        g.update_status(99, NodeStatus.DONE)  # não deve lançar

    def test_to_dict_structure(self):
        g = DAGGraph(session_id="sess")
        g.add_node(1, "A")
        d = g.to_dict()
        assert d["session_id"] == "sess"
        assert "summary" in d
        assert "nodes" in d

    def test_to_dict_summary_counts(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.add_node(2, "B")
        g.add_node(3, "C")
        g.update_status(1, NodeStatus.DONE)
        g.update_status(2, NodeStatus.FAILED)
        d = g.to_dict()
        s = d["summary"]
        assert s["total"] == 3
        assert s["done"] == 1
        assert s["failed"] == 1
        assert s["pending"] == 1

    def test_to_json_valid_json(self):
        import json
        g = DAGGraph()
        g.add_node(1, "A")
        parsed = json.loads(g.to_json())
        assert "nodes" in parsed

    def test_nodes_sorted_by_id(self):
        g = DAGGraph()
        g.add_node(3, "C")
        g.add_node(1, "A")
        g.add_node(2, "B")
        ids = [n["id"] for n in g.to_dict()["nodes"]]
        assert ids == [1, 2, 3]


# ---------------------------------------------------------------------------
# TestReadyNodes
# ---------------------------------------------------------------------------

class TestReadyNodes:
    def test_single_node_no_deps_is_ready(self):
        g = DAGGraph()
        g.add_node(1, "A")
        r = ready_nodes(g)
        assert len(r) == 1 and r[0].id == 1

    def test_node_with_pending_dep_not_ready(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.add_node(2, "B", depends_on=[1])
        r = ready_nodes(g)
        assert all(n.id == 1 for n in r)

    def test_node_ready_when_dep_done(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.add_node(2, "B", depends_on=[1])
        g.update_status(1, NodeStatus.DONE)
        r = ready_nodes(g)
        ids = {n.id for n in r}
        assert 2 in ids

    def test_done_node_not_in_ready(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.DONE)
        r = ready_nodes(g)
        assert not any(n.id == 1 for n in r)

    def test_running_node_not_in_ready(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.RUNNING)
        r = ready_nodes(g)
        assert r == []

    def test_sorted_by_priority_desc(self):
        g = DAGGraph()
        g.add_node(1, "low", priority=2)
        g.add_node(2, "high", priority=9)
        g.add_node(3, "mid", priority=5)
        r = ready_nodes(g)
        assert r[0].id == 2  # priority 9
        assert r[-1].id == 1  # priority 2

    def test_multiple_parallel_nodes_all_ready(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.add_node(2, "B")
        g.add_node(3, "C")
        r = ready_nodes(g)
        assert len(r) == 3

    def test_chain_only_first_ready(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.add_node(2, "B", depends_on=[1])
        g.add_node(3, "C", depends_on=[2])
        r = ready_nodes(g)
        assert len(r) == 1 and r[0].id == 1


# ---------------------------------------------------------------------------
# TestRichTree
# ---------------------------------------------------------------------------

class TestRichTree:
    def test_to_rich_tree_returns_tree(self):
        pytest.importorskip("rich")
        from rich.tree import Tree
        g = DAGGraph(session_id="test")
        g.add_node(1, "A")
        t = g.to_rich_tree()
        assert isinstance(t, Tree)

    def test_to_rich_tree_with_hierarchy(self):
        pytest.importorskip("rich")
        g = DAGGraph()
        g.add_node(1, "Root")
        g.add_node(2, "Child", depends_on=[1])
        t = g.to_rich_tree()
        assert t is not None

    def test_render_text_fallback(self):
        g = DAGGraph(session_id="s1")
        g.add_node(1, "A")
        g.add_node(2, "B", depends_on=[1])
        g.update_status(1, NodeStatus.DONE)
        text = g.render_text()
        assert "DAG s1" in text
        assert "#1" in text
        assert "#2" in text

    def test_render_text_shows_status_icon(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.DONE)
        text = g.render_text()
        assert "●" in text

    def test_render_text_shows_failed_icon(self):
        g = DAGGraph()
        g.add_node(1, "A")
        g.update_status(1, NodeStatus.FAILED)
        text = g.render_text()
        assert "✗" in text


# ---------------------------------------------------------------------------
# TestNodeStatus
# ---------------------------------------------------------------------------

class TestNodeStatus:
    def test_all_values_accessible(self):
        for s in ("pending", "running", "done", "failed", "skipped"):
            assert NodeStatus(s) is not None

    def test_is_string(self):
        assert NodeStatus.DONE == "done"
