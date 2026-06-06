"""`assemble_specialist_from_skill`'s workflow docstring must name the tool the
agent actually has — `search_skillsmp` — not the underlying MCP tool name
`search_cloud_skills`.

Naming drift (#24): company / omctalent agents are LangChain-hosted and do NOT
get direct MCP access (that is the whole reason `search_skillsmp` exists as a
native wrapper). Telling them in step 1 to "Call search_cloud_skills (from your
fastskills MCP)" instructs them to call a tool they cannot see — they should be
pointed at `search_skillsmp`, the sibling tool in the same toolset."""
from __future__ import annotations


def _description(tool) -> str:
    return getattr(tool, "description", None) or (
        tool.func.__doc__ if hasattr(tool, "func") else tool.__doc__
    )


def test_workflow_points_at_the_available_wrapper_tool():
    from onemancompany.agents.common_tools import assemble_specialist_from_skill
    desc = _description(assemble_specialist_from_skill)
    assert "search_skillsmp" in desc, (
        "the workflow must name search_skillsmp — the tool LangChain-hosted "
        "agents actually have — not the MCP-only search_cloud_skills"
    )


def test_no_instruction_to_call_the_mcp_only_name():
    from onemancompany.agents.common_tools import assemble_specialist_from_skill
    desc = _description(assemble_specialist_from_skill)
    assert "Call search_cloud_skills" not in desc, (
        "must not instruct the agent to call the MCP-only name as its action"
    )
