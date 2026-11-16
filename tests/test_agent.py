"""End-to-end graph behaviour with the fake model (no network, no API keys)."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from dastyar.agent import graph as graph_mod
from dastyar.agent.graph import build_agent
from dastyar.agent.review import format_transcript, last_turn, run_review
from dastyar.fake_model import FakeChatModel

RM = "!sh rm -rf nonexistent_dir_xyz"
DANGEROUS_PY = "!py import subprocess; open('touched.txt','w').write('1')"


@pytest.fixture
def agent(ctx):
    return build_agent(ctx, None, InMemorySaver())


def cfg(tid="t1"):
    return {"configurable": {"thread_id": tid}, "recursion_limit": 100}


async def run(agent, inp, config):
    events = []
    async for ns, mode, data in agent.astream(
        inp, config, stream_mode=["messages", "updates", "custom"], subgraphs=True
    ):
        events.append((ns, mode, data))
    return events


def say(text):
    return {"messages": [HumanMessage(text)]}


def interrupts(events):
    return [
        i for _, mode, d in events if mode == "updates" and "__interrupt__" in d
        for i in d["__interrupt__"]
    ]


async def messages(agent, config):
    return (await agent.aget_state(config)).values["messages"]


async def test_plain_reply_and_dynamic_system_prompt(ctx, monkeypatch):
    ctx.memory.add("user", "پاسخ‌ها را فارسی بده")
    ctx.skills.create("demo-skill", "---\nname: demo-skill\ndescription: Demo thing\n---\n\nSteps\n")
    fake = FakeChatModel()
    monkeypatch.setattr(graph_mod, "build_chat_model", lambda *_a, **_k: fake)
    agent = build_agent(ctx, None, InMemorySaver())
    await run(agent, say("hello"), cfg())
    assert (await messages(agent, cfg()))[-1].text == "Echo: hello"
    system = fake.seen[0][0]
    assert isinstance(system, SystemMessage)
    text = system.text
    assert "Dastyar" in text and "پاسخ‌ها را فارسی بده" in text and "demo-skill: Demo thing" in text
    assert str(ctx.workspace) in text


async def test_tokens_are_streamed(agent):
    events = await run(agent, say("hello there friend"), cfg())
    tokens = [d[0].text for ns, m, d in events if m == "messages" and ns == () and d[0].text]
    assert "".join(tokens).strip() == "Echo: hello there friend" and len(tokens) > 1


async def test_safe_tool_runs_without_approval(agent):
    events = await run(agent, say("!sh echo hi-there"), cfg())
    assert not interrupts(events)
    msgs = await messages(agent, cfg())
    tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1 and "hi-there" in tool_msgs[0].content


async def test_dangerous_command_interrupts_then_reject_blocks_it(ctx, agent):
    events = await run(agent, say(DANGEROUS_PY), cfg())
    (intr,) = interrupts(events)
    req = intr.value["action_requests"][0]
    assert req["name"] == "run_python" and "subprocess" in req["args"]["code"]
    assert not (ctx.workspace / "touched.txt").exists()  # nothing ran before approval

    await run(
        agent,
        Command(resume={"decisions": [{"type": "reject", "message": "no thanks"}]}),
        cfg(),
    )
    assert not (ctx.workspace / "touched.txt").exists()
    msgs = await messages(agent, cfg())
    assert any(isinstance(m, ToolMessage) and "no thanks" in str(m.content) for m in msgs)
    assert isinstance(msgs[-1], AIMessage)  # the model got to react to the rejection


async def test_approve_runs_the_call(ctx, agent):
    await run(agent, say(DANGEROUS_PY), cfg())
    await run(agent, Command(resume={"decisions": [{"type": "approve"}]}), cfg())
    assert (ctx.workspace / "touched.txt").read_text() == "1"


async def test_edit_decision_replaces_the_call(ctx, agent):
    await run(agent, say(DANGEROUS_PY), cfg())
    edited = {
        "type": "edit",
        "edited_action": {"name": "run_python", "args": {"code": "open('edited.txt','w').write('2')"}},
    }
    await run(agent, Command(resume={"decisions": [edited]}), cfg())
    assert (ctx.workspace / "edited.txt").exists()
    assert not (ctx.workspace / "touched.txt").exists()


async def test_interrupt_survives_in_checkpoint_for_page_reload(agent):
    await run(agent, say(RM), cfg())
    state = await agent.aget_state(cfg())
    assert state.interrupts and state.interrupts[0].value["action_requests"][0]["name"] == "terminal"


async def test_approval_modes(ctx, agent):
    ctx.settings.approval_mode = "never"
    assert not interrupts(await run(agent, say(RM), cfg("never")))
    ctx.settings.approval_mode = "always"
    assert interrupts(await run(agent, say("!sh echo harmless"), cfg("always")))
    ctx.settings.approval_mode = "dangerous"
    assert not interrupts(await run(agent, say("!sh echo harmless"), cfg("danger-safe")))
    assert interrupts(await run(agent, say(RM), cfg("danger-bad")))


async def test_memory_and_skill_tools_via_agent(ctx, agent):
    await run(agent, say("!remember User lives in Tehran"), cfg("m"))
    assert ctx.memory.entries("memory") == ["User lives in Tehran"]
    await run(agent, say("!skill backup-notes"), cfg("s"))
    assert ctx.skills.names() == ["backup-notes"]


async def test_delegate_runs_helpers_in_parallel_and_streams_progress(agent):
    events = await run(agent, say("!delegate !sh echo alpha-out | !sh echo beta-out"), cfg("d"))
    msgs = await messages(agent, cfg("d"))
    result = next(m for m in msgs if isinstance(m, ToolMessage) and m.name == "delegate_task")
    assert "Helper 1" in result.content and "alpha-out" in result.content
    assert "Helper 2" in result.content and "beta-out" in result.content
    custom = [d for _, m, d in events if m == "custom"]
    assert {c["index"] for c in custom} == {0, 1}
    assert {c["event"] for c in custom} >= {"start", "tool", "done"}


async def test_helpers_cannot_run_risky_calls(ctx, agent):
    await run(agent, say("!delegate !py import subprocess; open('sub_touched.txt','w').write('1')"), cfg("g"))
    assert not (ctx.workspace / "sub_touched.txt").exists()
    msgs = await messages(agent, cfg("g"))
    result = next(m for m in msgs if isinstance(m, ToolMessage) and m.name == "delegate_task")
    assert "REFUSED" in result.content


async def test_helper_failure_does_not_crash_parent(ctx, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("no helper today")

    monkeypatch.setattr(graph_mod, "build_subagent", boom)
    agent = build_agent(ctx, None, InMemorySaver())
    await run(agent, say("!delegate !sh echo x"), cfg("f"))
    msgs = await messages(agent, cfg("f"))
    assert any("helper failed" in str(m.content) for m in msgs if isinstance(m, ToolMessage))


# ---------------------------------------------------------------- reviewer
def _memory_call(content):
    return AIMessage(
        content="",
        tool_calls=[{"name": "memory", "args": {"action": "add", "target": "user", "content": content}, "id": "r1"}],
    )


async def test_reviewer_saves_memory(ctx):
    transcript = [HumanMessage("I always want answers in Persian"), AIMessage("باشه")]
    model = FakeChatModel(script=[_memory_call("User wants Persian answers"), AIMessage("Saved a preference")])
    note = await run_review(ctx, model, transcript)
    assert note == "Saved: memory/user: add"
    assert ctx.memory.entries("user") == ["User wants Persian answers"]
    assert "I always want answers in Persian" in model.seen[0][-1].text  # transcript was provided


async def test_reviewer_nothing_to_save_and_lock_busy(ctx):
    msgs = [HumanMessage("what's 2+2"), AIMessage("4")]
    assert await run_review(ctx, FakeChatModel(script=[AIMessage("NOTHING")]), msgs) == ""
    ctx.review_lock.acquire()
    try:
        model = FakeChatModel(script=[_memory_call("x")])
        assert await run_review(ctx, model, msgs) == ""
        assert not model.seen and ctx.memory.entries("user") == []  # skipped entirely
    finally:
        ctx.review_lock.release()


async def test_reviewer_failure_is_contained(ctx):
    class Broken(FakeChatModel):
        def _generate(self, *a, **k):
            raise RuntimeError("api down")

    note = await run_review(ctx, Broken(), [HumanMessage("x")])
    assert note.startswith("(review failed")
    assert not ctx.review_lock.locked()  # lock released


def test_transcript_formatting_and_last_turn():
    msgs = [
        HumanMessage("old"), AIMessage("old reply"),
        HumanMessage("new question"),
        AIMessage("", tool_calls=[{"name": "terminal", "args": {"command": "ls"}, "id": "1"}]),
        ToolMessage("x" * 2000, tool_call_id="1", name="terminal"),
        AIMessage("final"),
    ]
    turn = last_turn(msgs)
    assert turn[0].text == "new question" and len(turn) == 4
    text = format_transcript(turn)
    assert "USER: new question" in text and "CALLS terminal" in text and "[...]" in text
    assert "old reply" not in text
    assert len(format_transcript(turn, limit=200)) < 400
