"""Striim TQL deployer module"""

import os
import logging
from typing import List, Optional
from striim_deploy.settings.models import SettingsModel
from striim_deploy.api.client import StriimClient
from striim_deploy.core.validator import TQLValidator
from striim_deploy.state.manager import AppStateManager
from striim_deploy.utils.logger import get_logger
from striim_deploy.utils.error_handler import log_error_response
from striim_deploy.utils.namespace import split_identifier, namespace_from_filename


class StriimDeployer:
    """Handles deployment of TQL files to Striim"""

    def __init__(
        self,
        settings: SettingsModel,
        client: StriimClient,
        namespace: Optional[str] = None,
        override_namespace: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the StriimDeployer.

        Args:
            settings: Deployment settings
            client: API client
            namespace: Default namespace (e.g. derived from the branch). Used
                only when a file does not specify its own namespace. May be None.
            override_namespace: Explicit namespace that takes precedence over
                every other source (e.g. a --namespace argument). May be None.
            logger: Logger instance (optional)
        """
        self.settings = settings
        self.client = client
        self.namespace = namespace
        self.default_namespace = namespace
        self.override_namespace = override_namespace
        self.validator = TQLValidator(settings)
        self.state_manager = AppStateManager(settings, client, namespace)
        self._logger = logger

    def _resolve_namespace(self, file_path: str, identifier: str) -> Optional[str]:
        """
        Resolve the namespace for a single file.

        Priority (highest first):
          1. An explicit override (e.g. --namespace)
          2. A namespace prefix in the TQL (CREATE APPLICATION admin.sbrTest)
          3. A namespace prefix in the filename (admin.sbrTest.tql)
          4. The default branch -> namespace mapping

        Args:
            file_path: Path to the TQL file
            identifier: Application identifier as written in the TQL

        Returns:
            The resolved namespace, or None if no source provides one
        """
        if self.override_namespace:
            return self.override_namespace

        namespace_in_tql, _ = split_identifier(identifier)
        if namespace_in_tql:
            return namespace_in_tql

        namespace_in_filename = namespace_from_filename(file_path)
        if namespace_in_filename:
            return namespace_in_filename

        return self.default_namespace

    def _resolve_file_target(self, file_path: str):
        """
        Resolve and apply the (namespace, app_name) for a file.

        Sets ``self.namespace`` and the state manager's namespace so subsequent
        operations target the right namespace. Returns ``(None, None)`` if the
        application identifier cannot be extracted.

        Args:
            file_path: Path to the TQL file

        Returns:
            Tuple of (namespace or None, bare app name or None)
        """
        identifier = self.validator.extract_app_identifier(file_path)
        if not identifier:
            return None, None

        _, app_name = split_identifier(identifier)
        namespace = self._resolve_namespace(file_path, identifier)

        if namespace:
            self.namespace = namespace
            self.state_manager.namespace = namespace

        return namespace, app_name

    @property
    def logger(self):
        """Lazy loading for logger"""
        if self._logger is None:
            self._logger = get_logger(__name__)
        return self._logger

    def create_application(self, file_path: str) -> bool:
        """
        Create/update a single TQL application.

        Args:
            file_path: Path to TQL file

        Returns:
            True if deployment succeeded, False otherwise
        """
        self.logger.info("%s", "=" * 40)
        self.logger.info("Processing %s", file_path)
        self.logger.info("%s", "=" * 40)

        # Resolve the namespace and bare application name for this file
        namespace, app_name = self._resolve_file_target(file_path)
        if not app_name:
            self.logger.error("Could not extract application name from %s", file_path)
            return False

        if not namespace:
            self.logger.error(
                "Could not determine a namespace for %s. Add a namespace prefix "
                "to the application name (CREATE OR REPLACE APPLICATION ns.app), "
                "to the filename (ns.app.tql), or map the branch under "
                "'namespace_mapping' in the settings file.",
                file_path,
            )
            return False

        self.logger.info(
            "Deploying '%s' into namespace '%s'", app_name, namespace
        )

        # Enforce the configured filename naming pattern (no-op if disabled).
        if not self.validator.validate_naming_convention(file_path):
            return False

        if not self.validator.validate_filename(file_path, app_name):
            if self.settings.filename_mismatch == "error":
                return False
            else:
                self.logger.warning(
                    "Filename doesn't match app name, continuing anyway"
                )

        # Check and handle existing application status
        status = self.state_manager.check_app_status(app_name)
        if status:
            self.logger.info("Current application status: %s", status)
            # Prepare application for deployment based on its current state
            if not self.state_manager.prepare_for_deployment(app_name):
                self.logger.error(
                    "Failed to prepare application %s for deployment", app_name
                )
                return False

        # Read and process file content
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except IOError as e:
            self.logger.error("Failed to read file %s: %s", file_path, e)
            return False

        # Structural TQL sanity check (no-op if validate_syntax is disabled).
        if not self.validator.validate_syntax(content):
            self.logger.error("TQL syntax validation failed for %s", file_path)
            return False

        # Extract flows from the TQL content
        flows = self.validator.extract_flows(file_path)
        self.logger.info(
            "Extracted %d flows from %s: %s",
            len(flows),
            file_path,
            ", ".join(flows) if flows else "None",
        )

        # Preprocess TQL content
        processed_content = self.validator.preprocess_tql_content(content)
        if processed_content is None:  # Preprocessing failed
            return False

        # Normalize the namespace: declare it once via USE and (optionally)
        # strip any inline namespace prefixes so they cannot conflict with it.
        if self.settings.strip_namespace_prefix:
            processed_content = self.validator.strip_namespace_in_statements(
                processed_content
            )
        processed_content = f"USE {self.namespace};\n{processed_content}"

        # Deploy using API client
        response = self.client.request(
            "post",
            "tungsten",
            data=processed_content,
            headers={"content-type": "text/plain"},
        )
        self.logger.debug("Raw response type: %s, Value: %s", type(response), response)

        application_created = False

        if isinstance(response, bool) and response:
            self.logger.info("✅ Successfully created %s", file_path)
            application_created = True
        # For list responses with all successful commands, consider it a success
        elif isinstance(response, list) and all(
            cmd.get("executionStatus") == "Success" and cmd.get("responseCode") == 200
            for cmd in response
            if isinstance(cmd, dict)
        ):
            self.logger.info(
                "✅ Successfully created %s (all commands succeeded)", file_path
            )
            application_created = True
        else:
            if not application_created:
                log_error_response(response, self.logger)
                return False

        # After successfully creating the application
        # Use the FQN for settings lookups so per-app config keys like
        # "admin.TestApplication" or "TestApplication" are both found correctly.
        app_identifier = f"{namespace}.{app_name}" if namespace else app_name
        if application_created and self.settings.get_auto_deploy(app_identifier):
            self.logger.info("🚀 Attempting to auto-deploy application: %s", app_name)

            # Store flows in the state manager for deployment
            self.state_manager.set_application_flows(app_name, flows)

            # Check if this app has specific deployment settings
            app_config = self.settings.get_deployment_config(app_identifier)
            if app_config:
                self.logger.info(
                    "Using custom deployment configuration for %s", app_name
                )

            deploy_success = self.state_manager.deploy_application(app_name)

            if not deploy_success:
                self.logger.error("❌ Failed to deploy application %s", app_name)
                return False

            # Only attempt to start if auto_start is enabled and deployment was successful
            if self.settings.get_auto_start(app_identifier):
                # Wait for application to reach DEPLOYED state
                if self.state_manager.wait_for_state(
                    app_name, "DEPLOYED", timeout=self.settings.state_transition_timeout
                ):
                    self.logger.info(
                        "🚀 Attempting to auto-start application: %s", app_name
                    )
                    return self.state_manager.start_application(app_name)
                else:
                    self.logger.error(
                        "❌ Application failed to reach DEPLOYED state, cannot start"
                    )
                    return False

        # Creation succeeded. When auto_deploy is disabled we stop here and still
        # report success (the application was created), rather than falling
        # through to an implicit None.
        return True

    def create_applications(
        self,
        file_list: List[str],
        drop_list: List[str] = None,
        force_drop_all: bool = False,
    ) -> bool:
        """
        Create multiple TQL applications.

        Args:
            file_list: List of TQL file paths
            drop_list: List of applications to drop before creation
            force_drop_all: Whether to drop all applications before creation

        Returns:
            True if all creations succeeded, False otherwise
        """
        # Convert None to empty list
        drop_list = drop_list or []

        success = True
        failed_files = []

        # First, resolve all file targets so we can prepare them for deployment
        resolved_targets = []  # List of tuples (file_path, namespace, app_name)
        for file_path in file_list:
            if os.path.exists(file_path):
                namespace, app_name = self._resolve_file_target(file_path)
                if app_name and namespace:
                    resolved_targets.append((file_path, namespace, app_name))
                else:
                    self.logger.warning(
                        "Skipping unresolved target for file: %s", file_path
                    )
            else:
                self.logger.info("Skipping deleted file: %s", file_path)

        # Pre-prepare all resolved applications for deployment (stop/undeploy)
        for _file_path, namespace, app_name in resolved_targets:
            # Ensure state manager targets the correct namespace
            self.namespace = namespace
            self.state_manager.namespace = namespace
            self.logger.info(
                "Preparing existing application %s in namespace %s for redeploy",
                app_name,
                namespace,
            )
            if not self.state_manager.prepare_for_deployment(app_name):
                self.logger.error(
                    "Failed to prepare application %s for deployment", app_name
                )
                if not self.settings.continue_on_error:
                    return False

        # Now create each application (in original order)
        for file_path in file_list:
            if os.path.exists(file_path):
                # Resolve namespace + app name so a drop targets the right FQN
                namespace, app_name = self._resolve_file_target(file_path)

                if app_name and namespace:
                    # Check if app should be dropped explicitly
                    should_drop = force_drop_all or app_name in drop_list

                    if should_drop:
                        self.logger.info(
                            "🗑️ Dropping application before creation: %s", app_name
                        )
                        self.state_manager.drop_application(app_name)

                # Create the application
                result = self.create_application(file_path)

                if not result:
                    success = False
                    failed_files.append(file_path)

                    if not self.settings.continue_on_error:
                        break
            else:
                self.logger.info("Skipping deleted file: %s", file_path)

        # Log creation result
        if not success:
            self.logger.error("❌ CREATION COMPLETED WITH FAILURES")
            self.logger.error("Failed files: %s", failed_files)
        else:
            self.logger.info("✅ ALL APPLICATIONS CREATED SUCCESSFULLY")

        return success
