"""Validator for Striim TQL files"""

import re
import os
import logging
from typing import Optional

from striim_deploy.settings.models import SettingsModel
from striim_deploy.utils.logger import get_logger
from striim_deploy.utils.namespace import split_identifier


class TQLValidator:
    """
    Validator for Striim TQL files.

    Handles validation, preprocessing, and metadata extraction from TQL files
    based on the provided deployment settings.
    """

    def __init__(self, settings: SettingsModel, logger: logging.Logger = None):
        """
        Initialize the TQLValidator instance.

        Args:
            settings: Settings for TQL validation
            logger: Logger instance (optional)
        """
        self.settings = settings
        self._logger = logger

    @property
    def logger(self):
        """Lazy loading for logger"""
        if self._logger is None:
            self._logger = get_logger(__name__)
        return self._logger

    def extract_app_identifier(self, file_path: str) -> Optional[str]:
        """
        Extract the application identifier from a TQL file as written.

        The returned value is the raw name from the ``CREATE APPLICATION``
        statement, including any namespace prefix (e.g. ``"admin.sbrTest"``).
        Use :func:`striim_deploy.utils.namespace.split_identifier` to separate
        the namespace from the bare application name.

        Args:
            file_path: Path to the TQL file

        Returns:
            The application identifier as written, or None if not found
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Get application patterns from settings
            app_patterns = self.settings.get_application_patterns()

            # Try each pattern until a match is found
            for pattern in app_patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    return match.group(1)

            self.logger.error("Failed to extract application name from %s", file_path)
            return None

        except (FileNotFoundError, IOError) as e:
            self.logger.error("Error reading file %s: %s", file_path, e)
            return None
        except re.error as e:
            self.logger.error("Regex error when extracting app name: %s", e)
            return None

    def strip_namespace_in_statements(self, content: str) -> str:
        """
        Remove namespace prefixes from CREATE APPLICATION/FLOW statements.

        Used together with a prepended ``USE <namespace>;`` so the target
        namespace is declared in exactly one place, avoiding a mismatch between
        an inline ``namespace.app`` prefix and the active namespace.

        Args:
            content: TQL content

        Returns:
            Content with namespace prefixes stripped from object names
        """

        def _strip(match: re.Match) -> str:
            head, name = match.group(1), match.group(2)
            _, bare = split_identifier(name)
            return f"{head}{bare}"

        for keyword in ("APPLICATION", "FLOW"):
            pattern = rf"(CREATE\s+(?:OR\s+REPLACE\s+)?{keyword}\s+)([^\s;]+)"
            content = re.sub(pattern, _strip, content, flags=re.IGNORECASE)

        return content

    def validate_filename(self, file_path: str, app_name: str) -> bool:
        """
        Validate filename matches application name.

        Args:
            file_path: Path to the TQL file
            app_name: Application name to validate against

        Returns:
            True if validation passes, False otherwise
        """
        try:
            filename = os.path.basename(file_path)
            if filename.endswith(".tql"):
                filename = filename[: -len(".tql")]
            # A namespace prefix in the filename (admin.sbrTest.tql) is allowed;
            # compare on the bare name since app_name is also bare.
            _, filename = split_identifier(filename)

            if filename != app_name:
                mismatch_message = (
                    f"Filename '{filename}.tql' does not match app name '{app_name}'"
                )

                if self.settings.filename_mismatch == "error":
                    self.logger.error(mismatch_message)
                    return False
                else:
                    # Just log as a warning
                    self.logger.warning(mismatch_message)

            return True

        except OSError as e:
            self.logger.error("Error validating filename %s: %s", file_path, e)
            return False

    def preprocess_tql_content(self, content: str) -> Optional[str]:
        """
        Preprocess TQL content based on settings.

        Args:
            content: Original TQL content

        Returns:
            Preprocessed content or None if validation fails
        """
        try:
            if self.settings.enforce_create_or_replace:
                pattern = r"CREATE\s+APPLICATION\s+"
                replacement = "CREATE OR REPLACE APPLICATION "

                strategy = self.settings.create_or_replace_strategy

                if strategy == "auto":
                    # Check if it needs replacement (only has CREATE without OR REPLACE)
                    has_create = re.search(pattern, content, re.IGNORECASE) is not None
                    has_create_or_replace = (
                        re.search(
                            r"CREATE\s+OR\s+REPLACE\s+APPLICATION\s+",
                            content,
                            re.IGNORECASE,
                        )
                        is not None
                    )

                    if has_create and not has_create_or_replace:
                        # Automatically add OR REPLACE if missing
                        new_content = re.sub(
                            pattern,
                            replacement,
                            content,
                            flags=re.IGNORECASE,
                        )
                        if new_content != content:
                            self.logger.info(
                                "Added 'OR REPLACE' to CREATE APPLICATION statement"
                            )
                            content = new_content

                elif strategy == "require":
                    # Validate that CREATE OR REPLACE is used
                    if not re.search(
                        r"CREATE\s+OR\s+REPLACE\s+APPLICATION\s+",
                        content,
                        re.IGNORECASE,
                    ):
                        self.logger.error(
                            "TQL file must use CREATE OR REPLACE APPLICATION"
                        )
                        return None

            return content

        except ValueError as e:
            self.logger.error("Error preprocessing TQL content: %s", e)
            return None

    def extract_flows(self, file_path: str) -> list:
        """
        Extract flow names from a TQL file.

        Args:
            file_path: Path to TQL file

        Returns:
            List of flow names found in the file
        """
        flows = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Find all CREATE FLOW statements
            flow_pattern = r"CREATE\s+(?:OR\s+REPLACE\s+)?FLOW\s+([^\s;]+)"
            flows = re.findall(flow_pattern, content, re.IGNORECASE)

            # Always use the bare flow name; the namespace is applied separately
            # when building the fully-qualified name for the API.
            flows = [split_identifier(flow)[1] for flow in flows]

            return flows

        except (FileNotFoundError, IOError) as e:
            self.logger.error("Error reading file %s: %s", file_path, e)
            return []
        except re.error as e:
            self.logger.error("Regex error when extracting flows: %s", e)
            return []

    def validate_naming_convention(self, file_path: str) -> bool:
        """
        Validate the filename against the configured naming pattern.

        Enforced only when ``enforce_naming_convention`` is set. Directory
        allow-listing is handled earlier, during change detection
        (``detect_changes.filter_tql_files``), so it is intentionally not
        repeated here.

        Args:
            file_path: Path to TQL file

        Returns:
            True if the name is acceptable (or enforcement is off), else False
        """
        if not self.settings.enforce_naming_convention:
            return True

        filename = os.path.basename(file_path)
        # Strip namespace prefix (e.g. "admin.sbrTest.tql" -> "sbrTest.tql")
        # so the pattern only needs to describe the bare application name.
        if filename.endswith(".tql"):
            _, bare = split_identifier(filename[: -len(".tql")])
            filename = (bare or "") + ".tql"
        pattern = self.settings.naming_pattern or r"^[a-zA-Z][a-zA-Z0-9_-]*\.tql$"

        if not re.match(pattern, filename):
            self.logger.error(
                "Filename %s does not match required pattern: %s", filename, pattern
            )
            return False

        return True

    def validate_syntax(self, content: str) -> bool:
        """
        Lightweight structural validation of TQL content.

        Enforced only when ``validate_syntax`` is set. This is a sanity check,
        not a full TQL parser: it verifies the content is non-empty, contains at
        least one CREATE APPLICATION statement, and that every CREATE APPLICATION
        has a matching END APPLICATION.

        Args:
            content: TQL content

        Returns:
            True if the content passes the checks (or validation is off)
        """
        if not self.settings.validate_syntax:
            return True

        if not content or not content.strip():
            self.logger.error("TQL content is empty")
            return False

        create_count = len(
            re.findall(
                r"CREATE\s+(?:OR\s+REPLACE\s+)?APPLICATION\s+",
                content,
                re.IGNORECASE,
            )
        )
        end_count = len(re.findall(r"END\s+APPLICATION\b", content, re.IGNORECASE))

        if create_count == 0:
            self.logger.error("No CREATE APPLICATION statement found in TQL content")
            return False

        if create_count != end_count:
            self.logger.error(
                "Unbalanced application block: %d CREATE APPLICATION vs "
                "%d END APPLICATION",
                create_count,
                end_count,
            )
            return False

        return True
