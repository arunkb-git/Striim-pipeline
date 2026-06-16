"""Models for Striim deployment settings"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional
import logging
from striim_deploy.utils.logger import get_logger


@dataclass
class SettingsModel:
    """
    Model for Striim deployment settings.

    This class holds configuration settings for deploying Striim applications,
    including validation rules, state transitions, and namespace mappings.

    Attributes:
        filename_mismatch: Strategy for handling filename mismatches ("error" or "warning")
        state_transition_timeout: Timeout in seconds for state transitions
        max_retries: Maximum number of retries for operations
        auto_start: Whether to automatically start applications after deployment
        auto_deploy: Whether to automatically deploy applications
        continue_on_error: Whether to continue deployment when errors occur
        validate_syntax: Whether to validate TQL syntax
        strip_namespace_prefix: Whether to strip namespace prefixes
        enforce_create_or_replace: Whether to enforce CREATE OR REPLACE syntax
        create_or_replace_strategy: Strategy for CREATE OR REPLACE ("auto" or "require")
        require_specific_directories: Whether to restrict files to specific directories
        allowed_directories: List of allowed directories for TQL files
        enforce_naming_convention: Whether to enforce naming conventions
        naming_pattern: Regex pattern for valid file names
    """

    filename_mismatch: str = "error"
    state_transition_timeout: int = 60
    max_retries: int = 3
    auto_start: bool = False
    auto_deploy: bool = True
    continue_on_error: bool = False
    validate_syntax: bool = True
    strip_namespace_prefix: bool = True
    enforce_create_or_replace: bool = True
    create_or_replace_strategy: str = "auto"

    # Validation settings
    require_specific_directories: bool = True
    allowed_directories: Optional[List[str]] = None
    enforce_naming_convention: bool = True
    naming_pattern: str = r"^[a-zA-Z][a-zA-Z0-9_-]*\.tql$"

    _settings_path: Optional[str] = None
    _full_settings: Optional[Dict[str, Any]] = None
    _instance = None
    _logger: Optional[logging.Logger] = None

    def __post_init__(self):
        """
        Initialize default values after instance creation.

        Sets default allowed directories if none were provided.
        """
        if self.allowed_directories is None:
            self.allowed_directories = ["striim/TQL"]

    @property
    def logger(self) -> logging.Logger:
        """
        Lazy loading for logger instance.

        Returns:
            logging.Logger: Configured logger instance
        """

        if self._logger is None:
            self._logger = get_logger(__name__)
        return self._logger

    def set_logger(self, logger: logging.Logger) -> None:
        """
        Set a configured logger instance.

        Args:
            logger: Configured logger instance
        """
        self._logger = logger

    def get_application_patterns(self) -> List[str]:
        """
        Get application patterns from settings.

        These patterns are used to extract application names from TQL files.

        Returns:
            List[str]: Regular expression patterns for matching application declarations
        """
        if self._full_settings:
            return self._full_settings.get(
                "application_patterns",
                [
                    r"CREATE\s+APPLICATION\s+([^\s;]+)",
                    r"CREATE\s+OR\s+REPLACE\s+APPLICATION\s+([^\s;]+)",
                ],
            )
        return [
            r"CREATE\s+APPLICATION\s+([^\s;]+)",
            r"CREATE\s+OR\s+REPLACE\s+APPLICATION\s+([^\s;]+)",
        ]

    def get_namespace_mapping(self) -> Dict[str, str]:
        """
        Get namespace mapping from settings.

        This mapping translates branch names to deployment namespaces.

        Returns:
            Dict[str, str]: Mapping of branch names to namespace identifiers
        """
        if self._full_settings:
            return self._full_settings.get("namespace_mapping", {})
        return {}

    def get_environment_mapping(self) -> Dict[str, str]:
        """
        Get branch-to-environment mapping from settings.

        This mapping translates branch names to GitHub deployment
        environments. The environment selects the scoped Striim credentials
        (STRIIM_BASE_URL/USERNAME/PASSWORD) and therefore the target server
        for the deployment.

        Returns:
            Dict[str, str]: Mapping of branch names to environment names
        """
        if self._full_settings:
            return self._full_settings.get("environment_mapping", {})
        return {}

    def get_state_transitions(
        self, app_name: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get state transition matrix from settings.

        This defines valid state transitions for Striim applications. When
        ``app_name`` is given, any per-application ``state_transitions`` block
        (under ``deployment.applications.<app>``) is merged over the global
        matrix, letting an application override e.g. a per-state teardown
        ``timeout`` without redefining the whole transition.

        Args:
            app_name: Optional application name to apply per-app overrides for

        Returns:
            Dict: State transition configuration
        """
        if not self._full_settings:
            return {}

        transitions = self._full_settings.get("state_transitions", {})

        if not app_name:
            return transitions

        app_transitions = self.get_deployment_config(app_name).get(
            "state_transitions"
        )
        if not app_transitions:
            return transitions

        # Shallow-merge each overridden state over the global definition so a
        # per-app block only needs to carry the keys it changes (e.g. timeout).
        merged = {state: dict(settings) for state, settings in transitions.items()}
        for state, overrides in app_transitions.items():
            merged.setdefault(state, {}).update(overrides or {})
        return merged

    def get_auto_start(self, app_name: Optional[str] = None) -> bool:
        """
        Resolve the ``auto_start`` flag, honouring per-application overrides.

        Args:
            app_name: Optional application name to check for an override

        Returns:
            bool: The app-level ``auto_start`` if set, else the global default
        """
        if app_name:
            app_config = self.get_deployment_config(app_name)
            if "auto_start" in app_config:
                return app_config["auto_start"]
        return self.auto_start

    def get_auto_deploy(self, app_name: Optional[str] = None) -> bool:
        """
        Resolve the ``auto_deploy`` flag, honouring per-application overrides.

        Args:
            app_name: Optional application name to check for an override

        Returns:
            bool: The app-level ``auto_deploy`` if set, else the global default
        """
        if app_name:
            app_config = self.get_deployment_config(app_name)
            if "auto_deploy" in app_config:
                return app_config["auto_deploy"]
        return self.auto_deploy

    def get_validation(self, key: Optional[str] = None, default: Any = None) -> Any:
        """
        Get validation settings.

        Args:
            key: Optional specific validation setting to retrieve
            default: Default value if key is not found

        Returns:
            Any: Either the full validation dictionary or a specific setting
        """
        if self._full_settings:
            validation = self._full_settings.get("validation", {})
            if key:
                return validation.get(key, default)
            return validation
        return {} if key is None else default

    def get_deployment_config(self, app_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get deployment configuration from settings.

        Args:
            app_name: Optional application name to get specific config

        Returns:
            Dictionary with deployment configuration
        """
        if not self._full_settings:
            return {}

        deployment = self._full_settings.get("deployment", {})

        if app_name:
            apps = deployment.get("applications", {})
            if app_name in apps:
                return apps[app_name]
            # Fall back to bare-name match so "admin.TestApplication" and "TestApplication"
            # both resolve correctly regardless of whether the caller passes a
            # FQN or a bare name.
            caller_bare = app_name.rpartition(".")[-1]
            for key, config in apps.items():
                if key.rpartition(".")[-1] == caller_bare:
                    return config
            return {}

        return deployment

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a top-level setting from the settings file.

        Args:
            key: Setting key to retrieve
            default: Default value if key is not found

        Returns:
            Any: Retrieved setting value or default
        """
        if self._full_settings:
            return self._full_settings.get(key, default)
        return default
