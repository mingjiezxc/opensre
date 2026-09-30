from opensre_im_core.models import InvestigationState, ThoughtSection, ToolCall
from opensre_im_core.progress_text import build_progress_text


def test_empty_state_shows_investigating():
    text = build_progress_text(InvestigationState(thread_id="t"))
    assert "Investigating" in text


def test_current_thought_is_last():
    state = InvestigationState(thread_id="t")
    state.thoughts.append(ThoughtSection(text="Older thought", completed=True))
    state.thoughts.append(ThoughtSection(text="Live thought", completed=False))
    text = build_progress_text(state)
    assert text.index("Older thought") < text.index("Live thought")


def test_nests_bash_under_skill_not_raw_command():
    state = InvestigationState(thread_id="t")
    th = ThoughtSection(text="Using k8s skill", completed=False)
    th.tools.append(
        ToolCall(
            name="Skill",
            input={"skill": "infrastructure-kubernetes"},
            running=False,
            success=True,
        )
    )
    th.tools.append(
        ToolCall(
            name="Bash",
            input={
                "command": (
                    "python /tmp/x/.claude/skills/infrastructure-kubernetes/"
                    "scripts/list_pods.py -n default"
                )
            },
            running=False,
            success=True,
        )
    )
    state.thoughts.append(th)
    text = build_progress_text(state)
    assert "infrastructure-kubernetes" in text
    assert (
        "list_pods.py" not in text or "↳" in text
    )  # nested as count, not top-level script dump
    assert "python /tmp" not in text
    assert "Bash `python" not in text


def test_plan_tools_collapsed_to_plan_line():
    state = InvestigationState(thread_id="t")
    th = ThoughtSection(text="Creating both tasks now.", completed=True)
    th.tools.append(
        ToolCall(
            name="TaskCreate",
            input={"subject": "Run kubectl get pods in the default namespace"},
            running=False,
            success=True,
        )
    )
    th.tools.append(
        ToolCall(
            name="TaskCreate",
            input={"subject": "Run kubectl get services in the default namespace"},
            running=False,
            success=True,
        )
    )
    state.thoughts.append(th)
    text = build_progress_text(state)
    assert "Plan:" in text
    assert "TaskCreate" not in text


def test_completed_thought_collapses_many_tools():
    state = InvestigationState(thread_id="t")
    th = ThoughtSection(text="Done exploring", completed=True)
    for i, name in enumerate(["Glob", "Read", "Grep"]):
        th.tools.append(
            ToolCall(
                name=name,
                input={"pattern": f"*.{i}", "file_path": f"/a/{i}.md"},
                running=False,
                success=True,
            )
        )
    state.thoughts.append(th)
    text = build_progress_text(state)
    assert "Used 3 tools" in text
    assert "Glob" not in text


def _done(name, **inp):
    return ToolCall(name=name, input=inp, running=False, success=True)


def test_progress_collapses_like_one_on_one():
    """Collapse old thoughts, nest Bash under Skill — same string for every platform."""
    older = ThoughtSection(text="I'll validate Kubernetes connectivity", completed=True)
    older.tools = [
        _done("Skill", skill="infrastructure-kubernetes", args="list namespaces"),
        _done("Bash", command="python .claude/skills/k8s/scripts/list_namespaces.py"),
    ]
    live = ThoughtSection(text="Checking cluster-info", completed=False)
    live.tools = [
        _done("Skill", skill="infrastructure-kubernetes", args="e2e"),
        *[
            _done("Bash", command=c)
            for c in (
                "python .claude/skills/k8s/scripts/list_namespaces.py",
                "kubectl auth can-i --list",
                "kubectl version --short",
                "kubectl cluster-info",
            )
        ],
    ]
    text = build_progress_text(
        InvestigationState(thread_id="t", thoughts=[older, live])
    )
    assert "list_namespaces.py" not in text
    assert "kubectl auth can-i" not in text
    assert "Used 1 tool" in text
    assert "↳ 4 commands" in text

    many = ThoughtSection(text="Still working", completed=False)
    many.tools = [_done("Read", file_path=f"/tmp/file{i}.md") for i in range(5)]
    clipped = build_progress_text(InvestigationState(thread_id="t", thoughts=[many]))
    assert "+2 more" in clipped
    assert "file0.md" not in clipped
    assert "file4.md" in clipped


