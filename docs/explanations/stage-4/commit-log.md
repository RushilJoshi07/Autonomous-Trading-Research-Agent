# Commit log — Stage 4

Lightweight notes after each commit — what changed, why, anything non-obvious.

---

## Stage 4 component 1: dependencies and MCP scaffolding

**Change:** Added `mcp` and `scipy` to `pyproject.toml` (scipy floored at
1.11, the version that introduced `false_discovery_control`, needed later
for the statistics tool). Added `src/mcp_tools/__init__.py` and
`server.py` — an empty MCP server object, no tools registered yet. Saved
the approved Stage 4 plan to `docs/plans/stage-4-plan.md`.

What is non-obvious: the plan assumed `mcp.server.fastmcp.FastMCP`; the
actually-installed `mcp==2.0.0` doesn't have that module at all. Found and
verified the real API (`mcp.server.MCPServer`, same decorator/run shape) by
inspecting the installed package and running a throwaway tool through it
before writing `server.py` — not by guessing. Also pre-verified, because a
later component's plan depends on it, that this SDK version still converts
an exception raised inside a tool into a structured error result
(`is_error=True`, snake_case in this version). Full trail:
`docs/explanations/stage-4/step-01-dependencies-and-mcp-scaffolding.md`.
Full 170-test suite confirmed unchanged.
