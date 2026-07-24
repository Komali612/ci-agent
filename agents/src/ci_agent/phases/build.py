from contracts import PhaseResult

from ci_agent import playbooks
from ci_agent.core import PhaseContext, run_commands_phase


def run(ctx: PhaseContext) -> PhaseResult:
    return run_commands_phase("build", playbooks.BUILD[ctx.classification.language], ctx)
