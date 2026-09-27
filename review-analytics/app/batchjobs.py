"""Message Batches for the summary steps (improvement items, themes, actionability review).

Batch requests cost half as much as synchronous calls and usually finish within an hour.
Routine updates use them (HANDOFF.md §2):

    python -m app.actions batch --from 2026-09-26 --to 2026-10-09 --chain
    python -m app.batchjobs collect      # run every ~15 minutes until nothing is pending
    python -m app.batchjobs status

With --chain, collecting the improvement items submits the themes batch, and collecting the
themes submits the actionability batch, so the whole update is one submit plus repeated collects.
(Per-review analysis keeps its own batch commands in app.analyze.)
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

from anthropic.types.messages.batch_create_params import Request

from .analyze import make_client
from .db import connect


def submit(conn, client, kind: str, meta: dict, requests: list[tuple[str, dict]]) -> str:
    """Create one batch; `requests` are (custom_id, Messages API params) pairs."""
    batch = client.messages.batches.create(requests=[Request(custom_id=cid, params=p) for cid, p in requests])
    with conn:
        conn.execute("INSERT INTO llm_jobs (batch_id, kind, meta_json, status, n_requests) VALUES (?,?,?,?,?)",
                     (batch.id, kind, json.dumps(meta, ensure_ascii=False), batch.processing_status, len(requests)))
    print(f"已提交批次 {batch.id}（{kind}，{len(requests)} 个请求）")
    return batch.id


def _results(client, batch_id: str) -> dict[str, str | None]:
    """custom_id -> response text, or None when the request errored, expired or was refused."""
    out: dict[str, str | None] = {}
    for res in client.messages.batches.results(batch_id):
        text = None
        if res.result.type == "succeeded":
            m = res.result.message
            if m.stop_reason not in ("refusal", "max_tokens"):
                text = next((b.text for b in m.content if b.type == "text"), "")
        out[res.custom_id] = text
    return out


def _handlers() -> dict[str, Callable]:
    from . import actionability, actions, themes
    return {"actions": actions.collect_batch, "themes": themes.collect_batch, "actionability": actionability.collect_batch}


def collect(conn, client) -> int:
    """Apply every finished batch; returns how many are still running."""
    handlers = _handlers()
    for job in conn.execute("SELECT * FROM llm_jobs WHERE status != 'collected' ORDER BY created_at").fetchall():
        batch = client.messages.batches.retrieve(job["batch_id"])
        c = batch.request_counts
        print(f"{job['batch_id']}（{job['kind']}）：{batch.processing_status}，成功 {c.succeeded}，失败 {c.errored}，处理中 {c.processing}")
        if batch.processing_status != "ended":
            continue
        results = _results(client, job["batch_id"])
        failed = [k for k, v in results.items() if v is None]
        if failed:
            print(f"  {len(failed)} 个请求没有结果：{', '.join(failed[:10])}")
        handlers[job["kind"]](conn, client, json.loads(job["meta_json"]), results)
        with conn:
            conn.execute("UPDATE llm_jobs SET status = 'collected' WHERE batch_id = ?", (job["batch_id"],))
    # counted afterwards: a chained step may have just submitted the next batch
    return conn.execute("SELECT count(*) FROM llm_jobs WHERE status != 'collected'").fetchone()[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["collect", "status"])
    args = ap.parse_args(argv)
    conn = connect()
    if args.command == "collect":
        n = collect(conn, make_client())
        print("全部完成" if n == 0 else f"还有 {n} 个批次在处理，稍后再收取")
    else:
        for j in conn.execute("SELECT batch_id, kind, status, n_requests, created_at FROM llm_jobs ORDER BY created_at"):
            print(dict(j))


if __name__ == "__main__":
    sys.exit(main())
