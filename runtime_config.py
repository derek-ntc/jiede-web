"""Shared write-lock selection without importing or initializing the application."""

import os
from pathlib import Path

from dotenv import dotenv_values


DEFAULT_WRITE_LOCK_PATH = "/tmp/jiede-web-write.lock"


def resolve_write_lock_path(project_root, *, explicit_lock_path=None, env_file=None):
    """Select explicit > .env (override=True) > process env > default.

    Parsing is side-effect free: other dotenv values are used only as needed for
    dotenv interpolation, never loaded into the caller's environment. Call this
    before the application's existing load_dotenv to share its initial inputs.
    A missing dotenv file is allowed; a present but unreadable file is not.

    Configured relative paths (.env, process env, or default) are project-root
    relative, including when env_file is elsewhere. An explicit CLI path is
    caller-CWD relative. Every result is absolute and resolves symlinks/"..".
    """
    value = explicit_lock_path
    if value is None:
        dotenv_path = Path(env_file) if env_file is not None else Path(project_root) / ".env"
        try:
            with dotenv_path.open(encoding="utf-8") as stream:
                values = dotenv_values(stream=stream)
        except FileNotFoundError:
            values = {}
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError("Unable to read JIEDE_WRITE_LOCK_PATH configuration file") from error
        # A dotenv key without '=' has value None and load_dotenv ignores it.
        value = values.get("JIEDE_WRITE_LOCK_PATH")
        if value is None:
            value = os.getenv("JIEDE_WRITE_LOCK_PATH", DEFAULT_WRITE_LOCK_PATH)
    value = os.fspath(value)
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("JIEDE_WRITE_LOCK_PATH must be a non-empty valid file path")
    path = Path(value)
    if not path.name or path.name in {".", ".."}:
        raise ValueError("JIEDE_WRITE_LOCK_PATH must name a lock file")
    if explicit_lock_path is None and not path.is_absolute():
        path = Path(project_root) / path
    return path.resolve()
