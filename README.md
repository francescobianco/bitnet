# BitNet Docker Image

This project provides a Dockerized build of Microsoft's BitNet runtime using the
`microsoft/BitNet-b1.58-2B-4T-gguf` model from Hugging Face.

The image builds BitNet from source, compiles the CPU runtime, downloads the GGUF
model, prepares the `i2_s` quantized model, and exposes a default inference
command that can be run directly with Docker.

## Image

Default image name:

```sh
yafb/bitnet:latest
```

The tag can be changed at build time through the Makefile variables:

```sh
make build IMAGE=yafb/bitnet TAG=latest
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

Run the default inference command:

```sh
make run
```

Equivalent Docker command:

```sh
docker run --rm yafb/bitnet:latest
```

The default command runs a short non-interactive prompt so the container starts,
loads the model, generates text, and exits.

## Test

Build and run the image:

```sh
make test
```

This target verifies that the Dockerfile builds successfully and that the
container can load the model and run inference.

## Interactive Shell

Open a shell inside the image:

```sh
make shell
```

From inside the container, BitNet is available in `/app/BitNet`.

## Interactive Chat

The default container command is intentionally non-interactive. To start BitNet
in conversation mode, override the Docker command:

```sh
docker run --rm -it yafb/bitnet:latest \
  conda run --no-capture-output -n bitnet-cpp \
  python run_inference.py \
  -m models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf \
  -p "You are a helpful assistant" \
  -cnv
```

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
make run        # Run the default inference command
make test       # Build and run the image
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
