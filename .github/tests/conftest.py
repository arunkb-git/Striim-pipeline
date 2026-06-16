"""Shared test fixtures and configuration for Striim deploy tests."""

import os
import logging
from pathlib import Path
import sys
from unittest.mock import MagicMock
import pytest
import yaml

# Add the scripts directory to the Python path
repo_root = Path(__file__).parent.parent  # tests/conftest.py -> repo root (.github)
scripts_dir = repo_root / "scripts"
sys.path.insert(0, str(scripts_dir))

# Import the public API from the (refactored) striim_deploy package.
from striim_deploy import (
    SettingsModel,
    StriimClient,
    AppStateManager,
    StriimDeployer,
)


# Silence logs during tests
@pytest.fixture(autouse=True)
def disable_logging():
    """Disable logging output during tests."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture(autouse=True)
def reset_settings_singleton():
    """Reset the loader's cached settings instance between tests.

    ``load_settings`` caches a module-level singleton keyed by path; without a
    reset, a settings file written by one test would leak into the next.
    """
    from striim_deploy.settings import loader

    loader._instance = None
    yield
    loader._instance = None


@pytest.fixture
def test_dir():
    """Return the tests directory path."""
    return os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def data_dir(test_dir):
    """Return the test data directory path."""
    return os.path.join(test_dir, "data")


@pytest.fixture
def sample_tql_dir(data_dir):
    """Return the sample TQL files directory path."""
    return os.path.join(data_dir, "sample_tql_files")


@pytest.fixture
def test_config_path(data_dir):
    """Return the test config file path."""
    return os.path.join(data_dir, "test_settings.yml")


@pytest.fixture
def mock_settings():
    """Return a mock SettingsModel with sensible defaults."""
    settings = MagicMock(spec=SettingsModel)
    settings.filename_mismatch = "error"
    settings.state_transition_timeout = 30
    settings.max_retries = 2
    settings.auto_start = False
    settings.auto_deploy = True
    settings.continue_on_error = False
    settings.validate_syntax = True
    settings.strip_namespace_prefix = True
    settings.enforce_create_or_replace = True
    settings.create_or_replace_strategy = "auto"
    settings.require_specific_directories = True
    settings.allowed_directories = ["striim/TQL"]
    settings.enforce_naming_convention = True
    settings.naming_pattern = r"^[a-zA-Z][a-zA-Z0-9_-]*\.tql$"

    # Resolver helpers (per-application overrides)
    settings.get_auto_start.return_value = False
    settings.get_auto_deploy.return_value = True
    settings.get_deployment_config.return_value = {}

    settings.get_application_patterns.return_value = [
        r"CREATE\s+APPLICATION\s+([^\s;]+)",
        r"CREATE\s+OR\s+REPLACE\s+APPLICATION\s+([^\s;]+)",
    ]
    settings.get_namespace_mapping.return_value = {
        "main": "production",
        "dev": "development",
    }
    settings.get_state_transitions.return_value = {
        "RUNNING": {"action": "stop", "next_state": "STOPPED", "timeout": 30},
        "STOPPED": {"action": "undeploy", "next_state": "CREATED", "timeout": 30},
    }

    return settings


@pytest.fixture
def test_config_data():
    """Return test configuration data (valid settings YAML structure)."""
    return {
        "validation": {
            "filename_mismatch": "error",
            "require_specific_directories": True,
            "allowed_directories": ["striim/TQL"],
            "enforce_naming_convention": True,
            "naming_pattern": r"^[a-zA-Z][a-zA-Z0-9_-]*\.tql$",
            "state_transition_timeout": 30,
            "max_retries": 2,
            "auto_start": False,
            "auto_deploy": True,
            "continue_on_error": False,
            "validate_syntax": True,
            "enforce_create_or_replace": True,
            "create_or_replace_strategy": "auto",
        },
        "namespace_mapping": {
            "main": "production",
            "dev": "development",
            "feature": "test",
        },
        "environment_mapping": {
            "main": "prod",
            "dev": "dev",
        },
        "deployment": {
            "default_group": "default",
            "default_strategy": "one",
            "applications": {
                "OverrideApp": {
                    "auto_start": True,
                    "state_transitions": {"RUNNING": {"timeout": 999}},
                },
            },
        },
        "state_transitions": {
            "RUNNING": {"action": "stop", "next_state": "STOPPED", "timeout": 30},
            "STOPPED": {"action": "undeploy", "next_state": "CREATED", "timeout": 30},
        },
        "application_patterns": [
            r"CREATE\s+APPLICATION\s+([^\s;]+)",
            r"CREATE\s+OR\s+REPLACE\s+APPLICATION\s+([^\s;]+)",
        ],
    }


@pytest.fixture
def test_config_file(test_config_path, test_config_data):
    """Create a test config file on disk for the duration of a test."""
    os.makedirs(os.path.dirname(test_config_path), exist_ok=True)
    with open(test_config_path, "w") as f:
        yaml.dump(test_config_data, f)
    yield test_config_path
    if os.path.exists(test_config_path):
        os.remove(test_config_path)


@pytest.fixture
def mock_client():
    """Return a mock StriimClient."""
    client = MagicMock(spec=StriimClient)
    client.token = "test-token"
    client.authenticate.return_value = "test-token"
    client.ensure_authenticated.return_value = True
    return client


@pytest.fixture
def sample_tql_content():
    """Return sample TQL content."""
    return """
    CREATE APPLICATION TestApp;
    CREATE SOURCE TestSource USING FileReader (
    file: 'input.txt',
    format: 'csv'
    );
    CREATE TARGET TestTarget USING FileWriter (
    file: 'output.txt',
    format: 'csv'
    );
    CREATE CQ TestCQ
    INSERT INTO TestTarget
    SELECT * FROM TestSource;
    END APPLICATION TestApp;
    """


@pytest.fixture
def sample_tql_file(sample_tql_dir, sample_tql_content):
    """Create a sample TQL file."""
    os.makedirs(sample_tql_dir, exist_ok=True)
    file_path = os.path.join(sample_tql_dir, "TestApp.tql")
    with open(file_path, "w") as f:
        f.write(sample_tql_content)
    yield file_path
    if os.path.exists(file_path):
        os.remove(file_path)


@pytest.fixture
def mock_state_manager(mock_settings, mock_client):
    """Return a real AppStateManager wired to mocks."""
    return AppStateManager(mock_settings, mock_client, "test")


@pytest.fixture
def mock_deployer(mock_settings, mock_client):
    """Return a real StriimDeployer wired to mocks."""
    return StriimDeployer(mock_settings, mock_client, namespace="test")
