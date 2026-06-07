"""#130: asset-tool loading must accept BOTH packaging conventions and never
fail silently.

Talent-packaged tools (e.g. the aigraph Stage-3 tools) ship the equivalent
in-process langchain ``@tool`` under ``manifest.yaml`` + ``type: python`` +
``tool.py``; the canonical company convention is ``tool.yaml`` +
``type: langchain_module`` + ``<folder>.py``. The old loader required the
canonical convention and skipped the talent convention *silently*, so the
declared tool never registered and the employee's agent ran tool-less
(a "specialised talent as generic placeholder"). These tests pin the fix:
both conventions load, and a hire_list-declared tool that fails to register is
reported by the audit.
"""
from __future__ import annotations

from textwrap import dedent

from onemancompany.core.tool_registry import ToolRegistry

_TOOL_PY = dedent(
    '''
    from langchain_core.tools import tool

    @tool
    def {name}(x: str) -> str:
        """test asset tool"""
        return x
    '''
)


def _make_tool_dir(root, folder, *, conf_name, py_name, conf_body):
    d = root / folder
    d.mkdir(parents=True)
    (d / conf_name).write_text(conf_body, encoding="utf-8")
    (d / py_name).write_text(_TOOL_PY.format(name=folder), encoding="utf-8")
    return d


def test_talent_convention_loads(tmp_path):
    """manifest.yaml + type: python + tool.py (the aigraph convention)."""
    _make_tool_dir(
        tmp_path, "talent_tool",
        conf_name="manifest.yaml", py_name="tool.py",
        conf_body="name: talent_tool\ntype: python\ncommand: python tool.py\n",
    )
    reg = ToolRegistry()
    reg.load_asset_tools(tools_dir=tmp_path)
    assert reg.get_tool("talent_tool") is not None
    assert reg.get_meta("talent_tool").category == "asset"


def test_canonical_convention_still_loads(tmp_path):
    """tool.yaml + type: langchain_module + <folder>.py (back-compat)."""
    _make_tool_dir(
        tmp_path, "canon_tool",
        conf_name="tool.yaml", py_name="canon_tool.py",
        conf_body="name: canon_tool\ntype: langchain_module\n",
    )
    reg = ToolRegistry()
    reg.load_asset_tools(tools_dir=tmp_path)
    assert reg.get_tool("canon_tool") is not None


def test_unsupported_type_is_not_registered(tmp_path):
    """An unknown type must skip (loudly) — nothing registered."""
    _make_tool_dir(
        tmp_path, "weird_tool",
        conf_name="tool.yaml", py_name="weird_tool.py",
        conf_body="name: weird_tool\ntype: something_else\n",
    )
    reg = ToolRegistry()
    reg.load_asset_tools(tools_dir=tmp_path)
    assert reg.get_tool("weird_tool") is None


def test_talent_source_flag_scopes_tool(tmp_path):
    """source_talent in the manifest marks the tool as talent-sourced."""
    _make_tool_dir(
        tmp_path, "scoped_tool",
        conf_name="manifest.yaml", py_name="tool.py",
        conf_body="name: scoped_tool\ntype: python\nsource_talent: idea-generator\n",
    )
    reg = ToolRegistry()
    reg.load_asset_tools(tools_dir=tmp_path)
    assert reg.get_tool("scoped_tool") is not None
    assert reg.get_meta("scoped_tool").source == "talent"


def test_audit_flags_unresolved_declared_tools(tmp_path):
    """audit_declared_tools returns the declared tools that didn't register."""
    _make_tool_dir(
        tmp_path, "present_tool",
        conf_name="manifest.yaml", py_name="tool.py",
        conf_body="name: present_tool\ntype: python\n",
    )
    reg = ToolRegistry()
    reg.load_asset_tools(tools_dir=tmp_path)

    unresolved = reg.audit_declared_tools(
        {
            "idea-generator": ["present_tool", "ghost_tool"],
            "all-good": ["present_tool"],
        }
    )
    assert unresolved == {"idea-generator": ["ghost_tool"]}
