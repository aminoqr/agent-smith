"""Tests for JSON config loading."""
from pathlib import Path

import pytest

from agent_smith.config import ConfigError, load_json_model
from agent_smith.models import SandboxConfig

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def test_template_matches_defaults():
    loaded = load_json_model(ROOT / "sandbox_template.json", SandboxConfig)
    assert loaded == SandboxConfig()


def test_wrong_type_names_the_field():
    with pytest.raises(ConfigError, match="max_memory_mb"):
        load_json_model(FIXTURES / "bad_sandbox.json", SandboxConfig)


def test_missing_file():
    with pytest.raises(ConfigError, match="Cannot read config"):
        load_json_model(FIXTURES / "nope.json", SandboxConfig)


def test_invalid_json(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"max_memory_mb": ')
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_json_model(broken, SandboxConfig)


def test_unknown_key_is_silently_ignored():
    config = SandboxConfig.model_validate_json('{"timeout": 5}')
    assert config.max_execution_time_seconds == 30
