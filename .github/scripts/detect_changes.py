"""
Git change detection utility for Striim TQL files.

Detects changed TQL files between commits and outputs results
for GitHub Actions workflow integration.
"""

import subprocess
import os
import sys
import logging
from typing import List, Tuple, Optional

from striim_deploy.settings.models import SettingsModel
from striim_deploy.settings.loader import get_settings
from striim_deploy.utils.logger import get_logger


def get_previous_commit_sha() -> str:
    """
    Get the SHA of the previous commit.

    Returns:
    SHA of the previous commit
    """
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD^"], text=True).strip()
    except subprocess.CalledProcessError as e:
        # Handle the case where there might not be a previous commit
        logging.getLogger(__name__).error("Error getting previous commit SHA: %s", e)
        logging.getLogger(__name__).info(
            "Falling back to comparing with the empty tree"
        )
        return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # Empty tree SHA


def get_changed_files(prev_sha: str, logger: logging.Logger) -> List[Tuple[str, str]]:
    """
    Get list of files changed between commits.

    Args:
        prev_sha: SHA of the previous commit
        logger: Logger instance

    Returns:
    List of (status, filename) tuples
    """
    try:
        logger.info("Comparing current HEAD with %s", prev_sha)
        diff_output = subprocess.check_output(
            ["git", "diff", "--name-status", prev_sha, "HEAD"], text=True
        )

        logger.debug("Raw diff output:\n%s", diff_output)

        # More Pythonic approach using list comprehension
        result = []
        for line in diff_output.splitlines():
            if not line.strip():
                continue

            parts = line.split(maxsplit=2)
            if len(parts) >= 2:
                status, filename = parts[0], parts[1]
                # Handle renamed files
                if status.startswith("R") and len(parts) == 3:
                    filename = parts[2]

                result.append((status, filename))

        return result
    except subprocess.CalledProcessError as e:
        logger.error("Error getting changed files: %s", e)
        return []


def filter_tql_files(
    files: List[Tuple[str, str]],
    allowed_dirs: List[str],
    require_specific_dirs: bool,
    logger: logging.Logger,
) -> List[str]:
    """
    Filter to include only TQL files in allowed directories.

    Args:
        files: List of (status, filename) tuples
        allowed_dirs: List of allowed directory paths
        require_specific_dirs: Whether to require files to be in specific directories
        logger: Logger instance

    Returns:
        Filtered list of TQL file paths
    """
    logger.info("Filtering %d files for TQL files in allowed directories", len(files))

    filtered_files = []

    for status, filename in files:
        logger.debug("Checking file: %s %s", status, filename)

        if filename.endswith(".tql"):
            logger.debug("Found TQL file: %s", filename)

            is_in_allowed_dir = any(
                allowed_dir in filename for allowed_dir in allowed_dirs
            )

            logger.debug("Is in allowed dir: %s", is_in_allowed_dir)

            if is_in_allowed_dir or not require_specific_dirs:
                filtered_files.append(filename)
                logger.info("%s\t%s", status, filename)

    logger.info("Found %d TQL files after filtering", len(filtered_files))
    return filtered_files


def write_github_output(changed_files: List[str], logger: logging.Logger) -> None:
    """
    Write the change detection results to GitHub Actions output.

    args:
        changed_files: List of changed TQL file paths
        logger: Logger instance
    """
    has_changes = "true" if changed_files else "false"

    # If GITHUB_OUTPUT is available, write to that file
    if "GITHUB_OUTPUT" in os.environ:
        output_file = os.environ["GITHUB_OUTPUT"]
        logger.info("Writing output to GitHub Actions output file: %s", output_file)

        try:
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(f"has_changes={has_changes}\n")
                f.write("changed_files<<EOF\n")
                f.write("\n".join(changed_files))
                f.write("\nEOF\n")
        except (OSError, IOError) as e:
            logger.error("Error writing to GITHUB_OUTPUT file: %s", e)

            # Fall back to stdout for debugging if file write fails
            logger.info("Falling back to stdout for output")
            print(f"has_changes={has_changes}")
            print("changed_files:")
            for file in changed_files:
                print(f"  - {file}")
    else:
        # For backwards compatibility or local testing, output to stdout
        logger.warning(
            "GITHUB_OUTPUT environment variable not set, output only printed to stdout"
        )
        print(f"has_changes={has_changes}")
        print("changed_files:")
        for file in changed_files:
            print(f"  - {file}")


def detect_changed_files(settings_path: Optional[str] = None) -> Tuple[str, List[str]]:
    """
        Detect changed TQL files based on settings.

        Args:
        settings_path: Path to settings file

    Returns:
        Tuple of (has_changes, list_of_changed_files)
    """
    # Setup logging
    logger = get_logger("change_detection")
    logger.info("Starting change detection")

    try:
        # Load settings
        try:
            settings = get_settings(settings_path)
            logger.info(
                "Successfully loaded settings from %s",
                settings_path if settings_path else "default locations",
            )
        except (FileNotFoundError, ImportError, AttributeError) as e:
            logger.error("Error loading settings: %s", e)
            logger.info("Falling back to default settings")
            settings = SettingsModel()

        # Get validation settings
        try:
            validation = settings.get_validation()
            allowed_dirs = validation.get("allowed_directories", ["striim/TQL"])
            require_specific_dirs = validation.get("require_specific_directories", True)
        except (KeyError, AttributeError) as e:
            logger.error("Error accessing validation settings: %s", e)
            logger.info("Falling back to default settings")
            require_specific_dirs = True

        logger.info("Using allowed directories: %s", allowed_dirs)
        logger.info("Require specific directories: %s", require_specific_dirs)

        # Try to detect Git repository
        try:
            repo_root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True
            ).strip()
            logger.info("Git repository detected at: %s", repo_root)
        except subprocess.CalledProcessError as e:
            logger.error("Error detecting Git repository: %s", e)
            logger.info("Make sure you're running this script inside a Git repository")

        # Get the previous commit SHA
        prev_sha = get_previous_commit_sha()
        logger.info("Previous commit SHA: %s", prev_sha)

        # Get all changed files
        changed_statuses_files = get_changed_files(prev_sha, logger)
        logger.info("Found %d changed files in total", len(changed_statuses_files))

        # Filter to only TQL files in allowed directories
        changed_files = filter_tql_files(
            changed_statuses_files, allowed_dirs, require_specific_dirs, logger
        )

        # Set outputs for GitHub Actions
        has_changes = "true" if changed_files else "false"
        logger.info("Changed TQL files detected: %d", len(changed_files))
        for file in changed_files:
            logger.info("- %s", file)

        # Write to GitHub Actions output file
        write_github_output(changed_files, logger)

        return has_changes, changed_files

    except subprocess.CalledProcessError as e:
        logger.error("Git command failed: %s", e)
        sys.exit(1)
    except (OSError, ValueError) as e:
        logger.exception("Error detecting changes: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    # Default settings path can be overridden via command line argument
    settings_path = None
    if len(sys.argv) > 1:
        settings_path = sys.argv[1]

    detect_changed_files(settings_path)
