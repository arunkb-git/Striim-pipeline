"""Tests for the deployer module."""

import os
from unittest.mock import patch, MagicMock, call

from striim_deploy.core.deployer import StriimDeployer


def _make_validator(app_identifier="TestApp", tql="CREATE OR REPLACE APPLICATION TestApp;"):
    """Build a fully-stubbed validator for the happy path."""
    validator = MagicMock()
    validator.extract_app_identifier.return_value = app_identifier
    validator.validate_naming_convention.return_value = True
    validator.validate_filename.return_value = True
    validator.validate_syntax.return_value = True
    validator.extract_flows.return_value = []
    validator.preprocess_tql_content.return_value = tql
    validator.strip_namespace_in_statements.side_effect = lambda c: c
    return validator


class TestStriimDeployerInit:
    def test_initialization(self, mock_settings, mock_client):
        deployer = StriimDeployer(mock_settings, mock_client, namespace="test")

        assert deployer.settings == mock_settings
        assert deployer.client == mock_client
        assert deployer.namespace == "test"
        assert deployer.validator is not None
        assert deployer.state_manager is not None


class TestCreateApplication:
    """Test StriimDeployer.create_application."""

    def _deployer(self, mock_settings, mock_client, validator=None, state_manager=None):
        deployer = StriimDeployer(mock_settings, mock_client, namespace="test")
        deployer.validator = validator or _make_validator()
        deployer.state_manager = state_manager or MagicMock()
        return deployer

    def test_success(self, mock_settings, mock_client, sample_tql_file):
        """A clean create with auto_deploy disabled returns True."""
        mock_settings.get_auto_deploy.return_value = False
        validator = _make_validator()
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = None
        mock_client.request.return_value = True

        deployer = self._deployer(mock_settings, mock_client, validator, state_manager)
        result = deployer.create_application(sample_tql_file)

        assert result is True
        validator.extract_app_identifier.assert_called_once_with(sample_tql_file)
        validator.validate_filename.assert_called_once_with(sample_tql_file, "TestApp")
        state_manager.check_app_status.assert_called_once_with("TestApp")
        mock_client.request.assert_called_once()
        data = mock_client.request.call_args.kwargs["data"]
        assert "USE test;" in data

    def test_auto_deploy_disabled_still_returns_true(
        self, mock_settings, mock_client, sample_tql_file
    ):
        """Regression: a created app with auto_deploy off is a success, not None."""
        mock_settings.get_auto_deploy.return_value = False
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = None
        mock_client.request.return_value = True

        deployer = self._deployer(
            mock_settings, mock_client, state_manager=state_manager
        )
        result = deployer.create_application(sample_tql_file)

        assert result is True
        state_manager.deploy_application.assert_not_called()

    def test_auto_deploy_enabled(self, mock_settings, mock_client, sample_tql_file):
        """auto_deploy triggers deploy_application; auto_start off stops there."""
        mock_settings.get_auto_deploy.return_value = True
        mock_settings.get_auto_start.return_value = False
        mock_settings.get_deployment_config.return_value = {}
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = None
        state_manager.deploy_application.return_value = True
        mock_client.request.return_value = True

        deployer = self._deployer(
            mock_settings, mock_client, state_manager=state_manager
        )
        result = deployer.create_application(sample_tql_file)

        assert result is True
        state_manager.deploy_application.assert_called_once_with("TestApp")

    def test_extract_identifier_failure(
        self, mock_settings, mock_client, sample_tql_file
    ):
        """No application identifier -> failure, no API call."""
        validator = _make_validator()
        validator.extract_app_identifier.return_value = None

        deployer = self._deployer(mock_settings, mock_client, validator=validator)
        result = deployer.create_application(sample_tql_file)

        assert result is False
        validator.validate_filename.assert_not_called()
        mock_client.request.assert_not_called()

    def test_validate_filename_failure(
        self, mock_settings, mock_client, sample_tql_file
    ):
        """Filename mismatch in error mode aborts before deploying."""
        mock_settings.filename_mismatch = "error"
        validator = _make_validator()
        validator.validate_filename.return_value = False

        deployer = self._deployer(mock_settings, mock_client, validator=validator)
        result = deployer.create_application(sample_tql_file)

        assert result is False
        mock_client.request.assert_not_called()

    def test_filename_warning_continues(
        self, mock_settings, mock_client, sample_tql_file
    ):
        """Filename mismatch in warning mode proceeds."""
        mock_settings.filename_mismatch = "warning"
        mock_settings.get_auto_deploy.return_value = False
        validator = _make_validator()
        validator.validate_filename.return_value = False
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = None
        mock_client.request.return_value = True

        deployer = self._deployer(
            mock_settings, mock_client, validator=validator, state_manager=state_manager
        )
        result = deployer.create_application(sample_tql_file)

        assert result is True
        mock_client.request.assert_called_once()

    def test_naming_convention_failure(
        self, mock_settings, mock_client, sample_tql_file
    ):
        """A bad filename pattern aborts the deploy."""
        validator = _make_validator()
        validator.validate_naming_convention.return_value = False

        deployer = self._deployer(mock_settings, mock_client, validator=validator)
        result = deployer.create_application(sample_tql_file)

        assert result is False
        mock_client.request.assert_not_called()

    def test_existing_app_prepared(self, mock_settings, mock_client, sample_tql_file):
        """An existing app is torn down via prepare_for_deployment."""
        mock_settings.get_auto_deploy.return_value = False
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = "RUNNING"
        state_manager.prepare_for_deployment.return_value = True
        mock_client.request.return_value = True

        deployer = self._deployer(
            mock_settings, mock_client, state_manager=state_manager
        )
        result = deployer.create_application(sample_tql_file)

        assert result is True
        state_manager.prepare_for_deployment.assert_called_once_with("TestApp")

    def test_existing_app_prepare_failure(
        self, mock_settings, mock_client, sample_tql_file
    ):
        """A failed teardown aborts before deploying."""
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = "RUNNING"
        state_manager.prepare_for_deployment.return_value = False

        deployer = self._deployer(
            mock_settings, mock_client, state_manager=state_manager
        )
        result = deployer.create_application(sample_tql_file)

        assert result is False
        mock_client.request.assert_not_called()

    def test_syntax_failure(self, mock_settings, mock_client, sample_tql_file):
        """A syntax-validation failure aborts before deploying."""
        validator = _make_validator()
        validator.validate_syntax.return_value = False
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = None

        deployer = self._deployer(
            mock_settings, mock_client, validator=validator, state_manager=state_manager
        )
        result = deployer.create_application(sample_tql_file)

        assert result is False
        mock_client.request.assert_not_called()

    def test_preprocess_failure(self, mock_settings, mock_client, sample_tql_file):
        """Preprocessing returning None aborts the deploy."""
        validator = _make_validator()
        validator.preprocess_tql_content.return_value = None
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = None

        deployer = self._deployer(
            mock_settings, mock_client, validator=validator, state_manager=state_manager
        )
        result = deployer.create_application(sample_tql_file)

        assert result is False
        mock_client.request.assert_not_called()

    def test_api_failure(self, mock_settings, mock_client, sample_tql_file):
        """An error response from the API yields failure."""
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = None
        mock_client.request.return_value = {
            "status_code": 400,
            "error": True,
            "failure_message": "Invalid TQL",
        }

        deployer = self._deployer(
            mock_settings, mock_client, state_manager=state_manager
        )
        result = deployer.create_application(sample_tql_file)

        assert result is False
        mock_client.request.assert_called_once()

    def test_read_failure(self, mock_settings, mock_client):
        """A file read error yields failure."""
        validator = _make_validator()
        state_manager = MagicMock()
        state_manager.check_app_status.return_value = None

        deployer = self._deployer(
            mock_settings, mock_client, validator=validator, state_manager=state_manager
        )
        with patch("builtins.open", side_effect=IOError("read error")):
            result = deployer.create_application("nonexistent.tql")

        assert result is False
        mock_client.request.assert_not_called()


