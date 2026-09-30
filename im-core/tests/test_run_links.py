from opensre_im_core.run_links import (
    VIEW_LINK_LABEL,
    build_agent_run_url,
    format_view_link_markdown,
)


def test_view_link_label_is_the_decided_string():
    # Teams PR #55 decided this exact label; it is user-visible in both platforms.
    assert VIEW_LINK_LABEL == "View in OpenSRE"


def test_builds_run_url_and_trims_base():
    assert (
        build_agent_run_url("https://opensre.example.com/", "run-1")
        == "https://opensre.example.com/team/agent-runs/run-1"
    )
    assert (
        build_agent_run_url("https://opensre.example.com", "run-1")
        == "https://opensre.example.com/team/agent-runs/run-1"
    )


def test_returns_none_when_either_input_missing():
    assert build_agent_run_url("", "run-1") is None
    assert build_agent_run_url("https://x", "") is None
    assert build_agent_run_url("   ", "   ") is None
    assert build_agent_run_url("https://x", None) is None
    assert build_agent_run_url(None, "run-1") is None


def test_formats_markdown_link():
    assert (
        format_view_link_markdown("https://x/team/agent-runs/r")
        == "[View in OpenSRE](https://x/team/agent-runs/r)"
    )
