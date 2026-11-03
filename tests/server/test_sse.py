# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The run event stream and the events file tail behind it."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from aa_recipe_manager.cli import _format_step_time
from aa_recipe_manager.server import tail

from .conftest import (
    HEADERS,
    run_events,
    run_session,
    start,
    wait_for,
    wait_for_event,
)

ENDED = {"completed", "failed", "cancelled", "interrupted"}

#: Recorded runs and the progress lines the client writes for them.
WEB_FIXTURES = Path(__file__).parents[2] / "web" / "test" / "fixtures"


def stream(client, rid: str, **kwargs) -> list[str]:
    url = f"/api/runs/{rid}/events"
    with client.stream("GET", url, **kwargs) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        return list(response.iter_lines())


def parse(lines: list[str]) -> list[tuple[int, dict]]:
    """The (id, event) pairs of a stream."""
    pairs = []
    current = None
    for line in lines:
        if line.startswith("id: "):
            current = int(line[4:])
        elif line.startswith("data: "):
            pairs.append((current, json.loads(line[6:])))
    return pairs


def test_finished_run_replays_and_closes(run_client, config, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    run = wait_for(run_client, start(run_client, sid)["id"], ENDED)

    pairs = parse(stream(run_client, run["id"]))
    ids = [seq for seq, _ in pairs]
    assert ids == list(range(1, len(pairs) + 1))
    assert all(seq == event["seq"] for seq, event in pairs)
    assert pairs[-1][1]["kind"] == "run_finished"
    assert [event for _, event in pairs] == run_events(config, run)

    after = parse(stream(run_client, run["id"], params={"after": 3}))
    assert after[0][0] == 4
    resumed = parse(stream(run_client, run["id"], headers={"Last-Event-ID": "3"}))
    assert resumed == after


def test_live_run_streams_until_it_finishes(run_client, config, tmp_path):
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache", {"seconds": 2})
    run = start(run_client, sid)
    assert run_client.get(f"/api/runs/{run['id']}").json()["status"] != "completed"

    kinds = [event["kind"] for _, event in parse(stream(run_client, run["id"]))]
    assert kinds[0] == "run_started"
    assert kinds.index("step_started") < kinds.index("run_finished")
    assert kinds[-1] == "run_finished"


def test_stream_stays_open_until_a_cancel_ends_it(
    run_app, run_client, config, tmp_path
):
    run_app.state.runs.config = replace(config, cancel_grace_seconds=1)
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache", {"seconds": 60})
    run = start(run_client, sid)
    wait_for_event(config, run, "step_started", "slow")

    received: list[str] = []
    reader = threading.Thread(
        target=lambda: received.extend(stream(run_client, run["id"]))
    )
    reader.start()
    reader.join(timeout=2)
    assert reader.is_alive()

    response = run_client.post(f"/api/runs/{run['id']}/cancel", headers=HEADERS)
    assert response.status_code == 200
    reader.join(timeout=30)
    assert not reader.is_alive()
    last = parse(received)[-1][1]
    assert last["kind"] == "run_finished"
    assert last["data"]["status"] == "cancelled"


def test_ended_run_with_nothing_new_is_204(run_client, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    run = wait_for(run_client, start(run_client, sid)["id"], ENDED)
    url = f"/api/runs/{run['id']}/events"
    last = run["last_seq"]

    assert run_client.get(url, params={"after": last}).status_code == 204
    response = run_client.get(url, headers={"Last-Event-ID": str(last)})
    assert response.status_code == 204
    tail_end = parse(stream(run_client, run["id"], params={"after": last - 1}))
    assert [seq for seq, _ in tail_end] == [last]


def test_unknown_run_stream_is_404(run_client):
    response = run_client.get("/api/runs/20260101-000000-abcdef/events")
    assert response.status_code == 404


def test_follow_waits_for_complete_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    first = {"seq": 1, "kind": "run_started", "data": {}}
    last = {"seq": 2, "kind": "run_finished", "data": {}}

    async def scenario():
        stopped = False

        async def stop():
            return stopped

        seen = []

        async def consume():
            async for event in tail.follow(path, interval=0.01, stop=stop):
                seen.append(event)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        assert seen == []
        text = json.dumps(first) + "\n" + json.dumps(last)
        path.write_text(text[:-5], encoding="utf-8")
        await asyncio.sleep(0.05)
        assert seen == [first]
        path.write_text(text + "\n", encoding="utf-8")
        await asyncio.wait_for(task, 2)
        return seen

    assert asyncio.run(scenario()) == [first, last]


def test_follow_sends_keepalives_and_stops(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"seq": 1, "kind": "log"}) + "\n", encoding="utf-8")
    calls = 0

    async def stop():
        nonlocal calls
        calls += 1
        return calls > 5

    async def collect():
        return [
            event
            async for event in tail.follow(
                path, after=0, interval=0.01, stop=stop, keepalive=0
            )
        ]

    events = asyncio.run(collect())
    assert events[0] == {"seq": 1, "kind": "log"}
    assert None in events[1:]


