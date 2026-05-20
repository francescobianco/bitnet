# BitNet Docker Image

This project provides a Dockerized build of Microsoft's BitNet runtime using the
`microsoft/BitNet-b1.58-2B-4T-gguf` model from Hugging Face.

The image builds BitNet from source, compiles the CPU runtime, downloads the GGUF
model, prepares the `i2_s` quantized model, and exposes an interactive BitNet
tool agent that can be run directly with Docker.

The tool agent keeps the BitNet runtime intact and adds a small wrapper around
it. The wrapper can answer normal prompts with BitNet and can also execute a
small set of local, allowlisted tools inside the container.

## Image

Default image name:

```sh
yafb/bitnet:latest
```

The tag can be changed at build time through the Makefile variables:

```sh
make build IMAGE=yafb/bitnet TAG=latest
```

The generation length passed to BitNet can be changed with:

```sh
make run N_PREDICT=1024
```

## Requirements

You need:

- Docker
- Make
- Internet access during the first build
- Access to Docker Hub if you want to push `yafb/bitnet:latest`

The first build downloads several large dependencies, including LLVM, Miniforge,
Python packages, the BitNet repository, and the model files. Docker cache is used
for subsequent builds.

## Build

Build the Docker image:

```sh
make build
```

Equivalent Docker command:

```sh
docker build -t yafb/bitnet:latest .
```

## Run

Start an interactive BitNet tool-agent session:

```sh
make run
```

Equivalent Docker command:

```sh
docker run --rm -it yafb/bitnet:latest
```

The default command opens the BitNet tool agent. Use `/exit` or `Ctrl+C` to
leave the session.

Example:

```text
You> dammi la lista dei file nel disco

[tool:list_files]
Listing /app/BitNet:
3rdparty/
assets/
...
```

## Test

Build and run the image:

```sh
make test
```

This target verifies that the Dockerfile builds successfully and runs a local
tool-call smoke test.

## Knowledge Volume

`make run`, `make test`, `make shell`, and `make game` mount the local
`knowledge/` directory into the container at `/knowledge`.

This lets the tool agent read compact skills from:

```sh
knowledge/skills/
```

and write captured model outputs to:

```sh
knowledge/generated/
knowledge/reports/
```

## Interactive Shell

Open a shell inside the image:

```sh
make shell
```

From inside the container, BitNet is available in `/app/BitNet`.

## Direct Docker Agent

The default container command starts the interactive BitNet tool agent:

```sh
docker run --rm -it yafb/bitnet:latest
```

## Local Tools

The wrapper supports a small allowlist of local tools:

```sh
/ls [path]       # list files
/cat <path>      # read a text file, truncated for safety
/find <name>     # find files by name
/sh <command>    # run a shell command inside the container
!<command>       # shortcut for /sh
/game <name>     # generate, save, and analyze a POSIX sh game
pwd              # show the current directory
/help            # show commands
/exit            # quit
```

Natural-language requests such as `dammi la lista dei file nel disco` are routed
to the same local tools when they match a supported intent.

Shell commands run inside the Docker container with `/app/BitNet` as the working
directory. Command execution has a 30-second timeout and output is truncated.
Mounted host paths are still reachable from inside the container, so avoid
mounting sensitive directories when using the shell tool.

## POSIX Game Generation

The agent can ask BitNet to create a POSIX shell game, capture the raw model
output, extract the script, and write a short analysis report:

```sh
make game GAME=snake PROMPT="small snake-like terminal game"
```

Outputs:

```sh
knowledge/generated/snake.raw.txt
knowledge/generated/snake.sh
knowledge/reports/snake.md
```

The analysis checks common POSIX blockers such as Bash shebangs, arrays,
`[[ ... ]]`, `read -n`, `read -t`, `$RANDOM`, and `sh -n` syntax failures.

## Push

Push the image to the registry:

```sh
make push
```

Equivalent Docker command:

```sh
docker push yafb/bitnet:latest
```

Make sure you are logged in first:

```sh
docker login
```

## Makefile Targets

```sh
make help       # Show available commands
make build      # Build yafb/bitnet:latest
make run        # Start the interactive BitNet tool agent
make test       # Build and run a local tool-call smoke test
make smoke-test # Run the local tool-call smoke test
make game       # Generate and analyze a POSIX sh game
make shell      # Open a shell inside the image
make push       # Push the image to Docker Hub
make pull       # Pull the image from Docker Hub
make clean      # Remove the local image
make git-push   # Commit and push repository changes
```

## Notes

- The image is CPU-oriented.
- GPU offload is not enabled in this Dockerfile.
- The build currently patches one BitNet source line during image creation to
  satisfy newer Clang const-correctness checks.
- Hugging Face downloads are unauthenticated by default. Set up authentication
  separately if you need higher rate limits.