def test_shows_background_waiting_label():
    state = InvestigationState(thread_id="t")
    state.thoughts.append(ThoughtSection(text="Delegating work", completed=False))
    state.background_waiting_label = "Waiting on 2 background agent(s)…"
    text = build_progress_text(state)
    assert "Waiting on 2 background agent(s)…" in text


def test_shows_background_notification():
    state = InvestigationState(thread_id="t")
    state.thoughts.append(ThoughtSection(text="Delegating work", completed=False))
    state.background_notification = "Background agent finished: Redis healthy"
    text = build_progress_text(state)
    assert "Background agent finished: Redis healthy" in text


def test_live_clutter_scenario_readable():
    """Regression shape from the Web Chat live test."""
    state = InvestigationState(thread_id="t")
    t1 = ThoughtSection(text="Creating both tasks now.", completed=True)
    t1.tools.append(
        ToolCall(
            name="TaskCreate", input={"subject": "pods"}, running=False, success=True
        )
    )
    t1.tools.append(
        ToolCall(
            name="TaskCreate",
            input={"subject": "services"},
            running=False,
            success=True,
        )
    )
    t2 = ThoughtSection(
        text="Delegating to the Kubernetes subagent now.", completed=False
    )
    t2.tools.append(
        ToolCall(
            name="Skill",
            input={"skill": "infrastructure-kubernetes", "args": "list resources"},
            running=False,
            success=True,
        )
    )
    t2.tools.append(
        ToolCall(
            name="Bash",
            input={
                "command": (
                    "python3 << 'EOF'\nfrom kubernetes import client\n"
                    "print('long')\nEOF"
                )
            },
            running=False,
            success=False,
        )
    )
    t2.tools.append(
        ToolCall(
            name="Agent",
            input={"subagent_type": "kubernetes", "description": "Check pods"},
            running=True,
        )
    )
    state.thoughts.extend([t1, t2])
    text = build_progress_text(state)
    assert "Creating both tasks now." in text
    assert "Delegating to the Kubernetes subagent now." in text
    assert text.rfind("Delegating") > text.rfind("Creating both")
    assert "TaskCreate" not in text
    assert "from kubernetes import client" not in text
    assert "infrastructure-kubernetes" in text
    assert "kubernetes: Check pods" in text


def test_progress_includes_run_link_when_url_given():
    state = InvestigationState(thread_id="t")
    state.thoughts.append(ThoughtSection(text="Checking pods", completed=False))
    text = build_progress_text(
        state, run_url="https://opensre.example.com/team/agent-runs/run-abc"
    )
    assert (
        "[View in OpenSRE](https://opensre.example.com/team/agent-runs/run-abc)" in text
    )


def test_progress_has_no_run_link_without_url():
    state = InvestigationState(thread_id="t")
    state.thoughts.append(ThoughtSection(text="Checking pods", completed=False))
    assert "View in OpenSRE" not in build_progress_text(state)
    assert "View in OpenSRE" not in build_progress_text(state, run_url=None)


def test_progress_link_is_last_line():
    state = InvestigationState(thread_id="t")
    state.thoughts.append(ThoughtSection(text="Checking pods", completed=False))
    state.background_waiting_label = "Waiting on 1 background agent(s)…"
    text = build_progress_text(state, run_url="https://x/team/agent-runs/r")
    assert text.rstrip().endswith("[View in OpenSRE](https://x/team/agent-runs/r)")


def test_progress_link_present_with_no_thoughts_yet():
    # run_started fires before the first thought — the link must still render.
    text = build_progress_text(
        InvestigationState(thread_id="t"), run_url="https://x/team/agent-runs/r"
    )
    assert "Investigating" in text
    assert "[View in OpenSRE](https://x/team/agent-runs/r)" in text
