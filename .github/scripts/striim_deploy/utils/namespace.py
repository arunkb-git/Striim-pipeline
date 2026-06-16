"""Namespace mapping for Striim deployments"""

import os
import logging
from typing import Dict, Optional, Tuple

from striim_deploy.settings.loader import get_settings
from striim_deploy.utils.logger import get_logger


def split_identifier(identifier: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Split a Striim identifier into (namespace, name).

    ``"admin.sbrTest"`` -> ``("admin", "sbrTest")``
    ``"sbrTest"``        -> ``(None, "sbrTest")``

    Args:
        identifier: Application/flow identifier, optionally namespace-qualified

    Returns:
        Tuple of (namespace or None, bare name or None)
    """
    if identifier and "." in identifier:
        namespace, _, name = identifier.rpartition(".")
        return namespace, name
    return None, identifier


def namespace_from_filename(file_path: str) -> Optional[str]:
    """
    Extract a namespace encoded in a ``<namespace>.<app>.tql`` filename.

    ``"striim/TQL/admin.sbrTest.tql"`` -> ``"admin"``
    ``"striim/TQL/sbrTest.tql"``       -> ``None``

    Args:
        file_path: Path to the TQL file

    Returns:
        The namespace portion of the filename, or None if not present
    """
    base = os.path.basename(file_path)
    if base.endswith(".tql"):
        base = base[: -len(".tql")]
    namespace, _ = split_identifier(base)
    return namespace


class NamespaceMapper:
    """Maps branch names to deployment namespaces"""

    def __init__(
        self,
        namespace_mapping: Optional[Dict[str, str]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the namespace mapper.

        Args:
            namespace_mapping: Explicit mapping of branch to namespace
            logger: Logger instance (optional)
        """
        self._logger = logger

        if namespace_mapping is not None:
            self.mapping = namespace_mapping
        else:
            try:
                self.mapping = get_settings().get_namespace_mapping()
            except (KeyError, FileNotFoundError) as e:
                self.logger.warning("Failed to load namespace mapping: %s", e)
                self.mapping = {}

    @property
    def logger(self):
        """Lazy loading for logger"""
        if self._logger is None:
            self._logger = get_logger(__name__)
        return self._logger

    def get_namespace(self, branch: str) -> Optional[str]:
        """
        Get namespace based on branch name.

        Args:
            branch: Git branch name

        Returns:
            Corresponding namespace, or None if the branch is not mapped.
            Returning None (rather than a placeholder) lets callers fall back
            to other namespace sources such as the TQL file or its filename.
        """
        return self.mapping.get(branch)
