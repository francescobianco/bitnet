IMAGE ?= yafb/bitnet
TAG ?= latest
FULL_IMAGE := $(IMAGE):$(TAG)
DOCKER ?= docker
N_PREDICT ?= 512
KNOWLEDGE_DIR ?= $(CURDIR)/knowledge
DOCKER_RUN = $(DOCKER) run --rm -e N_PREDICT=$(N_PREDICT) -e KNOWLEDGE_DIR=/knowledge -v $(KNOWLEDGE_DIR):/knowledge

.PHONY: help build run test smoke-test game shell push pull tag-local clean git-push

help:
	@printf "Available targets:\n"
	@printf "  make build      Build $(FULL_IMAGE)\n"
	@printf "  make run        Run $(FULL_IMAGE)\n"
	@printf "  make test       Build and smoke-test $(FULL_IMAGE)\n"
	@printf "  make smoke-test Run a local tool-call smoke test\n"
	@printf "  make game       Generate and analyze a POSIX sh game\n"
	@printf "  make shell      Open a shell inside $(FULL_IMAGE)\n"
	@printf "  make push       Push $(FULL_IMAGE) to the registry\n"
	@printf "  make pull       Pull $(FULL_IMAGE) from the registry\n"
	@printf "  make clean      Remove local $(FULL_IMAGE)\n"

build:
	$(DOCKER) build -t $(FULL_IMAGE) .

run:
	$(DOCKER_RUN) -it $(FULL_IMAGE)

test: build smoke-test

smoke-test:
	printf '/ls /knowledge\n/sh pwd\n/exit\n' | $(DOCKER_RUN) -i $(FULL_IMAGE)

game:
	printf '/game $(or $(GAME),demo) $(or $(PROMPT),small terminal reflex game)\n/exit\n' | $(DOCKER_RUN) -i $(FULL_IMAGE)

shell:
	$(DOCKER_RUN) -it --entrypoint /bin/bash $(FULL_IMAGE)

push:
	$(DOCKER) push $(FULL_IMAGE)

pull:
	$(DOCKER) pull $(FULL_IMAGE)

tag-local:
	$(DOCKER) tag bitnet-local $(FULL_IMAGE)

clean:
	-$(DOCKER) rmi $(FULL_IMAGE)

git-push:
	@git add .
	@git commit -m "update"
	@git push