def test_log_entries_filter_and_traceback_fallback():
    events = [
        {"seq": 1, "time": 0.0, "kind": "run_started", "data": {}},
        {
            "seq": 2,
            "time": 1.0,
            "kind": "log",
            "data": {"step": "a", "stream": "stdout", "text": "hi\n", "instance": 3},
        },
        {
            "seq": 3,
            "time": 2.0,
            "kind": "step_failed",
            "data": {"step": "b", "error_type": "RuntimeError", "message": "boom"},
        },
        {
            "seq": 4,
            "time": 3.0,
            "kind": "step_failed",
            "data": {"step": "c", "traceback": "Traceback ...", "message": "x"},
        },
    ]
    entries = tail.log_entries(events, step=None, stream=None)
    every = [entry for entry in entries if entry["stream"] != "progress"]
    assert [entry["seq"] for entry in every] == [2, 3, 4]
    assert every[0]["instance"] == 3 and every[0]["buffered"] is False
    assert every[1]["text"] == "RuntimeError: boom"
    assert every[2]["text"] == "Traceback ..."
    progress = [(entry["seq"], entry["step"], entry["text"]) for entry in entries]
    assert progress[0] == (1, None, "run ... start (all)")
    assert (3, "b", "[?/?] b ... failed") in progress
    assert [entry["stream"] for entry in tail.log_entries(events, step="b", stream=None)] == [
        "progress",
        "traceback",
    ]
    assert tail.log_entries(events, step=None, stream="stdout") == [every[0]]


@pytest.mark.parametrize("name", ["flat", "mapped", "failing", "cached", "timed"])
def test_log_entries_progress_lines_match_the_client(name):
    """The client's reducer is held to the same file (web/test/progress.test.ts)."""
    expected = json.loads((WEB_FIXTURES / "progress.expected.json").read_text("utf-8"))
    text = (WEB_FIXTURES / f"{name}.events.jsonl").read_text("utf-8")
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    entries = tail.log_entries(events, step=None, stream="progress")
    assert [[entry["seq"], entry["step"], entry["text"]] for entry in entries] == expected[name]
    assert all(entry["instance"] is None and not entry["buffered"] for entry in entries)


def test_log_entries_keep_a_steps_progress_lines():
    text = (WEB_FIXTURES / "cached.events.jsonl").read_text("utf-8")
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    entries = tail.log_entries(events, step="dive_sv", stream=None)
    assert [entry["text"] for entry in entries] == [
        "[6/7] dive_sv ... start (3 instances)",
        "[6/7] dive_sv ... skipped: cached (3 instances: 3 skipped)",
    ]


@pytest.mark.parametrize(
    "elapsed, instance_seconds",
    [(1.5, ()), (1.5, (1.5,)), (6.0, (1.0, 2.0, 3.0)), (0.0371, ()), (12.3456, (4.0, 8.3456))],
)
def test_format_step_time_matches_the_cli(elapsed, instance_seconds):
    assert tail.format_step_time(elapsed, instance_seconds) == _format_step_time(
        elapsed, instance_seconds
    )


def test_read_events_skips_partial_and_older_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"seq": 1}\n{"seq": 2}\nnot json\n{"seq": 3}\n{"seq": 4', encoding="utf-8"
    )
    assert tail.read_events(path) == [{"seq": 1}, {"seq": 2}, {"seq": 3}]
    assert tail.read_events(path, after=2) == [{"seq": 3}]
    assert tail.read_events(tmp_path / "missing.jsonl") == []
