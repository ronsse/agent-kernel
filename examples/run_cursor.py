"""Example: Run Cursor CLI via the Agent Kernel runner adapter.

This does NOT require a full Tool Broker; it shows the low-level runner usage.
"""

from agent_kernel.runners.cursor_cli import CursorCliRunner
from agent_kernel.runners.types import RunnerRequest


def main() -> None:
    runner = CursorCliRunner()

    req = RunnerRequest(
        runner_id="cursor",
        workspace_path=".",
        prompt="Explain what this repository does in 5 bullet points.",
        mode="ask",
        output_format="stream-json",
        timeout_ms=180_000,
    )

    resp = runner.run(req)
    print("=== status ===")
    print(resp.status, resp.exit_code, resp.duration_ms, "ms")
    print("=== model ===")
    print(resp.model)
    print("=== thread_id ===")
    print(resp.thread_id)
    print("=== result_text ===")
    print(resp.result_text)


if __name__ == "__main__":
    main()