class TestCreateApplications:
    """Test StriimDeployer.create_applications (the batch driver)."""

    def test_single_success(self, mock_settings, mock_client, sample_tql_file):
        deployer = StriimDeployer(mock_settings, mock_client, namespace="test")

        with patch.object(deployer, "create_application", return_value=True) as m:
            with patch("os.path.exists", return_value=True):
                result = deployer.create_applications([sample_tql_file])

        assert result is True
        m.assert_called_once_with(sample_tql_file)

    def test_multiple_success(self, mock_settings, mock_client):
        files = ["file1.tql", "file2.tql", "file3.tql"]
        deployer = StriimDeployer(mock_settings, mock_client, namespace="test")
        deployer.validator = _make_validator()

        with patch.object(deployer, "create_application", return_value=True) as m:
            with patch("os.path.exists", return_value=True):
                result = deployer.create_applications(files)

        assert result is True
        assert m.call_count == 3

    def test_partial_failure(self, mock_settings, mock_client):
        files = ["file1.tql", "file2.tql", "file3.tql"]
        deployer = StriimDeployer(mock_settings, mock_client, namespace="test")
        deployer.validator = _make_validator()

        with patch.object(
            deployer, "create_application", side_effect=[True, False, True]
        ) as m:
            with patch("os.path.exists", return_value=True):
                mock_settings.continue_on_error = True
                result = deployer.create_applications(files)
                assert result is False
                assert m.call_count == 3

                m.reset_mock()
                m.side_effect = [True, False, True]
                mock_settings.continue_on_error = False
                result = deployer.create_applications(files)
                assert result is False
                assert m.call_count == 2

    def test_drop_option(self, mock_settings, mock_client):
        files = ["app1.tql", "app2.tql"]
        validator = _make_validator()
        validator.extract_app_identifier.side_effect = (
            lambda f: os.path.basename(f).replace(".tql", "")
        )
        state_manager = MagicMock()

        deployer = StriimDeployer(mock_settings, mock_client, namespace="test")
        deployer.validator = validator
        deployer.state_manager = state_manager

        with patch.object(deployer, "create_application", return_value=True) as m:
            with patch("os.path.exists", return_value=True):
                result = deployer.create_applications(files, drop_list=["app1"])

        assert result is True
        state_manager.drop_application.assert_called_once_with("app1")
        assert m.call_count == 2

    def test_force_drop_all(self, mock_settings, mock_client):
        files = ["app1.tql", "app2.tql"]
        validator = _make_validator()
        validator.extract_app_identifier.side_effect = (
            lambda f: os.path.basename(f).replace(".tql", "")
        )
        state_manager = MagicMock()

        deployer = StriimDeployer(mock_settings, mock_client, namespace="test")
        deployer.validator = validator
        deployer.state_manager = state_manager

        with patch.object(deployer, "create_application", return_value=True) as m:
            with patch("os.path.exists", return_value=True):
                result = deployer.create_applications(files, force_drop_all=True)

        assert result is True
        state_manager.drop_application.assert_has_calls(
            [call("app1"), call("app2")], any_order=True
        )
        assert m.call_count == 2

    def test_skip_deleted(self, mock_settings, mock_client):
        files = ["existing.tql", "deleted.tql"]
        deployer = StriimDeployer(mock_settings, mock_client, namespace="test")
        deployer.validator = _make_validator()

        with patch.object(deployer, "create_application", return_value=True) as m:
            with patch("os.path.exists", side_effect=[True, False]):
                result = deployer.create_applications(files)

        assert result is True
        m.assert_called_once_with("existing.tql")
