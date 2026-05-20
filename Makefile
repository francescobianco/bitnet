IMAGE ?= yafb/bitnet
TAG ?= latest
FULL_IMAGE := $(IMAGE):$(TAG)
DOCKER ?= docker

.PHONY: help build run test shell push pull tag-local clean git-push

help:
	@printf "Available targets:\n"
	@printf "  make build      Build $(FULL_IMAGE)\n"
	@printf "  make run        Run $(FULL_IMAGE)\n"
	@printf "  make test       Build and run $(FULL_IMAGE)\n"
	@printf "  make shell      Open a shell inside $(FULL_IMAGE)\n"
	@printf "  make push       Push $(FULL_IMAGE) to the registry\n"
	@printf "  make pull       Pull $(FULL_IMAGE) from the registry\n"
	@printf "  make clean      Remove local $(FULL_IMAGE)\n"

build:
	$(DOCKER) build -t $(FULL_IMAGE) .

run:
	$(DOCKER) run --rm -it $(FULL_IMAGE)

test: build run

shell:
	$(DOCKER) run --rm -it --entrypoint /bin/bash $(FULL_IMAGE)

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
