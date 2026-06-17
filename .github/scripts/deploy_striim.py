"""
Striim TQL deployment tool.
Command-line utility for deploying TQL files to Striim instances with
namespace mapping, authentication, and settings management.
"""

import sys
import os
import argparse
import logging
from typing import List, Optional, Tuple

from striim_deploy.settings.models import SettingsModel
from striim_deploy.utils.logger import configure_logging
from striim_deploy.settings.loader import load_settings
from striim_deploy.api.client import StriimClient
from striim_deploy.core.deployer import StriimDeployer
from striim_deploy.utils.namespace import NamespaceMapper


def parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments.

    Defines and processes command line arguments for the deployment tool.

    Returns:
        Parsed command line arguments
    """
    parser = argparse.ArgumentParser(description="Deploy Striim TQL files")
    parser.add_argument("--config", required=True, help="Path to settings YAML")
    parser.add_argument("--files", required=True, help="List of files to deploy")
    parser.add_argument("--namespace", help="Target namespace")
    parser.add_argument("--branch", help="Branch name for namespace mapping")
    parser.add_argument(
        "--drop-applications", help="Comma-separated list of apps to drop"
    )
    parser.add_argument(
        "--force-drop-all", action="store_true", help="Drop all changed apps"
    )

    return parser.parse_args()


def get_credentials() -> Optional[Tuple[str, str, str]]:
    """
    Get Striim credentials from environment variables.

    Returns:
        Tuple of (base_url, username, password) or None if missing
    """
    base_url = os.getenv("STRIIM_BASE_URL")
    username = os.getenv("STRIIM_USERNAME")
    password = os.getenv("STRIIM_PASSWORD")

    return (
        (base_url, username, password) if all([base_url, username, password]) else None
    )


def resolve_namespaces(
    args: argparse.Namespace, settings: SettingsModel, logger: logging.Logger
) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve the namespace sources for a deployment.

    Returns two values, both of which may be None:
      * override: an explicit --namespace that wins over every other source.
      * default: the branch -> namespace mapping, used only when a file does
        not declare its own namespace (in its TQL or filename).

    The per-file namespace is resolved later by the deployer, so neither value
    is required here; a file may supply its own namespace.

    Args:
        args: Command line arguments
        settings: Deployment settings
        logger: Logger instance

    Returns:
        Tuple of (override namespace or None, default namespace or None)
    """
    override = args.namespace or None

    default = None
    if args.branch:
        mapper = NamespaceMapper(settings.get_namespace_mapping(), logger=logger)
        default = mapper.get_namespace(args.branch)
        if default:
            logger.info(
                f"Default namespace '{default}' derived from branch '{args.branch}'"
            )
        else:
            logger.info(
                "Branch '%s' is not mapped under 'namespace_mapping'; relying on "
                "per-file namespaces (TQL prefix or filename).",
                args.branch,
            )

    return override, default


def parse_file_list(files_arg: str) -> List[str]:
    """
    Parse the list of files to deploy.

    Args:
        files_arg: File list as string

    Returns:
        List of file paths
    """
    return files_arg.split("\n") if files_arg else []


def parse_drop_list(drop_arg: str) -> List[str]:
    """
    Parse the list of applications to drop.

    Args:
        drop_arg: Comma-separated application names

    Returns:
        List of application names
    """
    return [app.strip() for app in (drop_arg or "").split(",") if app.strip()]


def main():
    """
    Main entry point for the Striim deployment tool.
    """
    # Load command line arguments
    args = parse_arguments()

    # Load settings
    settings = load_settings(args.config)

    # Setup logging
    logger = configure_logging("striim_deploy", settings)

    try:
        # Get credentials
        credentials = get_credentials()
        if not credentials:
            logger.error("Missing required environment variables")
            sys.exit(1)

        base_url, username, password = credentials

        # Determine namespace sources. Both may be None: each file can supply
        # its own namespace via its TQL or filename, resolved by the deployer.
        override_namespace, default_namespace = resolve_namespaces(
            args, settings, logger
        )

        # Create API client with authentication
        with StriimClient(base_url, username, password, logger=logger) as client:
            # Make sure authentication succeeds
            if not client.ensure_authenticated():
                logger.error("Authentication failed")
                sys.exit(1)

            # Create deployer
            deployer = StriimDeployer(
                settings,
                client,
                namespace=default_namespace,
                override_namespace=override_namespace,
                logger=logger,
            )

            # Parse file and drop lists
            file_list = parse_file_list(args.files)
            drop_list = parse_drop_list(args.drop_applications)

            # Deploy files. Per-application deployment_group/strategy (and any
            # per-flow overrides) come from the settings file, resolved in
            # AppStateManager.deploy_application.
            success = deployer.create_applications(
                file_list, drop_list=drop_list, force_drop_all=args.force_drop_all
            )

            sys.exit(0 if success else 1)

    except (ValueError, RuntimeError) as e:  # Replace with specific exceptions
        logger.exception("Deployment failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
