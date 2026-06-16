"""Settings loader for Striim deployment"""

import os
from typing import Optional, Dict, Any
import yaml

from striim_deploy.utils.logger import get_logger
from striim_deploy.settings.models import SettingsModel


logger = get_logger(__name__)

_instance = None


def load_settings(file_path: Optional[str] = None) -> SettingsModel:
    """
    Load settings from a YAML file.

    If no file path is provided, attempts to find settings file
    in common locations or from environment variables.

    Args:
        file_path: Path to YAML settings file

    Returns:
        SettingsModel: Instance with loaded settings

    Raises:
        FileNotFoundError: If a specific file path is provided but doesn't exist
    """
    global _instance

    # Return cached instance if already loaded
    if _instance is not None and (
        file_path is None or file_path == _instance._settings_path
    ):
        return _instance

    # Find the settings file if not explicitly provided
    if not file_path:
        file_path = _find_settings_file()

    # If a specific file path was provided but doesn't exist, raise FileNotFoundError
    if file_path and not os.path.exists(file_path):
        raise FileNotFoundError(f"Settings file not found: {file_path}")

    # If no file path was found, use defaults
    if not file_path:
        logger.warning("No settings file found. Using defaults.")
        _instance = _create_default_instance()
        return _instance

    try:
        # Load and parse the YAML file
        with open(file_path, "r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
            logger.info(f"Loaded settings from {file_path}")

        # Create instance from settings
        _instance = _create_instance_from_settings(settings)
        _instance._settings_path = file_path
        _instance._full_settings = settings

        return _instance
    except yaml.YAMLError as e:
        logger.warning(f"Could not parse YAML in {file_path}: {e}. Using defaults.")
        _instance = _create_default_instance()
        _instance._settings_path = file_path
        return _instance


def _find_settings_file() -> Optional[str]:
    """
    Find settings file in common locations.

    Returns:
        Optional[str]: Path to settings file if found, None otherwise
    """
    # First, try the environment variable
    file_path = os.environ.get("STRIIM_SETTINGS_PATH")
    if file_path:
        return file_path

    # Get the current script directory
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Try these paths in order
    potential_paths = [
        # Path relative to scripts/striim_deploy/settings
        os.path.join(current_dir, "../../..", "striim-deploy-settings.yml"),
        # Path for running from project root
        ".github/striim-deploy-settings.yml",
    ]

    for path in potential_paths:
        if os.path.exists(path):
            return path

    return None


def _create_default_instance() -> SettingsModel:
    """
    Create a default settings instance.

    Returns:
        SettingsModel: Instance with default settings
    """
    instance = SettingsModel()
    instance._settings_path = None
    instance._full_settings = {
        "validation": {
            "filename_mismatch": "error",
            "require_specific_directories": True,
            "allowed_directories": ["striim/TQL"],
            "enforce_naming_convention": True,
            "naming_pattern": "^[a-zA-Z][a-zA-Z0-9_-]*\\.tql$",
            "state_transition_timeout": 60,
            "max_retries": 3,
            "auto_deploy": True,
            "auto_start": False,
            "enforce_create_or_replace": True,
            "create_or_replace_strategy": "auto",
        }
    }
    return instance


def _create_instance_from_settings(settings: Dict[str, Any]) -> SettingsModel:
    """
    Create a settings instance from parsed settings.

    Args:
        settings: Parsed YAML settings

    Returns:
        SettingsModel: Settings instance
    """
    validation = settings.get("validation", {})

    return SettingsModel(
        filename_mismatch=validation.get("filename_mismatch", "error"),
        state_transition_timeout=validation.get("state_transition_timeout", 60),
        max_retries=validation.get("max_retries", 3),
        auto_deploy=validation.get("auto_deploy", True),
        auto_start=validation.get("auto_start", False),
        continue_on_error=validation.get("continue_on_error", False),
        validate_syntax=validation.get("validate_syntax", True),
        strip_namespace_prefix=validation.get("strip_namespace_prefix", True),
        enforce_create_or_replace=validation.get("enforce_create_or_replace", True),
        create_or_replace_strategy=validation.get("create_or_replace_strategy", "auto"),
        require_specific_directories=validation.get(
            "require_specific_directories", True
        ),
        allowed_directories=validation.get("allowed_directories", ["striim/TQL"]),
        enforce_naming_convention=validation.get("enforce_naming_convention", True),
        naming_pattern=validation.get(
            "naming_pattern", r"^[a-zA-Z][a-zA-Z0-9_-]*\.tql$"
        ),
    )


def get_settings(settings_path: Optional[str] = None) -> SettingsModel:
    """
    Get or create a singleton instance of SettingsModel.

    This implements the singleton pattern to ensure settings
    are loaded only once during application execution.

    Args:
        settings_path: Optional path to settings YAML file

    Returns:
        SettingsModel: Singleton instance
    """
    return load_settings(settings_path)
