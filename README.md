# BitNet Docker Image

This project provides a Dockerized build of Microsoft's BitNet runtime using the
`microsoft/BitNet-b1.58-2B-4T-gguf` model from Hugging Face.

The image builds BitNet from source, compiles the CPU runtime, downloads the GGUF
model, prepares the `i2_s` quantized model, and exposes an interactive BitNet
tool agent that can be run directly with Docker.

The tool agent keeps the BitNet runtime intact and adds a small wrapper around
it. The wrapper can answer normal prompts with BitNet, execute local tools
inside the container, and run a repeatable training loop based on compact
synthetic skills.

The goal of this project is not to fine-tune BitNet weights. Instead, it trains
the runtime environment around BitNet: the container stores concise operational
knowledge, asks the model to create shell artifacts, captures the failures, and
turns those failures into small reusable skill snippets.

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
make agent N_PREDICT=1024
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

Start the native interactive BitNet chat:

```sh
make run
```

Equivalent Docker command:

```sh
docker run --rm -it yafb/bitnet:latest conda run --no-capture-output -n bitnet-cpp python run_inference.py -m models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf -n 512 -p "You are a helpful assistant" -cnv
```

Use `/exit` or `Ctrl+C` to leave the session.

To use tool calls and the local `knowledge/` volume, start the tool agent:

```sh
make agent
```

The default Docker command also starts the tool agent:

```sh
docker run --rm -it yafb/bitnet:latest
```

The tool agent opens a `You>` prompt. Use `/exit` or `Ctrl+C` to leave the
session. BitNet is spawned as an internal persistent service on the first LLM
request. The wrapper then keeps that process alive and sends later prompts to
the same session instead of launching a new inference process each time.

At service startup, the wrapper loads compact knowledge from `knowledge/skills/`
once into the initial system prompt. Later game or artifact requests reuse that
already-loaded context.

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

`make run`, `make agent`, `make test`, `make shell`, and `make game` mount the local
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

## Synthetic Skill Training Loop

This repository uses a lightweight training process based on artifacts, not
weight updates. The loop is:

1. Give BitNet a concrete creation task, usually a POSIX shell game or terminal
   artifact.
2. Save the raw model output under `knowledge/generated/`.
3. Extract the runnable shell script and check it with `sh -n`.
4. Inspect the result manually or through reports under `knowledge/reports/`.
5. Identify the smallest missing concept that caused the failure.
6. Add a short, reusable snippet to `knowledge/skills/POSIX.md`.
7. Start a new agent session so the persistent BitNet service loads the updated
   skill once at startup.
8. Regenerate the artifact and compare the behavior.

The skill file should stay synthetic and compact. It is not a long tutorial and
it should not contain large copied programs. Each snippet should encode one
practical correction that BitNet repeatedly misses.

Good skill entries look like this:

```md
### Terminal size
- Read the terminal size with `stty size 2>/dev/null`.
- Fallback to `24 80` when stdout is not a terminal.
- Clamp game coordinates to rows and columns before drawing.
```

```md
### POSIX input loop
- POSIX `read` has no portable `-n` or `-t`.
- Use `stty raw min 0 time 1` and `dd bs=1 count=1 2>/dev/null`.
- Always restore the terminal with `trap 'stty "$old"' EXIT INT TERM`.
```

Bad skill entries are long essays, full generated games, vague advice, or
project-specific fixes that do not generalize.

The purpose is to make BitNet behave like a more experienced POSIX user by
giving it a small set of sharp rules that unlock better generations.

## Interactive Shell

Open a shell inside the image:

```sh
make shell
```

From inside the container, BitNet is available in `/app/BitNet`.

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

Game generation is intentionally used as a stress test. Terminal games expose
model weaknesses quickly because they require portable input handling, terminal
size awareness, redraw loops, collision logic, cleanup traps, and simple state
management. When a generated game fails, the fix should usually become a tiny
skill entry rather than a large hand-written replacement.

Typical gaps to capture:

- Uses Bash features while claiming POSIX sh.
- Ignores terminal rows and columns.
- Leaves the terminal in raw mode after exit.
- Uses blocking input that freezes the game loop.
- Redraws without clearing or positioning the cursor consistently.
- Generates code that passes syntax but cannot run interactively.

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
make run        # Start native BitNet chat
make agent      # Start the BitNet tool agent
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
