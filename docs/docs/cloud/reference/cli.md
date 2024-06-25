# LangGraph CLI
The LangGraph CLI includes commands to build and run a LangGraph Cloud API server locally in [Docker](https://www.docker.com/). For development and testing, use the CLI to deploy a local API server.

## Installation
1. Ensure that Docker is installed (e.g. `docker --version`).
1. Install the `langgraph-cli` Python package (e.g. `pip install langgraph-cli`).
1. Run the command `langgraph --help` to confirm that the CLI is installed.

## Configuration File
The LangGraph CLI requires a JSON configuration file with the following keys:

| Key | Description |
| --- | ----------- |
| `dependencies` | **Required**. Array of dependencies for LangGraph Cloud API server. Dependencies can be one of the following: (1) `"."`, which will look for local Python packages, (2) `pyproject.toml`, `setup.py` or `requirements.txt` in the app directory `"./local_package"`, or (3) a package name. |
| `graphs` | **Required**. Mapping from graph ID to path where the compiled graph is defined. Example: `./your_package/your_file.py:variable`, where `variable` is an instance of `langgraph.graph.graph.CompiledGraph`. |
| `env` | Path to `.env` file or a mapping from environment variable to its value. |
| `python_version` | `3.11` or `3.12`. Defaults to `3.11`. |
| `pip_config_file`| Path to `pip` config file. |
| `dockerfile_lines` | Array of additional lines to add to Dockerfile following the import from parent image. |

<div class="admonition tip">
    <p class="admonition-title">Note</p>
    <p>
        The LangGraph CLI defaults to using the configuration file <strong>langgraph.json</strong> in the current directory.
    </p>
</div>

Example:
```json
{
    "dependencies": [
        "langchain_openai",
        "./your_package"
    ],
    "graphs": {
        "my_graph_id": "./your_package/your_file.py:variable"
    },
    "env": "./.env"
}
```

Example:
```json
{
    "python_version": "3.11",
    "dependencies": [
        "langchain_openai",
        "."
    ],
    "graphs": {
        "my_graph_id": "./your_package/your_file.py:variable"
    },
    "env": {
        "OPENAI_API_KEY": "secret-key"
    }
}
```

## Commands
The base command for the LangGraph CLI is `langgraph`.

**Usage**
```
langgraph [OPTIONS] COMMAND [ARGS]
```

### `build`
Build LangGraph Cloud API server Docker image.

**Usage**
```
langgraph build [OPTIONS]
```

**Options**

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--platform TEXT` | | Target platform(s) to build the Docker image for. Example: `langgraph build --platform linux/amd64,linux/arm64` |
| `-t, --tag TEXT` | | **Required**. Tag for the Docker image. Example: `langgraph build -t my-image` |
| `--pull / --no-pull` | `--pull` | Build with latest remote Docker image. Use `--no-pull` for running the LangGraph Cloud API server with locally built images. |
| `-c, --config FILE` | `langgraph.json` | Path to configuration file declaring dependencies, graphs and environment variables. |
| `--help` | | Display command documentation. |

### `down`
Stop LangGraph Cloud API server.

**Usage**
```
langgraph down [OPTIONS]
```

**Options**

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--debugger-port INTEGER` | | Pull the debugger image locally and serve the UI on specified port. |
| `--verbose` | | Show more output from the server logs. |
| `-c, --config FILE` | `langgraph.json` | Path to configuration file declaring dependencies, graphs and environment variables. |
| `-d, --docker-compose FILE` | | Advanced. Path to `docker-compose.yml` file with additional services to launch. |
| `-p, --port INTEGER` | `8123` | Port to expose. Example: `langgraph up --port 8000` |
| `--help` | | Display command documentation. |

### `logs`
Show LangGraph Cloud API server logs.

**Usage**
```
langgraph logs [OPTIONS]
```

**Options**

| Option | Default | Description |
| ------ | ------- | ----------- |
| `-f, --follow` | | Follow logs. |
| `-c, --config FILE` | `langgraph.json` | Path to configuration file declaring dependencies, graphs and environment variables. |
| `-d, --docker-compose FILE` | | Advanced. Path to `docker-compose.yml` file with additional services to launch. |
| `--help` | | Display command documentation. |

### `up`
Start LangGraph Cloud API server.

**Usage**
```
langgraph up [OPTIONS]
```

**Options**

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--wait` | | Wait for services to start before returning. Implies `--detach`. |
| `--watch` | | Restart on file changes. |
| `--debugger-port INTEGER` | | Pull the debugger image locally and serve the UI on specified port. |
| `--verbose` | | Show more output from the server logs. |
| `-c, --config FILE` | `langgraph.json` | Path to configuration file declaring dependencies, graphs and environment variables. |
| `-d, --docker-compose FILE` | | Advanced. Path to `docker-compose.yml` file with additional services to launch. |
| `-p, --port INTEGER` | `8123` | Port to expose. Example: `langgraph up --port 8000` |
| `--pull / --no-pull` | `--pull` | Build with latest remote Docker image. Use `--no-pull` for running the LangGraph Cloud API server with locally built images. |
| `--recreate / --no-recreate` | `--no-recreate` | Recreate containers even if their configuration and image haven't changed. |
| `--help` | | Display command documentation. |
