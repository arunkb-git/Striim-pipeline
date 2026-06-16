"""Tests for the namespace utilities."""

from unittest.mock import patch, MagicMock

from striim_deploy.utils.namespace import (
    NamespaceMapper,
    split_identifier,
    namespace_from_filename,
)


class TestSplitIdentifier:
    """Test split_identifier."""

    def test_with_namespace(self):
        assert split_identifier("admin.sbrTest") == ("admin", "sbrTest")

    def test_without_namespace(self):
        assert split_identifier("sbrTest") == (None, "sbrTest")

    def test_none(self):
        assert split_identifier(None) == (None, None)


class TestNamespaceFromFilename:
    """Test namespace_from_filename."""

    def test_with_namespace(self):
        assert namespace_from_filename("striim/TQL/admin.sbrTest.tql") == "admin"

    def test_without_namespace(self):
        assert namespace_from_filename("striim/TQL/sbrTest.tql") is None


class TestNamespaceMapper:
    """Test the NamespaceMapper class."""

    def test_initialization_with_mapping(self):
        """Test initialization with explicit mapping."""
        mapping = {"main": "production", "dev": "development"}
        mapper = NamespaceMapper(mapping)

        assert mapper.mapping == mapping

    def test_initialization_from_settings(self):
        """Test initialization falls back to the loaded settings singleton."""
        expected_mapping = {"main": "production", "dev": "development"}
        settings = MagicMock()
        settings.get_namespace_mapping.return_value = expected_mapping

        with patch(
            "striim_deploy.utils.namespace.get_settings", return_value=settings
        ):
            mapper = NamespaceMapper()

        assert mapper.mapping == expected_mapping

    def test_initialization_error(self):
        """Test initialization tolerates a failure loading settings."""
        settings = MagicMock()
        settings.get_namespace_mapping.side_effect = KeyError("Missing key")

        with patch(
            "striim_deploy.utils.namespace.get_settings", return_value=settings
        ):
            mapper = NamespaceMapper()

        assert mapper.mapping == {}

    def test_get_namespace_found(self):
        """Test getting namespace for known branch."""
        mapping = {"main": "production", "dev": "development"}
        mapper = NamespaceMapper(mapping)

        assert mapper.get_namespace("main") == "production"
        assert mapper.get_namespace("dev") == "development"

    def test_get_namespace_not_found(self):
        """Unknown branches resolve to None so callers can fall back."""
        mapper = NamespaceMapper({"main": "production"})

        assert mapper.get_namespace("feature") is None

    def test_get_namespace_empty_mapping(self):
        """Test getting namespace with empty mapping."""
        mapper = NamespaceMapper({})

        assert mapper.get_namespace("any") is None
