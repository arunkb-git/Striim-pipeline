"""Unified API client for Striim operations with built-in authentication"""

import logging
from typing import Optional, Dict, Any, Union
import requests

from striim_deploy.utils.error_handler import parse_api_error


class StriimClient:
    """
    Unified API client for Striim operations.

    Handles authentication and API requests with consistent error handling.
    """

    def __init__(
        self,
        base_url: str,
        username: str = None,
        password: str = None,
        logger: logging.Logger = None,
    ):
        """
        Initialize the API client.

        Args:
            base_url: Base URL for the Striim API
            username: Username for authentication
            password: Password for authentication
            logger: Logger instance
        """
        self.base_url = base_url
        self.username = username
        self.password = password
        self.token = None
        self._logger = logger
        self.session = requests.Session()

        # Authenticate immediately if credentials are provided
        if username and password:
            self.authenticate()

    @property
    def logger(self):
        """Lazy loading for logger"""
        if self._logger is None:
            self._logger = logging.getLogger(__name__)
        return self._logger

    def authenticate(self) -> Optional[str]:
        """
        Get authentication token from Striim API.

        Returns:
            Authentication token if successful, None otherwise
        """
        if not self.username or not self.password:
            self.logger.error("Username and password required for authentication")
            return None

        # Use the direct API path for authentication
        url = f"{self.base_url}/security/authenticate"

        try:
            response = requests.post(
                url,
                data={"username": self.username, "password": self.password},
                timeout=30,
            )

            if response.ok:
                try:
                    token = response.json().get("token")
                    if token:
                        self.token = token
                        return token
                except ValueError as e:
                    self.logger.error("Failed to parse authentication response: %s", e)

            self.logger.error(
                "Authentication failed: %s - %s", response.status_code, response.text
            )
            return None

        except requests.RequestException as e:
            self.logger.error("Authentication request failed: %s", e)
            return None

    def ensure_authenticated(self) -> bool:
        """
        Ensure the client is authenticated before making requests.

        Returns:
            True if authenticated, False otherwise
        """
        if not self.token and self.username and self.password:
            return self.authenticate() is not None
        return bool(self.token)

    def request(
        self,
        method: str,
        endpoint: str,
        data: Any = None,
        params: Dict = None,
        headers: Dict = None,
        timeout: int = 30,
        auth_required: bool = True,
    ) -> Union[Dict, bool, None]:
        """
        Make an API request with consistent error handling.

        Args:
            method: HTTP method (get, post, put, delete)
            endpoint: API endpoint
            data: Request data
            params: Query parameters
            headers: Additional headers
            timeout: Request timeout in seconds
            auth_required: Whether authentication is required

        Returns:
            Response data if successful, None otherwise
        """
        if auth_required and not self.ensure_authenticated():
            self.logger.error("Authentication required but not available")
            return None

        if not headers:
            headers = {}

        if self.token:
            headers["authorization"] = f"STRIIM-TOKEN {self.token}"

        # Determine if this is an API v2 endpoint or direct endpoint
        if endpoint.startswith("/"):
            url = f"{self.base_url}{endpoint}"
        elif endpoint.startswith("http"):
            url = endpoint
        else:
            url = f"{self.base_url}/api/v2/{endpoint}"

        try:
            response = self.session.request(
                method,
                url,
                json=data if isinstance(data, (dict, list)) else None,
                data=data if not isinstance(data, (dict, list)) else None,
                params=params,
                headers=headers,
                timeout=timeout,
            )

            if response.ok:
                try:
                    if response.content:
                        return response.json()
                    return True
                except ValueError:
                    return response.text if response.text else True

            # Special handling for 404 on application not found
            if (
                response.status_code == 404
                and "applications/" in url
                and "Application not found" in response.text
            ):
                # Log as info instead of error
                self.logger.info(
                    "ℹ️ Application not found: %s - creating new application",
                    url.split("applications/")[1],
                )
                return None

            # Handle other errors
            self.logger.error(
                "API request failed: %s - %s - %s",
                response.status_code,
                url,
                response.text,
            )
            return parse_api_error(response)

        except requests.exceptions.RequestException as e:
            self.logger.error("Request exception: %s - %s", e, url)
            return None

    def get(self, endpoint: str, params: Dict = None) -> Union[Dict, bool, None]:
        """Make a GET request"""
        return self.request("get", endpoint, params=params)

    def post(
        self, endpoint: str, data: Any = None, params: Dict = None
    ) -> Union[Dict, bool, None]:
        """Make a POST request"""
        return self.request("post", endpoint, data=data, params=params)

    def delete(self, endpoint: str, params: Dict = None) -> Union[Dict, bool, None]:
        """Make a DELETE request"""
        return self.request("delete", endpoint, params=params)

    def close(self) -> None:
        """Close the session"""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
