"""Tests for the Striim API client."""

from unittest.mock import patch, MagicMock
import requests

from striim_deploy.api.client import StriimClient


class TestStriimClient:
    """Test the StriimClient class."""

    def test_initialization_without_credentials(self):
        """A client built without credentials does not authenticate."""
        client = StriimClient("https://striim.example.com")
        assert client.base_url == "https://striim.example.com"
        assert client.username is None
        assert client.password is None
        assert client.token is None

    @patch("striim_deploy.api.client.StriimClient.authenticate")
    def test_auto_authentication(self, mock_authenticate):
        """Authentication is triggered during init when credentials are given."""
        StriimClient("https://striim.example.com", "user", "pass")
        mock_authenticate.assert_called_once()

    @patch("requests.post")
    def test_authenticate_success(self, mock_post):
        """Test successful authentication."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"token": "test-token"}
        mock_post.return_value = mock_response

        client = StriimClient("https://striim.example.com")
        client.username, client.password = "user", "pass"
        token = client.authenticate()

        assert token == "test-token"
        assert client.token == "test-token"
        mock_post.assert_called_once_with(
            "https://striim.example.com/security/authenticate",
            data={"username": "user", "password": "pass"},
            timeout=30,
        )

    @patch("requests.post")
    def test_authenticate_failure(self, mock_post):
        """Test failed authentication paths."""
        client = StriimClient("https://striim.example.com")
        client.username, client.password = "user", "pass"

        # API returns error response
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_post.return_value = mock_response
        assert client.authenticate() is None
        assert client.token is None

        # API returns invalid JSON
        mock_response.ok = True
        mock_response.json.side_effect = ValueError("Invalid JSON")
        assert client.authenticate() is None

        # Request raises exception
        mock_post.side_effect = requests.RequestException("Connection error")
        assert client.authenticate() is None

    def test_ensure_authenticated(self):
        """Test authentication check."""
        # With existing token
        client = StriimClient("https://striim.example.com")
        client.token = "test-token"
        assert client.ensure_authenticated() is True

        # Without token, with credentials
        client = StriimClient("https://striim.example.com")
        client.username, client.password = "user", "pass"
        with patch.object(client, "authenticate", return_value="new-token"):
            assert client.ensure_authenticated() is True
            client.authenticate.assert_called_once()

        # Without token or credentials
        client = StriimClient("https://striim.example.com")
        assert client.ensure_authenticated() is False

    @patch("requests.Session.request")
    def test_request_with_auth(self, mock_request):
        """Test request with authentication."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.content = b'{"key": "value"}'
        mock_response.json.return_value = {"key": "value"}
        mock_request.return_value = mock_response

        client = StriimClient("https://striim.example.com")
        client.token = "test-token"
        result = client.request("get", "apps", params={"filter": "test"})

        assert result == {"key": "value"}
        mock_request.assert_called_once_with(
            "get",
            "https://striim.example.com/api/v2/apps",
            json=None,
            data=None,
            params={"filter": "test"},
            headers={"authorization": "STRIIM-TOKEN test-token"},
            timeout=30,
        )

    @patch("requests.Session.request")
    def test_request_no_auth(self, mock_request):
        """Test request without authentication."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.content = b""
        mock_request.return_value = mock_response

        client = StriimClient("https://striim.example.com")
        result = client.request("get", "health", auth_required=False)

        assert result is True
        mock_request.assert_called_once()

    @patch("requests.Session.request")
    def test_request_error(self, mock_request):
        """Test request with an error response."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 404
        mock_response.text = "Not found"
        mock_request.return_value = mock_response

        client = StriimClient("https://striim.example.com")
        client.token = "test-token"
        result = client.request("get", "apps/nonexistent")

        assert isinstance(result, dict)
        assert result.get("status_code") == 404
        assert result.get("error") is True

    @patch("requests.Session.request")
    def test_request_endpoint_formats(self, mock_request):
        """Test request with different endpoint formats."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.content = b""
        mock_request.return_value = mock_response

        client = StriimClient("https://striim.example.com")
        client.token = "test-token"
        headers = {"authorization": "STRIIM-TOKEN test-token"}

        client.request("get", "apps")
        mock_request.assert_called_with(
            "get",
            "https://striim.example.com/api/v2/apps",
            json=None,
            data=None,
            params=None,
            headers=headers,
            timeout=30,
        )

        client.request("get", "/custom/path")
        mock_request.assert_called_with(
            "get",
            "https://striim.example.com/custom/path",
            json=None,
            data=None,
            params=None,
            headers=headers,
            timeout=30,
        )

        client.request("get", "http://other.example.com/path")
        mock_request.assert_called_with(
            "get",
            "http://other.example.com/path",
            json=None,
            data=None,
            params=None,
            headers=headers,
            timeout=30,
        )

    @patch("requests.Session.request")
    def test_parse_error(self, mock_request):
        """Test error parsing for dict, list and non-JSON responses."""
        client = StriimClient("https://striim.example.com")
        client.token = "test-token"

        # JSON error response
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "executionStatus": "Failure",
            "failureMessage": "Invalid input",
        }
        mock_request.return_value = mock_response

        result = client.request("post", "apps")
        assert result["status_code"] == 400
        assert result["execution_status"] == "Failure"
        assert result["failure_message"] == "Invalid input"

        # List error response
        mock_response.json.return_value = [
            {
                "command": "CREATE APP Test",
                "executionStatus": "Failure",
                "failureMessage": "App exists",
                "responseCode": 400,
            }
        ]
        result = client.request("post", "tungsten", data="CREATE APP Test")
        assert "command_errors" in result
        assert len(result["command_errors"]) == 1
        assert result["command_errors"][0]["command"] == "CREATE APP Test"

        # Non-JSON error response
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "Error text"
        result = client.request("get", "apps")
        assert result["raw_text"] == "Error text"

    @patch.object(StriimClient, "request")
    def test_convenience_methods(self, mock_request):
        """Test convenience methods for HTTP verbs."""
        client = StriimClient("https://striim.example.com")
        client.token = "test-token"

        client.get("apps", params={"filter": "active"})
        mock_request.assert_called_with("get", "apps", params={"filter": "active"})

        client.post("apps", data={"name": "test"})
        mock_request.assert_called_with(
            "post", "apps", data={"name": "test"}, params=None
        )

        client.delete("apps/123")
        mock_request.assert_called_with("delete", "apps/123", params=None)

    def test_context_manager(self):
        """Test client as context manager."""
        with patch.object(StriimClient, "close") as mock_close:
            with StriimClient("https://striim.example.com") as client:
                assert isinstance(client, StriimClient)
            mock_close.assert_called_once()
