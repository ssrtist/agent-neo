"""Single source of truth for the Agent Neo (agent-neo / neo) version."""

APP_NAME = "Agent Neo"
CODE_NAME = "agent-neo"
ALIAS_NAME = "neo"
__version__ = "0.9.0"


def version_string() -> str:
    return f"{APP_NAME} ({CODE_NAME}) {__version__}"
