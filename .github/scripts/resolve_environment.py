"""
Resolve the GitHub Environment for a Striim deployment.

The CI workflow uses the printed value to select the job's GitHub Environment,
which in turn provides the scoped STRIIM_BASE_URL / STRIIM_USERNAME /
STRIIM_PASSWORD secrets (and therefore the target server) for the deployment.

Resolution order:
  1. An explicit ``--environment`` (e.g. from a manual workflow_dispatch).
  2. The ``environment_mapping`` entry for ``--branch`` in the settings file.

Exits non-zero with a clear message if no environment can be determined, so a
push to an unmapped branch fails fast instead of deploying to the wrong server.
"""

import argparse
import sys

from striim_deploy.settings.loader import load_settings


def resolve_environment(config_path: str, branch: str, override: str) -> str:
    """Return the environment name, or an empty string if undeterminable."""
    environment = (override or "").strip()
    if environment:
        return environment

    if branch:
        settings = load_settings(config_path)
        return settings.get_environment_mapping().get(branch, "")

    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Resolve the GitHub Environment for a Striim deployment"
    )
    parser.add_argument("--config", required=True, help="Path to settings YAML")
    parser.add_argument("--branch", default="", help="Git branch name")
    parser.add_argument(
        "--environment",
        default="",
        help="Explicit environment override (e.g. from workflow_dispatch)",
    )
    args = parser.parse_args()

    environment = resolve_environment(args.config, args.branch, args.environment)

    if not environment:
        sys.stderr.write(
            f"No environment mapping for branch '{args.branch}'. "
            "Add it to 'environment_mapping' in the settings file, or pass an "
            "explicit environment via workflow_dispatch.\n"
        )
        sys.exit(1)

    # Print only the resolved environment so the workflow can capture it.
    print(environment)


if __name__ == "__main__":
    main()
