from lab_agent_safety.tracing import TraceWriter, merge_traces, read_trace


def test_write_and_read_back(tmp_path):
    path = tmp_path / "run" / "lab.jsonl"
    with TraceWriter(path, run_id="r1", source="lab") as t:
        t.write("command", op="aspirate", violations=[{"rule": "max_transfer_ul"}])
    events = read_trace(path)
    assert len(events) == 1
    e = events[0]
    assert e["run_id"] == "r1" and e["source"] == "lab" and e["event"] == "command"
    assert e["violations"][0]["rule"] == "max_transfer_ul"


def test_lines_are_flushed_before_close(tmp_path):
    path = tmp_path / "agent.jsonl"
    t = TraceWriter(path, run_id="r1", source="agent")
    t.write("tool_call", name="aspirate")
    assert len(read_trace(path)) == 1  # visible without close (crash safety)
    t.close()


def test_merge_orders_by_time(tmp_path):
    a, b = tmp_path / "agent.jsonl", tmp_path / "lab.jsonl"
    ta, tb = TraceWriter(a, "r1", "agent"), TraceWriter(b, "r1", "lab")
    ta.write("tool_call")
    tb.write("command")
    ta.write("tool_result")
    ta.close(), tb.close()
    assert [e["event"] for e in merge_traces(a, b)] == ["tool_call", "command", "tool_result"]


def test_missing_stream_reads_as_empty(tmp_path):
    assert read_trace(tmp_path / "nope.jsonl") == []
