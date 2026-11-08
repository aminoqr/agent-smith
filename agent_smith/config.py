"""Loading and validating JSON configuration files, plus `.env` for secrets."""
import os
from pathlib import Path
from typing import Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


class ConfigError(Exception):
    """A config file is missing, unreadable, or does not match its model."""


def load_json_model(path: str | Path, model_cls: Type[ModelT]) -> ModelT:
    """Read a JSON file and validate it against model_cls.

    Raises ConfigError with a one-line message instead of a traceback.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"Cannot read config {path}: {e.strerror}") from e
    try:
        return model_cls.model_validate_json(text)
    except ValidationError as e:
        problems = "; ".join(_describe(err) for err in e.errors())
        raise ConfigError(f"Invalid config {path}: {problems}") from e


def load_env_file(
    path: str | Path = ".env",
    env: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Load `KEY=value` lines into the environment without overriding it.

    A real environment variable always wins: the grader may export a key for
    the run, and a stale `.env` on the machine must not silently replace it.
    A missing file is fine, so this is safe to call unconditionally.

    Returns the names it set. Thirty lines instead of a dependency, and it is
    one less third-party package reading our secrets.
    """
    environment = os.environ if env is None else env
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []

    loaded: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in environment:
            continue
        environment[name] = value
        loaded.append(name)
    return loaded


def _describe(error: dict) -> str:
    """Format one Pydantic error as 'field.path: message'."""
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else error["msg"]
