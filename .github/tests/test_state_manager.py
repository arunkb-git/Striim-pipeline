"""Tests for the application state manager."""

from unittest.mock import patch, MagicMock

from striim_deploy.state.manager import AppStateManager


class TestAppStateManager:
    """Test the AppStateManager class."""

    def test_initialization(self, mock_settings, mock_client):
        """Test state manager initialization."""
        manager = AppStateManager(mock_settings, mock_client, "test")

        assert manager.settings == mock_settings
        assert manager.client == mock_client
        assert manager.namespace == "test"
        for action in ("stop", "undeploy", "drop", "wait", "none"):
            assert action in manager.operations

    def test_check_app_status_found(self, mock_settings, mock_client):
        """Test checking application status when found."""
        mock_client.get.return_value = {"status": "RUNNING"}

        manager = AppStateManager(mock_settings, mock_client, "test")
        status = manager.check_app_status("TestApp")

        assert status == "RUNNING"
        mock_client.get.assert_called_once_with("applications/test.TestApp")

    def test_check_app_status_not_found(self, mock_settings, mock_client):
        """Test checking application status when not found."""
        mock_client.get.return_value = None

        manager = AppStateManager(mock_settings, mock_client, "test")
        assert manager.check_app_status("TestApp") is None

    def test_check_app_status_invalid_response(self, mock_settings, mock_client):
        """Test checking application status with invalid response."""
        mock_client.get.return_value = "invalid"

        manager = AppStateManager(mock_settings, mock_client, "test")
        assert manager.check_app_status("TestApp") is None

    @patch("time.time")
    @patch("time.sleep")
    def test_wait_for_state_success(
        self, mock_sleep, mock_time, mock_settings, mock_client
    ):
        """Test waiting for state with success."""
        mock_time.side_effect = [0, 2, 4, 6]
        mock_client.get.side_effect = [{"status": "RUNNING"}, {"status": "STOPPED"}]

        manager = AppStateManager(mock_settings, mock_client, "test")
        result = manager.wait_for_state("TestApp", "STOPPED", 10)

        assert result is True
        assert mock_client.get.call_count == 2
        assert mock_sleep.call_count == 1

    @patch("time.time")
    @patch("time.sleep")
    def test_wait_for_state_timeout(
        self, mock_sleep, mock_time, mock_settings, mock_client
    ):
        """Test waiting for state with timeout."""
        mock_time.side_effect = [0, 5, 10, 15]
        mock_client.get.return_value = {"status": "RUNNING"}

        manager = AppStateManager(mock_settings, mock_client, "test")
        result = manager.wait_for_state("TestApp", "STOPPED", 10)

        assert result is False
        assert mock_client.get.call_count >= 1

    def test_stop_application_success(self, mock_settings, mock_client):
        """Test stopping application with success."""
        mock_client.delete.return_value = True

        manager = AppStateManager(mock_settings, mock_client, "test")
        with patch.object(manager, "wait_for_state", return_value=True) as mock_wait:
            result = manager._stop_application("TestApp", "STOPPED", 30)

        assert result is True
        mock_client.delete.assert_called_once_with("applications/test.TestApp/sprint")
        mock_wait.assert_called_once_with("TestApp", "STOPPED", 30)

    def test_stop_application_failure(self, mock_settings, mock_client):
        """Test stopping application with failure."""
        mock_client.delete.return_value = False

        manager = AppStateManager(mock_settings, mock_client, "test")
        result = manager._stop_application("TestApp", "STOPPED", 30)

        assert result is False
        mock_client.delete.assert_called_once()

    def test_undeploy_application_success(self, mock_settings, mock_client):
        """Test undeploying application with success."""
        mock_client.get.return_value = None  # not yet CREATED
        mock_client.delete.return_value = True

        manager = AppStateManager(mock_settings, mock_client, "test")
        with patch.object(manager, "wait_for_state", return_value=True) as mock_wait:
            result = manager._undeploy_application("TestApp", "CREATED", 30)

        assert result is True
        mock_client.delete.assert_called_once_with(
            "applications/test.TestApp/deployment"
        )
        mock_wait.assert_called_once_with("TestApp", "CREATED", 30)

    def test_undeploy_application_failure(self, mock_settings, mock_client):
        """Test undeploying application with failure."""
        mock_client.get.return_value = None
        mock_client.delete.return_value = False

        manager = AppStateManager(mock_settings, mock_client, "test")
        result = manager._undeploy_application("TestApp", "CREATED", 30)

        assert result is False
        mock_client.delete.assert_called_once()

    def test_undeploy_application_invalid_response(self, mock_settings, mock_client):
        """Test undeploying application with an error response."""
        mock_client.get.return_value = None
        mock_client.delete.return_value = {"error": "Failed"}

        manager = AppStateManager(mock_settings, mock_client, "test")
        result = manager._undeploy_application("TestApp", "CREATED", 30)

        assert result is False
        mock_client.delete.assert_called_once()

    def test_undeploy_application_already_created(self, mock_settings, mock_client):
        """An app already in CREATED needs no undeploy."""
        mock_client.get.return_value = {"status": "CREATED"}

        manager = AppStateManager(mock_settings, mock_client, "test")
        result = manager._undeploy_application("TestApp", "CREATED", 30)

        assert result is True
        mock_client.delete.assert_not_called()

    def test_drop_application_success(self, mock_settings, mock_client):
        """Test dropping application with success."""
        mock_client.delete.return_value = True

        manager = AppStateManager(mock_settings, mock_client, "test")
        result = manager.drop_application("TestApp")

        assert result is True
        mock_client.delete.assert_called_once_with("applications/test.TestApp")

    def test_drop_application_failure(self, mock_settings, mock_client):
        """Test dropping application with failure."""
        mock_client.delete.return_value = False

        manager = AppStateManager(mock_settings, mock_client, "test")
        result = manager.drop_application("TestApp")

        assert result is False
        mock_client.delete.assert_called_once()


