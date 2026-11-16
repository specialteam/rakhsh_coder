"""Shell and Python execution tools."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from ..agent.safety import resolve_in_workspace
from ..context import AppContext
from .common import safe


def make_terminal_tools(ctx: AppContext) -> list[BaseTool]:
    shell = ctx.env.shell_name()

    @tool(parse_docstring=True)
    @safe
    def terminal(command: str, timeout: int = 120, workdir: str | None = None) -> str:
        """Run a shell command on the user's machine and return its combined output.

        Runs non-interactively (no stdin). Long outputs are truncated in the middle. The working
        directory defaults to the agent workspace. Use this for git, package managers, scripts,
        inspecting the system, etc.

        Args:
            command: The command line to run (in the machine's native shell, see the system prompt).
            timeout: Seconds before the command and its child processes are killed (default 120).
            workdir: Optional directory to run in (absolute, or relative to the workspace).
        """
        cwd = resolve_in_workspace(workdir, ctx.workspace) if workdir else None
        if cwd is not None and not cwd.is_dir():
            return f"ERROR: workdir {workdir!r} is not a directory"
        return ctx.env.run(command, timeout=max(1, min(int(timeout), 1800)), cwd=cwd).render()

    terminal.description = terminal.description.replace(
        "native shell", f"native shell: {shell}"
    )

    @tool(parse_docstring=True)
    @safe
    def run_python(code: str, timeout: int = 120) -> str:
        """Execute a Python script and return what it prints (stdout + stderr).

        The code runs in a fresh Python process with the workspace as working directory, so
        nothing persists between calls except files. Print the results you want to see.

        Args:
            code: Complete Python source code to run.
            timeout: Seconds before the process is killed (default 120).
        """
        return ctx.env.run_python(code, timeout=max(1, min(int(timeout), 1800))).render()

    return [terminal, run_python]
