"""The tool badge shows a human name, not the identifier.

``frontend/src/agent-tool-labels.mjs`` holds those names.  A tool added to the catalogue without a
label would silently go back to rendering "query stock prices", so this test reads the module and
compares its keys with the catalogue.
"""
from __future__ import annotations

import re
from pathlib import Path

MODULE = Path(__file__).resolve().parent.parent / "frontend" / "src" / "agent-tool-labels.mjs"


def labelled_tools() -> set[str]:
    source = MODULE.read_text(encoding="utf-8")
    block = source.split("export const TOOL_LABELS = {", 1)[1].split("};", 1)[0]
    return set(re.findall(r"^\s*([a-z][a-z0-9_]*):", block, re.MULTILINE))


def test_every_catalogue_tool_has_a_display_name():
    from app.agent_tool_catalog import definitions

    missing = sorted(set(definitions()) - labelled_tools())
    assert not missing, f"tools without a display name in agent-tool-labels.mjs: {missing}"


def test_no_display_name_is_left_behind_by_a_removed_tool():
    from app.agent_tool_catalog import definitions

    stale = sorted(labelled_tools() - set(definitions()))
    assert not stale, f"labels for tools that no longer exist: {stale}"