class TestPrepareForDeployment:
    """Test the iterative teardown walk in prepare_for_deployment."""

    def _manager(self, mock_settings, mock_client):
        return AppStateManager(mock_settings, mock_client, "test")

    def test_no_existing_app(self, mock_settings, mock_client):
        """No live application means nothing to tear down."""
        manager = self._manager(mock_settings, mock_client)
        with patch.object(manager, "check_app_status", return_value=None):
            assert manager.prepare_for_deployment("TestApp") is True

    def test_walks_running_to_created(self, mock_settings, mock_client):
        """RUNNING -> stop -> STOPPED -> undeploy -> CREATED."""
        mock_settings.get_state_transitions.return_value = {
            "RUNNING": {"action": "stop", "next_state": "STOPPED", "timeout": 30},
            "STOPPED": {"action": "undeploy", "next_state": "CREATED", "timeout": 30},
        }
        manager = self._manager(mock_settings, mock_client)

        stop_op = MagicMock(return_value=True)
        undeploy_op = MagicMock(return_value=True)
        manager.operations = {
            "stop": stop_op,
            "undeploy": undeploy_op,
            "none": lambda *args: True,
        }

        with patch.object(
            manager, "check_app_status", side_effect=["RUNNING", "STOPPED", "CREATED"]
        ):
            result = manager.prepare_for_deployment("TestApp")

        assert result is True
        stop_op.assert_called_once_with("TestApp", "STOPPED", 30)
        undeploy_op.assert_called_once_with("TestApp", "CREATED", 30)

    def test_uses_app_specific_transitions(self, mock_settings, mock_client):
        """prepare_for_deployment resolves transitions for the given app."""
        manager = self._manager(mock_settings, mock_client)
        with patch.object(manager, "check_app_status", return_value=None):
            manager.prepare_for_deployment("TestApp")
        mock_settings.get_state_transitions.assert_called_once_with("TestApp")

    def test_no_transition_defined(self, mock_settings, mock_client):
        """An unknown state with no transition proceeds (returns True)."""
        mock_settings.get_state_transitions.return_value = {}
        manager = self._manager(mock_settings, mock_client)

        with patch.object(manager, "check_app_status", return_value="UNKNOWN"):
            assert manager.prepare_for_deployment("TestApp") is True

    def test_invalid_action(self, mock_settings, mock_client):
        """An unrecognized action aborts the walk."""
        mock_settings.get_state_transitions.return_value = {
            "RUNNING": {"action": "invalid", "next_state": "STOPPED", "timeout": 30}
        }
        manager = self._manager(mock_settings, mock_client)

        with patch.object(manager, "check_app_status", return_value="RUNNING"):
            assert manager.prepare_for_deployment("TestApp") is False
