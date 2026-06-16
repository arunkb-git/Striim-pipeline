"""Tests for the settings model and loader."""

from unittest.mock import patch, mock_open
import pytest

from striim_deploy.settings.models import SettingsModel
from striim_deploy.settings.loader import load_settings, get_settings


class TestSettingsModel:
    """Test the SettingsModel dataclass."""

    def test_default_initialization(self):
        """Test that default values are set when initializing."""
        config = SettingsModel()

        assert config.filename_mismatch == "error"
        assert config.state_transition_timeout == 60
        assert config.max_retries == 3
        assert config.auto_start is False
        assert config.auto_deploy is True
        assert config.continue_on_error is False
        assert config.validate_syntax is True
        assert config.strip_namespace_prefix is True
        assert config.enforce_create_or_replace is True
        assert config.create_or_replace_strategy == "auto"
        assert config.require_specific_directories is True
        assert config.allowed_directories == ["striim/TQL"]
        assert config.enforce_naming_convention is True
        assert config.naming_pattern == r"^[a-zA-Z][a-zA-Z0-9_-]*\.tql$"

    def test_custom_initialization(self):
        """Test initialization with custom values."""
        config = SettingsModel(
            filename_mismatch="warning",
            state_transition_timeout=30,
            max_retries=2,
            auto_start=True,
            auto_deploy=False,
            continue_on_error=True,
            validate_syntax=False,
            strip_namespace_prefix=False,
            enforce_create_or_replace=False,
            create_or_replace_strategy="require",
            require_specific_directories=False,
            allowed_directories=["custom/dir"],
            enforce_naming_convention=False,
            naming_pattern=r"^test_.*\.tql$",
        )

        assert config.filename_mismatch == "warning"
        assert config.state_transition_timeout == 30
        assert config.max_retries == 2
        assert config.auto_start is True
        assert config.auto_deploy is False
        assert config.continue_on_error is True
        assert config.validate_syntax is False
        assert config.strip_namespace_prefix is False
        assert config.enforce_create_or_replace is False
        assert config.create_or_replace_strategy == "require"
        assert config.require_specific_directories is False
        assert config.allowed_directories == ["custom/dir"]
        assert config.enforce_naming_convention is False
        assert config.naming_pattern == r"^test_.*\.tql$"


class TestSettingsLoader:
    """Test loading settings via the loader module."""

    def test_singleton_pattern(self, test_config_file):
        """get_settings returns the same cached instance for the same path."""
        config1 = get_settings(test_config_file)
        config2 = get_settings(test_config_file)

        assert config1 is config2

    def test_from_yaml(self, test_config_file, test_config_data):
        """Test loading configuration from YAML file."""
        config = load_settings(test_config_file)
        validation = test_config_data["validation"]

        assert config.filename_mismatch == validation["filename_mismatch"]
        assert config.state_transition_timeout == validation["state_transition_timeout"]
        assert config.max_retries == validation["max_retries"]
        assert config.auto_start == validation["auto_start"]
        assert config.auto_deploy == validation["auto_deploy"]
        assert config.continue_on_error == validation["continue_on_error"]
        assert config.validate_syntax == validation["validate_syntax"]
        assert config.enforce_create_or_replace == validation["enforce_create_or_replace"]
        assert config.create_or_replace_strategy == validation["create_or_replace_strategy"]
        assert config.require_specific_directories == validation["require_specific_directories"]
        assert config.allowed_directories == validation["allowed_directories"]
        assert config.enforce_naming_convention == validation["enforce_naming_convention"]
        assert config.naming_pattern == validation["naming_pattern"]

    def test_from_yaml_with_missing_file(self):
        """An explicit non-existent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_settings("non_existent_file.yml")

    @patch("builtins.open", new_callable=mock_open, read_data="invalid: yaml: :")
    def test_from_yaml_with_invalid_yaml(self, _mock_file):
        """Invalid YAML falls back to defaults."""
        with patch("os.path.exists", return_value=True):
            config = load_settings("test_file.yml")

        assert config.filename_mismatch == "error"
        assert config.state_transition_timeout == 60
        assert config.max_retries == 3

    def test_get_application_patterns(self, test_config_file, test_config_data):
        """Test getting application patterns."""
        config = load_settings(test_config_file)
        assert config.get_application_patterns() == test_config_data["application_patterns"]

    def test_get_namespace_mapping(self, test_config_file, test_config_data):
        """Test getting namespace mapping."""
        config = load_settings(test_config_file)
        assert config.get_namespace_mapping() == test_config_data["namespace_mapping"]

    def test_get_environment_mapping(self, test_config_file, test_config_data):
        """Test getting environment mapping."""
        config = load_settings(test_config_file)
        assert config.get_environment_mapping() == test_config_data["environment_mapping"]

    def test_get_state_transitions(self, test_config_file, test_config_data):
        """Test getting state transitions (global, no app override)."""
        config = load_settings(test_config_file)
        assert config.get_state_transitions() == test_config_data["state_transitions"]

    def test_get_validation(self, test_config_file, test_config_data):
        """Test getting validation settings."""
        config = load_settings(test_config_file)

        assert config.get_validation() == test_config_data["validation"]
        assert (
            config.get_validation("filename_mismatch")
            == test_config_data["validation"]["filename_mismatch"]
        )
        assert config.get_validation("non_existent", "default") == "default"

    def test_get_method(self, test_config_file, test_config_data):
        """Test the generic get method."""
        config = load_settings(test_config_file)

        assert config.get("namespace_mapping") == test_config_data["namespace_mapping"]
        assert config.get("non_existent", "default") == "default"

    @patch.dict("os.environ", {"STRIIM_SETTINGS_PATH": "env_settings.yml"})
    def test_from_yaml_with_env_variable(self):
        """Test loading configuration using the settings-path env variable."""
        with patch("os.path.exists", return_value=True):
            with patch(
                "builtins.open",
                mock_open(read_data="validation: {filename_mismatch: warning}"),
            ):
                config = load_settings()

        assert config._settings_path == "env_settings.yml"


class TestPerApplicationOverrides:
    """Test per-application resolution helpers."""

    def test_get_deployment_config(self, test_config_file):
        config = load_settings(test_config_file)
        app_config = config.get_deployment_config("OverrideApp")
        assert app_config["auto_start"] is True

    def test_get_auto_start_override(self, test_config_file):
        config = load_settings(test_config_file)
        # Global default is False; OverrideApp flips it to True.
        assert config.get_auto_start() is False
        assert config.get_auto_start("OverrideApp") is True

    def test_get_state_transitions_app_override(self, test_config_file):
        config = load_settings(test_config_file)
        # Global RUNNING timeout is 30; OverrideApp bumps only the timeout.
        global_tx = config.get_state_transitions()
        app_tx = config.get_state_transitions("OverrideApp")

        assert global_tx["RUNNING"]["timeout"] == 30
        assert app_tx["RUNNING"]["timeout"] == 999
        # Non-timeout keys are inherited from the global matrix.
        assert app_tx["RUNNING"]["action"] == "stop"
        # The global matrix is not mutated by the merge.
        assert global_tx["RUNNING"]["timeout"] == 30
