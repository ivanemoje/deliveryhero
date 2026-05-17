"""
agent/graph.py — LangGraph agentic pipeline for legal contract processing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph

# ── Shared state type ──────────────────────────────────────────────────────────


class PipelineState(TypedDict):
    file_path: str
    object_key: Optional[str]  # noqa: UP007 - LangGraph resolves this on local Python 3.9.
    metadata: Optional[dict]  # noqa: UP007 - LangGraph resolves this on local Python 3.9.
    contract_id: Optional[str]  # noqa: UP007 - LangGraph resolves this on local Python 3.9.
    error: Optional[str]  # noqa: UP007 - LangGraph resolves this on local Python 3.9.
    retries: int
    status: Literal["running", "ok", "failed", "skipped", "retry"]


# ── Node implementations ───────────────────────────────────────────────────────


def node_check_duplicate(state: PipelineState) -> PipelineState:
    """Derive the idempotency key and check if already in DB."""
    from src.db import file_already_processed

    raw_path = state["file_path"]

    # Handle key derivation without mangling URIs with Path()
    if raw_path.startswith("gdoc://"):
        doc_id = raw_path[len("gdoc://") :]
        key = f"contracts/{doc_id}.gdoc"
    else:
        key = f"contracts/{Path(raw_path).name}"

    if file_already_processed(key):
        print(f"[SKIP] already processed: {key}")
        return {**state, "object_key": key, "status": "skipped"}

    return {**state, "object_key": key}


def node_ingest(state: PipelineState) -> PipelineState:
    """Upload to MinIO. Bypasses upload for Google Docs (API handled in extraction)."""
    from src.storage import upload_file

    path_str = state["file_path"]
    key = state["object_key"]

    # Remote files don't need 'ingestion' to MinIO in this architecture
    if path_str.startswith("gdoc://"):
        return state

    try:
        upload_file(Path(path_str), key)
        return state
    except Exception as e:
        return {**state, "error": f"ingest failed: {e}", "status": "failed"}


def node_extract(state: PipelineState) -> PipelineState:
    """Extract metadata using the format-agnostic extractor."""
    from dataclasses import asdict

    from src.extractor import extract_contract_metadata

    try:
        # extract_contract_metadata handles both local paths and gdoc://
        meta = extract_contract_metadata(state["file_path"])
        return {**state, "metadata": asdict(meta)}
    except Exception as e:
        return {**state, "error": f"extract failed: {e}", "status": "failed"}


def node_validate(state: PipelineState) -> PipelineState:
    """Check for required metadata fields."""
    meta = state.get("metadata") or {}
    required = (
        "client_name",
        "provider_name",
        "effective_date",
        "expiration_date",
        "total_contract_value",
        "currency",
        "force_majeure_notice_days",
        "non_renewal_notice_months",
    )
    missing = [f for f in required if not meta.get(f)]

    if not missing:
        return {**state, "status": "ok", "error": None}

    retries = state.get("retries", 0)
    max_retries = int(os.getenv("AGENT_MAX_RETRIES", "2"))

    if retries < max_retries:
        return {**state, "retries": retries + 1, "status": "retry"}

    return {
        **state,
        "status": "failed",
        "error": f"Validation failed: missing {missing}",
    }


def node_persist(state: PipelineState) -> PipelineState:
    """Save extracted data to PostgreSQL."""
    from dataclasses import fields

    from src.db import save_contract
    from src.extractor import ContractMetadata

    meta_dict = state["metadata"]
    valid_keys = {f.name for f in fields(ContractMetadata)}
    meta = ContractMetadata(**{k: v for k, v in meta_dict.items() if k in valid_keys})

    try:
        contract_id = save_contract(meta, state.get("object_key", ""))
        return {**state, "contract_id": contract_id}
    except Exception as e:
        return {**state, "error": f"persist failed: {e}", "status": "failed"}


def node_notify(state: PipelineState) -> PipelineState:
    """Final logging and threshold alerts."""
    meta = state.get("metadata") or {}
    fm_days = meta.get("force_majeure_notice_days")

    if state["status"] == "failed":
        print(f"[PIPELINE FAILED] {state['file_path']} — {state.get('error')}")
    elif state["status"] == "skipped":
        # Already logged in node_check_duplicate
        pass
    else:
        print(f"[PIPELINE OK] contract_id={state.get('contract_id')} file={state['file_path']}")
        if fm_days is not None and fm_days > 14:
            message = (
                "Force Majeure notice period exceeds 14-day termination trigger: "
                f"{fm_days} days for contract_id={state.get('contract_id')}"
            )
            print(f"  [ALERT] {message}")
            _send_slack_alert(message)

    return state


def _send_slack_alert(message: str) -> None:
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return

    try:
        import httpx

        httpx.post(webhook_url, json={"text": message}, timeout=5.0).raise_for_status()
    except Exception as e:
        print(f"[WARN] Slack alert failed: {e}")


# ── Routing & Assembly ─────────────────────────────────────────────────────────


def build_graph():
    g = StateGraph(PipelineState)

    g.add_node("check_duplicate", node_check_duplicate)
    g.add_node("ingest", node_ingest)
    g.add_node("extract", node_extract)
    g.add_node("validate", node_validate)
    g.add_node("persist", node_persist)
    g.add_node("notify", node_notify)

    g.set_entry_point("check_duplicate")

    g.add_conditional_edges(
        "check_duplicate",
        lambda s: "notify" if s["status"] == "skipped" else "ingest",
    )
    g.add_conditional_edges(
        "ingest",
        lambda s: "notify" if s["status"] == "failed" else "extract",
    )
    g.add_edge("extract", "validate")
    g.add_conditional_edges(
        "validate",
        lambda s: s["status"],  # maps to "ok", "retry", or "failed"
        {"ok": "persist", "retry": "extract", "failed": "notify"},
    )
    g.add_edge("persist", "notify")
    g.add_edge("notify", END)

    return g.compile()


pipeline_graph = build_graph()
