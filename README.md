# Striim TQL Deployment

![demo](demo.gif)

GitOps-style deployment of Striim TQL applications: commit a `.tql` file, push,
and a GitHub Actions workflow validates it and deploys it to the correct Striim
server and namespace — no manual steps.

The pipeline is driven by **two files**:

| File | Purpose |
|------|---------|
| [`.github/workflows/striim-deploy.yml`](.github/workflows/striim-deploy.yml) | The GitHub Actions workflow — *when* to deploy, on *which* runner, with *which* credentials. |
| [`.github/striim-deploy-settings.yml`](.github/striim-deploy-settings.yml) | Deployment behavior — namespace/environment mapping, validation rules, deployment groups, state handling. |

The deployment logic itself lives in `.github/scripts/` (a small Python package,
`striim_deploy`). Day to day you only edit the two YAML files above and your TQL.

## Table of Contents

- [Prerequisites](#prerequisites)
- [How It Works](#how-it-works)
- [The Workflow (`striim-deploy.yml`)](#the-workflow-striim-deployyml)
- [The Settings File (`striim-deploy-settings.yml`)](#the-settings-file-striim-deploy-settingsyml)
- [Namespace Resolution](#namespace-resolution)
- [How `strip_namespace_prefix` Works](#how-strip_namespace_prefix-works)
- [Setup Guide](#setup-guide)
- [Python Client Reference](#python-client-reference)

## Prerequisites

### GitHub Environments

Each deployment target (e.g. `dev`, `prod`) must be configured as a **GitHub Environment** in your repository before the workflow can deploy to it.

For each environment:

1. Go to **Settings → Environments** and create an environment whose name matches the value in `environment_mapping` (e.g. `dev`, `prod`).
2. Add the following three secrets to the environment:

   | Secret | Description |
   |--------|-------------|
   | `STRIIM_BASE_URL` | Base URL of the target Striim server (e.g. `https://striim.example.com`) |
   | `STRIIM_USERNAME` | Striim username for the deployment account |
   | `STRIIM_PASSWORD` | Striim password for the deployment account |

3. Optionally configure **protection rules** (e.g. required reviewers) on production environments to add an approval gate before deploys go out.

Because each environment has its own `STRIIM_BASE_URL`, Dev, Test, and Prod can live on **separate Striim clusters** — or on the **same cluster differentiated by namespace** — with no changes to the workflow.

### TQL Files Must Use Vaults for All Sensitive Values

TQL files committed to this repository **must not contain any environment-specific values** — connection strings, hostnames, usernames, passwords, API keys, or any other credentials. Hardcoding these values ties a TQL file to a single environment, breaks promotion across Dev/Test/Prod, and risks exposing secrets in version control.

Instead, store all sensitive values in a **Striim vault** and reference them in TQL using the `[[vault.key]]` syntax:

```sql
-- Good: vault reference, promotes cleanly across environments
Username: '[[myvault.db_username]]',
Password: '[[myvault.db_password]]',
ConnectionURL: '[[myvault.db_connection_url]]',

-- Bad: hardcoded values, environment-specific and insecure
Username: 'admin',
Password: 'secret123',
ConnectionURL: 'jdbc:oracle:thin:@prod-db.example.com:1521:ORCL',
```

Create a vault with the **same name** in each namespace (Dev, Test, Prod). If the vault entries share the same key names but hold environment-appropriate values, the same TQL deploys correctly to every environment without modification.

Striim supports several vault backends — native (AES-256), Azure Key Vault, CyberArk, Google Secret Manager, and HashiCorp Vault. See the [Striim documentation on Using Vaults](https://www.striim.com/docs/en/using-vaults.html) for setup instructions and TQL syntax.

---

## How It Works

```
push to master / develop ─┐
   (or manual dispatch)    │
                           ▼
            ┌─────────────────────────────┐
            │ resolve job                 │  branch ─▶ GitHub Environment
            │   resolve_environment.py    │  (dev / prod / …)
            └──────────────┬──────────────┘
                           ▼
            ┌─────────────────────────────┐
            │ deploy job  (Environment)   │  scoped secrets = target server
            │   detect_changes.py         │  → changed *.tql
            │   deploy_striim.py          │  → validate, USE <ns>;, create,
            └─────────────────────────────┘     deploy (optionally start)
```

1. **Trigger** — a push to `master` or `develop`, or a manual run from the
   **Actions** tab.
2. **`resolve` job** — maps the branch (or a manual override) to a GitHub
   Environment via `environment_mapping`. The environment selects the scoped
   Striim credentials, and therefore the target server.
3. **`deploy` job** — runs *under* that environment, finds the `.tql` files
   changed in the push, validates each, resolves its namespace, and deploys it.

Both jobs run on a **self-hosted runner** (`runs-on: self-hosted`) so they can
reach Striim from inside the trusted network.

## The Workflow (`striim-deploy.yml`)

### Triggers

```yaml
on:
  push:
    branches: [ 'master', 'develop' ]
  workflow_dispatch:
    inputs:
      environment:        # optional: override the branch → environment mapping
      drop_applications:  # optional: comma-separated apps to drop first
      force_drop_all:     # optional: drop every changed app before redeploy
```

- A **push** to `master`/`develop` deploys automatically. Add branches here to
  enable more.
- **workflow_dispatch** runs it manually (**Actions → Deploy Striim TQL → Run
  workflow**) with the optional inputs above.

### Jobs

| Job | Runs on | What it does |
|-----|---------|--------------|
| `resolve` | self-hosted | Runs `resolve_environment.py` to turn the branch (or the `environment` input) into a GitHub Environment name, exposed as a job output. |
| `deploy` | self-hosted, under `environment:` | Checks out (depth 2, for the diff), runs `detect_changes.py` to list changed TQL, then `deploy_striim.py` to deploy them. |

### Credentials

The `deploy` job sets `environment: ${{ needs.resolve.outputs.environment }}`.
That scopes `secrets.*` to **that environment's** secrets, so the `env` block
uses constant names on every branch:

```yaml
env:
  STRIIM_BASE_URL:  ${{ secrets.STRIIM_BASE_URL }}
  STRIIM_USERNAME:  ${{ secrets.STRIIM_USERNAME }}
  STRIIM_PASSWORD:  ${{ secrets.STRIIM_PASSWORD }}
```

You define these three secrets once per environment (see [Setup Guide](#setup-guide)).
Because each environment has its own `STRIIM_BASE_URL`, environments can live on
**different servers**.

## The Settings File (`striim-deploy-settings.yml`)

This file controls *how* deployments behave. The key sections:

### `deployment`

Controls how applications are placed onto Striim servers. Striim clusters can
have multiple servers organized into named **deployment groups**; the
`strategy` decides how many of those servers an application runs on.

#### Top-level defaults

```yaml
deployment:
  default_group: "default"   # deployment group used when no app-level override exists
  default_strategy: "one"    # "one" = any single server in the group
                             # "all" = every server in the group
  single_server: true        # set true when the cluster has only one server
```

These are the fallbacks applied to every application that has no entry in
`applications`.

#### Application-level overrides

Each key under `deployment.applications` is an application name (bare name or
fully-qualified `namespace.appName` — both resolve correctly). Any field you
set here overrides the global default **for that application only**; fields you
omit fall back to the global value.

```yaml
deployment:
  applications:
    MyApp1:
      deployment_group: "Group1"   # override default_group
      strategy: "all"              # override default_strategy → run on every server in Group1
      auto_start: true             # override validation.auto_start for this app only
      auto_deploy: true            # override validation.auto_deploy for this app only
      state_transitions:           # override per-state teardown timeouts (merged, not replaced)
        RUNNING:
          timeout: 120             # wait up to 120 s for the app to stop before redeploying
        STOPPING:
          timeout: 120
```

The available per-application overrides are:

| Key | Overrides | Description |
|-----|-----------|-------------|
| `deployment_group` | `deployment.default_group` | Striim deployment group this app belongs to |
| `strategy` | `deployment.default_strategy` | `"one"` (any server) or `"all"` (every server in the group) |
| `auto_start` | `validation.auto_start` | Start the app automatically after it is deployed |
| `auto_deploy` | `validation.auto_deploy` | Deploy the app automatically after it is created from TQL |
| `state_transitions` | Global `state_transitions` timeouts | Per-state teardown timeouts (see below) |
| `flows` | — | Flow-level deployment overrides (see below) |

**`auto_start` vs `auto_deploy`**

- `auto_deploy: true` — after the TQL is submitted and the application is in
  `CREATED` state, the pipeline immediately deploys it (moves it to `DEPLOYED`).
- `auto_start: true` — after deploying, the pipeline immediately starts the
  application (moves it to `RUNNING`).

Set `auto_start: false` (the global default) for applications you want to
deploy but start manually, for example during an initial rollout or when a
pipeline depends on external systems being ready first.

**Per-application `state_transitions` overrides**

When the pipeline needs to redeploy an application that already exists, it
walks the app back to `CREATED` state using the `state_transitions` matrix. The
per-application block is **shallow-merged** over the global matrix, so you only
need to list the keys you want to change — everything else falls back to the
global definition:

```yaml
# Global matrix — applies to all apps
state_transitions:
  RUNNING:
    action: "stop"
    next_state: "STOPPED"
    timeout: 30          # global: wait up to 30 s for the app to stop

deployment:
  applications:
    SlowApp:
      state_transitions:
        RUNNING:
          timeout: 120   # override only the timeout; action/next_state stay the same
```

This is useful for applications that process large in-flight batches and need
more time to drain before they can be safely stopped.

#### Flow-level overrides

Individual flows within an application can each target a different deployment
group and strategy. Flow overrides are nested under the application's `flows`
key:

```yaml
deployment:
  applications:
    MyApp1:
      deployment_group: "Group1"
      strategy: "all"
      flows:
        FlowX:
          deployment_group: "Group2"   # FlowX runs on Group2, not Group1
          strategy: "all"
        FlowY:
          deployment_group: "Group3"
          strategy: "one"              # FlowY runs on one server in Group3
```

This lets a single application spread its flows across multiple server groups —
useful when different flows have different resource requirements or need to be
co-located with specific data sources.

#### Override resolution order

For any given application the pipeline resolves each setting in this order
(first match wins):

```
flow-level override
  └─ application-level override
       └─ deployment top-level default (default_group / default_strategy)
            └─ global validation default (auto_start / auto_deploy)
```

#### Application name matching

The `applications` key accepts both fully-qualified names (`admin.sbrTest`) and
bare names (`sbrTest`). Use whichever form you prefer — the namespace is always
resolved through the normal [Namespace Resolution](#namespace-resolution)
process (from the TQL content, filename, or `namespace_mapping`); the key here
is purely for matching the right config block.

The pipeline matches by stripping the namespace prefix, so `admin.sbrTest` in
the config and a bare `sbrTest` passed by the deploy script both resolve to the
same block:

```yaml
deployment:
  applications:
    admin.sbrTest:   # matched by bare name "sbrTest"; namespace comes from the TQL or filename
      auto_start: true
```

### `validation`

Where TQL must live, naming rules, and how content is processed before deploy.

```yaml
validation:
  require_specific_directories: true
  allowed_directories: [ "striim/TQL" ]
  enforce_naming_convention: true
  naming_pattern: "^[a-zA-Z][a-zA-Z0-9_-]*\\.tql$"
  filename_mismatch: "error"          # "error" | "warning"
  enforce_create_or_replace: true
  create_or_replace_strategy: "auto"  # "auto" adds "OR REPLACE"; "require" rejects without it
  strip_namespace_prefix: true        # see the section below
  auto_deploy: true                   # deploy immediately after create
  auto_start: false                   # start immediately after deploy
  continue_on_error: false            # stop on the first failure
  state_transition_timeout: 60
  max_retries: 3
```

### `namespace_mapping` (optional)

A fallback mapping of branch → Striim namespace. **Optional** — a file can carry
its own namespace instead. See [Namespace Resolution](#namespace-resolution).

```yaml
namespace_mapping:
  develop: "dev"
  master: "prod"
```

### `environment_mapping`

Branch → GitHub Environment. The environment selects the scoped secrets (and so
the target server). Decoupled from `namespace_mapping`, so an environment need
not share the namespace's name and can live on a different server.

```yaml
environment_mapping:
  develop: "dev"
  master: "prod"
```

### `state_transitions`

For an app that already exists in Striim, how to move it out of its current
state before redeploying (e.g. `RUNNING` → stop → undeploy → recreate). Each
state declares an `action`, the `next_state` to wait for, and a `timeout`.

### `application_patterns`

Regexes used to find the application name inside a `.tql` file.

### `logging`

```yaml
logging:
  level: "info"   # "debug" | "info" | "warning" | "error"
```

## Namespace Resolution

The **Striim namespace** an application deploys into is resolved **per file**.
`namespace_mapping` is only a fallback. For each file, the namespace is
determined in this order (first match wins):

| Priority | Source | Example |
|----------|--------|---------|
| 1 | `--namespace` override (passed to `deploy_striim.py`) | `--namespace admin` |
| 2 | Namespace prefix in the TQL | `CREATE OR REPLACE APPLICATION admin.sbrTest` → `admin` |
| 3 | Namespace prefix in the filename | `admin.sbrTest.tql` → `admin` |
| 4 | Branch → namespace mapping | `namespace_mapping: { master: prod }` |

So all of the following target the `admin` namespace, and you do **not** need a
`namespace_mapping` entry if the file carries its own namespace:

```sql
-- striim/TQL/sbrTest.tql        (namespace declared in the TQL)
CREATE OR REPLACE APPLICATION admin.sbrTest ...
```

```sql
-- striim/TQL/admin.sbrTest.tql  (namespace declared in the filename)
CREATE OR REPLACE APPLICATION sbrTest ...
```

If no source yields a namespace, the deploy fails fast with a message explaining
the options. The filename check tolerates a namespace prefix, so
`admin.sbrTest.tql` still matches application `sbrTest`.

## How `strip_namespace_prefix` Works

`strip_namespace_prefix: true` (the default) controls how the namespace is
written into the **deployed** TQL. When enabled, the pipeline:

1. Removes any inline `namespace.` prefix from `CREATE APPLICATION` and
   `CREATE FLOW` statements, and
2. Prepends a single `USE <namespace>;` line using the resolved namespace.

This guarantees the namespace is declared in exactly one place, so an inline
prefix can never disagree with the active namespace. Given a resolved namespace
of `admin`, this input:

```sql
CREATE OR REPLACE APPLICATION admin.sbrTest;
```

is deployed as:

```sql
USE admin;
CREATE OR REPLACE APPLICATION sbrTest;
```

Regardless of this flag, applications and flows are always **tracked by their
bare name** (the namespace is applied separately when building the fully
qualified `namespace.app` name for the API), so status checks, drops, and state
transitions target the correct object.

## Setup Guide

### 1. Register a self-hosted runner

The jobs use `runs-on: self-hosted`, so a runner must be registered with the
repository. In GitHub go to **Settings → Actions → Runners → New self-hosted
runner**, choose the OS/architecture, and follow the generated commands. For
example, on macOS (Apple Silicon):

```bash
mkdir actions-runner && cd actions-runner
curl -o actions-runner-osx-arm64-2.334.0.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.334.0/actions-runner-osx-arm64-2.334.0.tar.gz
tar xzf ./actions-runner-osx-arm64-2.334.0.tar.gz
./config.sh --url https://github.com/<owner>/<repo> --token <REGISTRATION_TOKEN>
./run.sh        # or install as a service: ./svc.sh install && ./svc.sh start
```

> The registration token is short-lived — copy a fresh one from the Settings
> page rather than reusing an old one. Full guide:
> [Adding self-hosted runners](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners).

The runner needs **Python 3** with `pip3` available (the jobs run
`pip3 install -r .github/scripts/requirements.txt`).

### 2. Create GitHub Environments and secrets

For each value in `environment_mapping` (e.g. `dev`, `prod`):

1. Go to **Settings → Environments** and create the environment.
2. Add three secrets to it: `STRIIM_BASE_URL`, `STRIIM_USERNAME`,
   `STRIIM_PASSWORD`, pointing at that environment's Striim server.
3. Optionally add protection rules (e.g. **required reviewers** on `prod`) for an
   approval gate before production deploys.

### 3. Configure the settings file

Edit [`.github/striim-deploy-settings.yml`](.github/striim-deploy-settings.yml):
set `environment_mapping` (and optionally `namespace_mapping`) for your branches,
and adjust `validation` / `deployment` to taste.

### 4. Add your TQL

Place `.tql` files under an allowed directory (default `striim/TQL/`), following
the naming convention. Declare the namespace in the TQL, the filename, or via
`namespace_mapping` — see [Namespace Resolution](#namespace-resolution).

### 5. Deploy

Push to a mapped branch, or trigger the workflow manually from the **Actions**
tab. Pushing a branch that has no environment mapping (and no manual override)
fails fast with a clear message.

### Adding another target later

Create the environment, add its secrets, and add a `branch: environment` line to
`environment_mapping` (and a `branch: namespace` line to `namespace_mapping` if
you use the mapping fallback).

---

# Python Client Reference

Reference for the `striim_deploy` Python package that powers the deploy
job. You normally don't call this directly — it's invoked by the workflow.

## API Client

### StriimClient

The `StriimClient` class provides a unified interface for all Striim API operations with built-in authentication and error handling.

#### Initialization

```python
from striim_deploy.api.client import StriimClient

client = StriimClient(
    base_url="https://your-striim-instance.com",
    username="your_username",
    password="your_password"
)
```

Authentication happens automatically on initialization when credentials are provided.

#### Parameters

- **base_url** (str): Base URL for the Striim API
- **username** (str, optional): Username for authentication
- **password** (str, optional): Password for authentication
- **logger** (logging.Logger, optional): Custom logger instance

#### Key Features

- Automatic authentication on initialization
- Session management with persistent connections
- Consistent error handling across all requests
- Support for both API v2 and direct endpoints
- Context manager support for automatic cleanup

## Authentication

### Getting a Token

```python
# Authenticate and retrieve token
token = client.authenticate()
if token:
    print(f"Authentication successful: {token}")
```

### Ensuring Authentication

```python
# Check and re-authenticate if necessary
if client.ensure_authenticated():
    print("Client is authenticated")
```

## Core API Operations

### Generic Request

The `request()` method provides a flexible interface for all HTTP operations:

```python
response = client.request(
    method="post",           # HTTP method: get, post, put, delete
    endpoint="tungsten",     # API endpoint
    data={"key": "value"},   # Request payload
    params={"param": "val"}, # Query parameters
    headers={"custom": "header"},  # Additional headers
    timeout=30,              # Request timeout in seconds
    auth_required=True       # Whether authentication is required
)
```

### HTTP Method Shortcuts

#### GET Request

```python
# Get application details
response = client.get(f"applications/{namespace}.{app_name}")

# With query parameters
response = client.get("applications", params={"filter": "status==RUNNING"})
```

#### POST Request

```python
# Create application from TQL (use request() to pass custom headers)
response = client.request(
    "post",
    "tungsten",
    data=tql_content,
    headers={"content-type": "text/plain"}
)

# Create application with JSON payload
response = client.post(
    "applications",
    data={"name": "MyApp", "namespace": "MyNamespace"}
)
```

#### DELETE Request

```python
# Delete an application
response = client.delete(f"applications/{namespace}.{app_name}")

# Stop a running application
response = client.delete(f"applications/{namespace}.{app_name}/sprint")
```

## Application State Management

### AppStateManager

The `AppStateManager` class handles application lifecycle operations and state transitions.

#### Initialization

```python
from striim_deploy.state.manager import AppStateManager

manager = AppStateManager(
    settings=settings,      # SettingsModel instance
    client=client,          # StriimClient instance
    namespace="MyNamespace" # Target namespace
)
```

### Application Status Operations

#### Check Application Status

```python
status = manager.check_app_status(app_name)
# Returns: "CREATED", "DEPLOYED", "RUNNING", "HALT", etc.
```

#### Wait for Specific State

```python
# Wait up to 60 seconds for application to reach DEPLOYED state
success = manager.wait_for_state(
    app_name="MyApp",
    desired_state="DEPLOYED",
    timeout=60
)
```

### Application Lifecycle Operations

#### Deploy Application

```python
success = manager.deploy_application(app_name="MyApp")
```

**API Endpoint:** `POST /api/v2/applications/{namespace}.{app_name}/deployment`

**Purpose:** Deploy a created application to make it ready for execution.

#### Start Application

```python
success = manager.start_application(
    app_name="MyApp",
    max_retries=3  # Optional, defaults from settings
)
```

**API Endpoint:** `POST /api/v2/applications/{namespace}.{app_name}/sprint`

**Purpose:** Start a deployed application and begin processing data.

#### Stop Application

`_stop_application()` is an internal operation. It is not normally called
directly; instead it is dispatched automatically by `prepare_for_deployment()`
based on the configured state transitions.

```python
success = manager._stop_application(
    app_name="MyApp",
    next_state="STOPPED",  # Expected state after stopping
    timeout=60
)
```

**API Endpoint:** `DELETE /api/v2/applications/{namespace}.{app_name}/sprint`

**Purpose:** Stop a running application.

#### Undeploy Application

`_undeploy_application()` is an internal operation. Like `_stop_application()`,
it is dispatched automatically through the state-transition machinery rather
than called directly.

```python
success = manager._undeploy_application(
    app_name="MyApp",
    next_state="CREATED",
    timeout=60
)
```

**API Endpoint:** `DELETE /api/v2/applications/{namespace}.{app_name}/deployment`

**Purpose:** Undeploy an application, returning it to CREATED state.

#### Drop Application

```python
success = manager.drop_application(app_name="MyApp")
```

**API Endpoint:** `DELETE /api/v2/applications/{namespace}.{app_name}`

**Purpose:** Completely remove an application from Striim.

### State Transition Management

#### Prepare for Deployment (full teardown)

`prepare_for_deployment()` walks the entire state-transition matrix automatically, executing each configured action (stop, undeploy, etc.) until the application reaches `CREATED` state and is ready to be recreated:

```python
success = manager.prepare_for_deployment(app_name="MyApp")
# Walks: RUNNING → stop → STOPPED → undeploy → CREATED
```

## Error Handling

### Response Types

The API client returns different response types based on the operation:

#### Successful Response

```python
# Dictionary response (most common)
response = {
    "status": "DEPLOYED",
    "name": "MyApp",
    "namespace": "MyNamespace"
}

# Boolean response (for simple operations)
response = True

# List response (for batch operations)
response = [
    {"executionStatus": "Success", "responseCode": 200},
    {"executionStatus": "Success", "responseCode": 200}
]
```

#### Error Response

```python
# Error dictionary
error = {
    "error": True,
    "status_code": 400,
    "command_errors": [
        {
            "command": "CREATE APPLICATION MyApp...",
            "failure_message": "Application already exists",
            "response_code": 400
        }
    ]
}

# Or with top-level error information
error = {
    "error": True,
    "status_code": 500,
    "execution_status": "Failure",
    "failure_message": "Internal server error"
}
```

### Error Checking

```python
response = client.post("tungsten", data=tql_content)

# Check for errors
if isinstance(response, dict) and response.get("error"):
    # Handle error
    if "command_errors" in response:
        for err in response["command_errors"]:
            print(f"Command failed: {err['failure_message']}")
    else:
        print(f"Error: {response.get('failure_message')}")
elif response:
    # Success
    print("Operation successful")
```

### HALT State Handling

When an application is in HALT state, detailed error information is automatically logged:

```python
status = manager.check_app_status("MyApp")
if status == "HALT":
    # Detailed error information is automatically logged
    # Returns False for any start/deploy operations
    pass
```

## Usage Examples

### Complete Deployment Workflow

```python
from striim_deploy.api.client import StriimClient
from striim_deploy.state.manager import AppStateManager
from striim_deploy.settings.loader import load_settings

# Load settings
settings = load_settings("path/to/settings.yml")

# Initialize client
client = StriimClient(
    base_url="https://striim.example.com",
    username="admin",
    password="password"
)

# Initialize state manager
manager = AppStateManager(
    settings=settings,
    client=client,
    namespace="Production"
)

# Deploy TQL file
with open("myapp.tql", "r") as f:
    tql_content = f.read()

# Add namespace prefix
tql_with_namespace = f"USE Production;\n{tql_content}"

# Create application (use request() to pass custom headers)
response = client.request(
    "post",
    "tungsten",
    data=tql_with_namespace,
    headers={"content-type": "text/plain"}
)

if response:
    app_name = "MyApp"
    
    # Deploy the application
    if manager.deploy_application(app_name):
        print("Application deployed successfully")
        
        # Wait for DEPLOYED state
        if manager.wait_for_state(app_name, "DEPLOYED", timeout=60):
            # Start the application
            if manager.start_application(app_name):
                print("Application started successfully")
```

### Using Context Manager

```python
# Automatic cleanup of session
with StriimClient(base_url=url, username=user, password=pwd) as client:
    response = client.get(f"applications/{namespace}.{app_name}")
    print(f"Application status: {response.get('status')}")
# Session automatically closed
```

### Handling Application Updates

```python
def update_application(client, manager, app_name, new_tql):
    """Update an existing application with new TQL"""
    
    # Check current status
    status = manager.check_app_status(app_name)
    
    # Prepare for deployment (stop/undeploy if needed)
    if not manager.prepare_for_deployment(app_name):
        print(f"Failed to prepare {app_name} for update")
        return False
    
    # Drop existing application
    if not manager.drop_application(app_name):
        print(f"Failed to drop {app_name}")
        return False
    
    # Create updated application
    response = client.post("tungsten", data=new_tql)
    
    if not response:
        print("Failed to create updated application")
        return False
    
    # Deploy and start
    if manager.deploy_application(app_name):
        manager.wait_for_state(app_name, "DEPLOYED", timeout=60)
        return manager.start_application(app_name)
    
    return False
```

### Batch Operations

```python
def deploy_multiple_applications(client, manager, tql_files):
    """Deploy multiple TQL files"""
    results = {}
    
    for tql_file in tql_files:
        app_name = extract_app_name_from_file(tql_file)
        
        with open(tql_file, "r") as f:
            tql_content = f.read()
        
        # Create application
        response = client.post("tungsten", data=tql_content)
        
        if response:
            # Deploy application
            success = manager.deploy_application(app_name)
            results[app_name] = success
        else:
            results[app_name] = False
    
    return results
```

## API Endpoints Reference

### Authentication

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/security/authenticate` | Get authentication token |

### Application Management

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v2/applications/{fqn}` | Get application details and status |
| POST | `/api/v2/tungsten` | Create application from TQL |
| DELETE | `/api/v2/applications/{fqn}` | Drop application |
| POST | `/api/v2/applications/{fqn}/deployment` | Deploy application |
| DELETE | `/api/v2/applications/{fqn}/deployment` | Undeploy application |
| POST | `/api/v2/applications/{fqn}/sprint` | Start application |
| DELETE | `/api/v2/applications/{fqn}/sprint` | Stop application |

**Note:** `{fqn}` represents the fully qualified name: `{namespace}.{app_name}`

## Best Practices

1. **Always use context managers** when possible to ensure proper cleanup
2. **Check response types** before processing (dict, bool, list, or None)
3. **Handle HALT states** explicitly - applications in HALT cannot be started
4. **Use wait_for_state()** after state-changing operations to ensure completion
5. **Implement retry logic** for transient failures (start/deploy operations)
6. **Log error details** from command_errors for better debugging
7. **Validate TQL content** before sending to the API
8. **Use namespace prefixes** consistently across all operations

## Troubleshooting

### Common Issues

**Authentication Failures**
```python
# Check credentials and base URL
if not client.ensure_authenticated():
    print("Authentication failed - check credentials")
```

**Application Not Found**
```python
status = manager.check_app_status(app_name)
if status is None:
    print(f"Application {app_name} does not exist")
```

**State Transition Failures**
```python
# Check current state and transition rules
current_state = manager.check_app_status(app_name)
transitions = settings.get_state_transitions()
print(f"Current: {current_state}")
print(f"Transition rules: {transitions.get(current_state)}")
```

**Timeout Issues**
```python
# Increase timeout for slow operations
manager.wait_for_state(app_name, "DEPLOYED", timeout=300)  # 5 minutes
```
