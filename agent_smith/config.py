"""Loading and validating JSON configuration files."""
from pathlib import Path
from typing import Type, TypeVar

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


def _describe(error: dict) -> str:
    """Format one Pydantic error as 'field.path: message'."""
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else error["msg"]
