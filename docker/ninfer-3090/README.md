# ninfer-3090

A container for [NInfer-3090](https://github.com/ashalliants/ninfer-3090), a single-GPU C++/CUDA inference engine for the RTX 3090 that serves Qwen3.8-27B ( OpenAI and Anthropic compatible APIs ).
No image is published for it, so this wraps the prebuilt Linux release tarball ( CUDA 12.8 runtime, Ubuntu 24.04 ) instead of compiling it. The model is **not** in the image.

## Build

Run from WSL, in this directory:

```
docker build -t docker-releases.nexus3.dev.randomnoun/ninfer-3090:0.17.1 .
```

The build downloads the release from github.com and checks it against `NINFER_SHA256`. To use another release, change both build args ( the hash is in the release's `SHA256SUMS-v<version>-linux.txt` ):

```
docker build --build-arg NINFER_VERSION=0.17.2 --build-arg NINFER_SHA256=<sha256> -t ...:0.17.2 .
```

The `ldd` line fails the build if the binary needs a library the image lacks. If it does, add the package it names to the `apt-get install` line.

## Check it

Without a GPU, check that every library the server needs is in the image. 
Check by running the following: 

```
docker run --rm --entrypoint ldd docker-releases.nexus3.dev.randomnoun/ninfer-3090:0.17.1 /opt/ninfer/ninfer-serve | grep 'not found'
```

only `libcuda.so.1` should be missing; it's the driver's, and the NVIDIA container runtime will add it from the host when a container is given a GPU.

Running the server itself, even `--version`, needs the driver library, so it needs a GPU ( `--gpus all`, on a machine with the NVIDIA driver and container toolkit ).
My laptop conveniently also has an nvidia GPU, so this worked locally:

```
docker run --rm --gpus all --entrypoint /opt/ninfer/ninfer-serve docker-releases.nexus3.dev.randomnoun/ninfer-3090:0.17.1 --version
```

## Push

```
docker login docker-releases.nexus3.dev.randomnoun
docker push docker-releases.nexus3.dev.randomnoun/ninfer-3090:0.17.1
```

If the largest layer times out, push with `skopeo` as for sonar ( see [SETUP-LLM-BE-SONAR.md](../../setup/SETUP-LLM-BE-SONAR.md#build-the-image ) ):

```
docker save -o ninfer-3090.tar docker-releases.nexus3.dev.randomnoun/ninfer-3090:0.17.1
skopeo copy --dest-creds USER:PASS docker-archive:ninfer-3090.tar docker://docker-releases.nexus3.dev.randomnoun/ninfer-3090:0.17.1
```

## Running it

The entrypoint is the release's `run.sh`, and the default command is its recommended profile, `qwen38-27b`. `run.sh` is configured by `NINFER_*` environment variables
( context length, speculation, API key and so on; they're listed at the top of `run.sh` in the release ). The image sets `NINFER_HOST=0.0.0.0` and `NINFER_MODEL_DIR=/models`.

* **Model:** about 19 GB, from `/models/qwen3_8_27b.ninfer`. Mount a volume there, and fetch it into the volume with the release's own downloader ( resumable, checks size and SHA-256 ):
  `docker run --rm --entrypoint /opt/ninfer/download-model.sh -v ninfer-models:/models ...:0.17.1 qwen38-27b`
* **GPU:** one RTX 3090 only. `docker run --gpus '"device=0"' -p 8080:8080 -v ninfer-models:/models ...:0.17.1`
* **No authentication by default.** Set `NINFER_API_KEY` if the port is reachable from anywhere you don't trust.
* **Memory:** the default profile pins 8 GiB of host RAM for its context cache ( `NINFER_HOST_CONTEXT_MIB` ), so allow well over that to the container.
* **Host driver:** the image deletes CUDA's forward-compat libraries, so the container uses the host's driver, which has to support CUDA 12.8.1 ( driver 570.124.06 or newer; bnenod05's 580 does ).

Not yet tried on `bnenod05`. The release notes say only a chat completion was tested on Linux, on a rented 3090.
