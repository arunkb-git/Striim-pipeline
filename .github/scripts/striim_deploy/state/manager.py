"""Manages Striim application state transitions"""

from typing import Optional, List
import time
import logging

from striim_deploy.settings.models import SettingsModel
from striim_deploy.api.client import StriimClient
from striim_deploy.utils.logger import get_logger
from striim_deploy.utils.error_handler import format_error_message


class AppStateManager:
    """Manages Striim application state transitions"""

    def __init__(
        self,
        settings: SettingsModel,
        client: StriimClient,
        namespace: str,
        logger: logging.Logger = None,
    ):
        """
        Initialize the AppStateManager.

        Args:
            Settings: Deployment settings
            client: API client
            namespace: Target namespace
            logger: Logger instance (optional)
        """
        self.settings = settings
        self.client = client
        self.namespace = namespace
        self._logger = logger
        self._app_flows = {}

        # Define API operations
        self.operations = {
            "stop": self._stop_application,
            "undeploy": self._undeploy_application,
            "drop": self.drop_application,
            "wait": self._wait_for_transition,
            "none": lambda *args: True,
        }

    @property
    def logger(self):
        """Lazy loading for logger"""
        if self._logger is None:
            self._logger = get_logger(__name__)
        return self._logger

    def check_app_status(self, app_name: str) -> Optional[str]:
        """
        Check current application status and provide details for HALT states.

        Args:
            app_name: Application name

        Returns:
            Application status or None if not found
        """
        response = self.client.get(f"applications/{self.namespace}.{app_name}")

        if response and isinstance(response, dict):
            status = response.get("status")

            # If application is in HALT state, provide details
            if status == "HALT":
                halt_reason = response.get("haltReason") or response.get(
                    "statusDetails"
                )
                if halt_reason:
                    self.logger.error("❌ Application %s is in HALT state:", app_name)
                    # Format and display the error message
                    formatted_lines = format_error_message(halt_reason)
                    for line in formatted_lines:
                        self.logger.error("  %s", line)

            return status
        return None

    def wait_for_state(
        self, app_name: str, desired_state: str, timeout: int = None
    ) -> bool:
        """
        Wait for application to reach desired state.

        Args:
            app_name: Application name
            desired_state: Target state
            timeout: Maximum wait time in seconds

        Returns:
            True if state reached, False otherwise
        """
        if timeout is None:
            timeout = self.settings.state_transition_timeout

        start_time = time.time()
        end_time = start_time + timeout

        while time.time() < end_time:
            status = self.check_app_status(app_name)
            if status == desired_state:
                self.logger.info("Application reached %s state", desired_state)
                return True

            time.sleep(2)
            elapsed = int(time.time() - start_time)
            self.logger.info(
                "Waiting for %s state... (%ds/%ds)", desired_state, elapsed, timeout
            )

        self.logger.error("Timeout waiting for %s state", desired_state)
        return False

    @staticmethod
    def _request_succeeded(result) -> bool:
        """
        Decide whether an API result represents success.

        A successful Striim request returns ``True`` (no body) or the parsed
        response body (a dict/list/str). A failed request returns ``None`` or an
        error dict carrying ``{"error": True}``. The lifecycle endpoints (stop,
        undeploy) often return a 200 with a status body, so a dict without an
        error flag must be treated as success.
        """
        if not result:
            return False
        if isinstance(result, dict) and result.get("error"):
            return False
        return True

    def _stop_application(self, app_name: str, next_state: str, timeout: int) -> bool:
        """Stop a running application"""
        result = self.client.delete(f"applications/{self.namespace}.{app_name}/sprint")

        if not self._request_succeeded(result):
            self.logger.error(
                "Stop request failed for %s. API Response: %s", app_name, result
            )
            return False

        if next_state and not self.wait_for_state(app_name, next_state, timeout):
            self.logger.error(
                "Application %s failed to reach %s state after stop",
                app_name,
                next_state,
            )
            return False

        return True

    def start_application(self, app_name: str, max_retries: int = None) -> bool:
        """Start a deployed application with retries"""
        if max_retries is None:
            max_retries = self.settings.max_retries

        # Build the fully qualified app name
        app_fqn = f"{self.namespace}.{app_name}"

        for attempt in range(max_retries):
            # Check current status before attempting to start
            status = self.check_app_status(app_name)

            # If application is in HALT state, log the specific error
            if status == "HALT":
                return False
            # If application is already RUNNING, the start already succeeded
            if status == "RUNNING":
                self.logger.info("Application %s is already in RUNNING state", app_name)
                return True
            # If application is not in DEPLOYED state, log a warning
            if status != "DEPLOYED":
                self.logger.warning(
                    "Application %s is in %s state, not DEPLOYED state",
                    app_name,
                    status,
                )
                if attempt < max_retries - 1:
                    self.logger.info("Waiting for state to change...")
                    time.sleep(2)
                    continue
                else:
                    self.logger.error(
                        "Application never reached DEPLOYED state after %d checks",
                        max_retries,
                    )
                    return False

            self.logger.info(
                "Starting application %s (attempt %d/%d)",
                app_name,
                attempt + 1,
                max_retries,
            )

            result = self.client.post(f"applications/{app_fqn}/sprint")

            if isinstance(result, bool) and result:
                self.logger.info("✅ Successfully started application: %s", app_name)
                # Wait for application to transition to RUNNING state
                if self.wait_for_state(
                    app_name, "RUNNING", self.settings.state_transition_timeout
                ):
                    return True
            elif isinstance(result, dict):
                # Check if the application went into HALT state after starting
                status = self.check_app_status(app_name)
                if status == "HALT":
                    return False

            if attempt < max_retries - 1:
                self.logger.warning(
                    "Start attempt %d failed for %s, retrying in %d seconds...",
                    attempt + 1,
                    app_name,
                    2,
                )
                time.sleep(2)

        self.logger.error(
            "Failed to start application after %d attempts: %s", max_retries, app_name
        )
        return False

    def set_application_flows(self, app_name: str, flows: list) -> None:
        """
        Set the flows for an application for later deployment.

        Args:
            app_name: Application name
            flows: List of flow names
        """
        self._app_flows[app_name] = flows
        self.logger.info(
            "Stored %d flows for application %s: %s",
            len(flows),
            app_name,
            ", ".join(flows) if flows else "None",
        )

    def deploy_application(self, app_name: str, max_retries: int = None) -> bool:
        """
        Deploy an application using settings-based configuration.

        Args:
            app_name: Application name
            max_retries: Maximum number of retry attempts

        Returns:
            True if deployment succeeded, False otherwise
        """
        if max_retries is None:
            max_retries = self.settings.max_retries

        # Build the fully qualified application name with namespace
        app_fqn = f"{self.namespace}.{app_name}"

        # Check if application exists and is in the right state
        current_status = self.check_app_status(app_name)
        if not current_status or current_status not in [
            "CREATED",
            "RUNNING",
            "STOPPED",
        ]:
            self.logger.error(
                "Application %s is in %s state, cannot deploy",
                app_name,
                current_status or "non-existent",
            )
            return False

        # Get deployment configuration from settings
        deployment_config = self.settings.get("deployment", {})

        # Get flows for this application
        flows = self._app_flows.get(app_name, [])

        # Check if we're using single server mode
        single_server = deployment_config.get("single_server", True)
        if single_server:
            self.logger.info("Using single-server deployment mode")

        # Get application-specific configuration
        default_group = deployment_config.get("default_group", "default")
        default_strategy = deployment_config.get("default_strategy", "one")
        # Get application-specific configuration if available
        apps = deployment_config.get("applications", {})
        app_config = apps.get(app_name, {})

        # Get application's deployment group and strategy
        app_group = app_config.get("deployment_group", default_group)
        app_strategy = app_config.get("strategy", default_strategy)
        app_deploy_type = "ALL" if app_strategy.lower() == "all" else "ANY"

        # Create the new format deployment plan
        flow_deployment_plans = []

        if flows:
            self.logger.info(
                "Building deployment plan for %d flows with group '%s' and strategy '%s'",
                len(flows),
                app_group,
                app_deploy_type,
            )

            for flow in flows:
                # Check for flow-specific configuration
                flow_config = app_config.get("flows", {}).get(flow, {})

                # Get flow deployment settings or use app defaults
                flow_group = flow_config.get("deployment_group", app_group)
                flow_strategy = flow_config.get("strategy", app_strategy)
                flow_deploy_type = "ALL" if flow_strategy.lower() == "all" else "ANY"

                # Include namespace with flow name
                flow_fqn = f"{self.namespace}.{flow}"

                # Add flow to the flows array
                flow_deployment_plans.append(
                    {
                        "flowName": flow_fqn,
                        "deploymentGroupName": flow_group,
                        "deploymentType": flow_deploy_type,
                    }
                )

                self.logger.info(
                    "Added flow '%s' to deployment plan with group '%s' and type '%s'",
                    flow_fqn,
                    flow_group,
                    flow_deploy_type,
                )
        else:
            self.logger.warning(
                "No flows found for application %s, using empty flows array", app_name
            )

        # Create the complete deployment plan in the new format
        deployment_plan = {
            "deploymentGroupName": app_group,
            "deploymentType": app_deploy_type,
            "flows": flow_deployment_plans,
        }

        # Make the deployment API call
        for attempt in range(max_retries):
            self.logger.info(
                "Deploying application %s (attempt %d/%d)",
                app_name,
                attempt + 1,
                max_retries,
            )
            headers = {"Content-Type": "application/json"}
            self.logger.debug("Deployment plan: %s", deployment_plan)

            result = self.client.request(
                "post",
                f"applications/{app_fqn}/deployment",
                data=deployment_plan,
                headers=headers,
            )

            if isinstance(result, dict) and result.get("status"):
                self.logger.info("✅ Successfully deployed application: %s", app_name)

                # Wait for application to be in DEPLOYED state
                if self.wait_for_state(
                    app_name, "DEPLOYED", self.settings.state_transition_timeout
                ):
                    return True
            elif result is True:
                # Handle case where result is just True (boolean success)
                self.logger.info("✅ Successfully deployed application: %s", app_name)

                # Wait for application to be in DEPLOYED state
                if self.wait_for_state(
                    app_name, "DEPLOYED", self.settings.state_transition_timeout
                ):
                    return True
            elif isinstance(result, dict) and result.get("error"):
                # Log the specific error message for debugging
                error_message = result.get("failure_message") or result.get("raw_text")
                if error_message:
                    self.logger.error("Deployment error: %s", error_message)

            if attempt < max_retries - 1:
                self.logger.warning(
                    "Deploy attempt %d failed for %s, retrying in 2 seconds...",
                    attempt + 1,
                    app_name,
                )
                time.sleep(2)

        self.logger.error(
            "Failed to deploy application %s after %d attempts", app_name, max_retries
        )
        return False

    def _undeploy_application(
        self, app_name: str, next_state: str, timeout: int
    ) -> bool:
        """Undeploy an application"""

        # Check current state before attempting undeploy
        current_state = self.check_app_status(app_name)
        # If application is already in CREATED state, consider undeploy successful
        if current_state == "CREATED":
            self.logger.info(
                "Application %s is already in CREATED state, undeploy not needed",
                app_name,
            )
            return True

        result = self.client.delete(
            f"applications/{self.namespace}.{app_name}/deployment"
        )

        # Special handling for HALT state - check if it transitioned to CREATED
        if current_state == "HALT" and isinstance(result, dict):
            # Check if the application state changed to CREATED
            if result.get("status") == "CREATED":
                self.logger.info("Application transitioned from HALT to CREATED state")
                return True

        if not self._request_succeeded(result):
            self.logger.error(
                "Failed to undeploy application %s. API Response: %s", app_name, result
            )
            # Check if the application is now in CREATED state despite error response
            check_state = self.check_app_status(app_name)
            if check_state == "CREATED":
                self.logger.info(
                    "Application is now in CREATED state despite error response"
                )
                return True
            return False

        if next_state and not self.wait_for_state(app_name, next_state, timeout):
            self.logger.error(
                "Application %s failed to reach %s state after undeploy",
                app_name,
                next_state,
            )
            return False

        return True

    def drop_application(self, app_name: str) -> bool:
        """Drop an application completely"""
        result = self.client.delete(f"applications/{self.namespace}.{app_name}")

        if isinstance(result, bool) and result:
            self.logger.info("✅ Successfully dropped application: %s", app_name)
            return True

        self.logger.error("Failed to drop application: %s", app_name)
        return False

    def _wait_for_transition(
        self, app_name: str, next_state: str, timeout: int
    ) -> bool:
        """Wait for a state transition to complete"""
        return self.wait_for_state(app_name, next_state, timeout)

    def prepare_for_deployment(
        self, app_name: str, target_state: str = "CREATED", max_steps: int = 6
    ) -> bool:
        """
        Prepare an application for deployment based on its current state.

        Walks the configured state transitions, performing one action per step
        (e.g. RUNNING -> stop -> undeploy -> CREATED), until the application
        reaches ``target_state``, has no configured transition, or the step
        limit is hit. Re-checking the live state each step keeps a multi-stage
        teardown (stop *and* undeploy) from stopping after the first action.

        Args:
            app_name: Application name
            target_state: State that means "ready to (re)create" (default CREATED)
            max_steps: Safety bound on the number of transition steps

        Returns:
            True if prepared successfully, False otherwise
        """
        transitions = self.settings.get_state_transitions(app_name)

        for _ in range(max_steps):
            current_status = self.check_app_status(app_name)

            # Not deployed (or absent) -> nothing to tear down.
            if not current_status or current_status == target_state:
                return True

            transition_settings = transitions.get(
                current_status, transitions.get("DEFAULT", {})
            )

            if not transition_settings:
                self.logger.info(
                    "No transition settings for state %s, proceeding with deployment",
                    current_status,
                )
                return True

            action = transition_settings.get("action", "none")
            next_state = transition_settings.get("next_state")
            timeout = transition_settings.get("timeout", 60)

            # "none" is a terminal/ready state - stop walking.
            if action == "none":
                return True

            self.logger.info(
                "Application %s is in %s state.", app_name, current_status
            )
            self.logger.info("Taking action: %s", action)

            operation = self.operations.get(action)
            if not operation:
                self.logger.error("Unknown action: %s", action)
                return False

            if not operation(app_name, next_state, timeout):
                return False

        self.logger.error(
            "Application %s did not reach %s state within %d transition steps",
            app_name,
            target_state,
            max_steps,
        )
        return False
