# syntax=docker/dockerfile:1
# NInfer-3090 ( https://github.com/ashalliants/ninfer-3090 ): wraps the prebuilt Linux release rather than compiling it.
FROM ubuntu:24.04 AS fetch
ARG NINFER_VERSION=0.17.1
# from SHA256SUMS-v${NINFER_VERSION}-linux.txt on the release page
ARG NINFER_SHA256=2f469d9171fa9a1e6fca1de85f6a5d5cfb7326302f69a0561dcd9ef81415febb
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
RUN curl -fsSLO "https://github.com/ashalliants/ninfer-3090/releases/download/v${NINFER_VERSION}-rtx3090/ninfer-rtx3090-linux-x64-${NINFER_VERSION}-rtx3090.tar.gz" \
 && echo "${NINFER_SHA256}  ninfer-rtx3090-linux-x64-${NINFER_VERSION}-rtx3090.tar.gz" | sha256sum -c - \
 && mkdir /opt/ninfer \
 && tar xzf ninfer-*.tar.gz --strip-components=1 -C /opt/ninfer \
 && cd /opt/ninfer && sha256sum -c SHA256SUMS.txt

FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tini libcurl4t64 libzstd1 \
      libavcodec60 libavformat60 libavutil58 libswscale7 \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/cuda*/compat
COPY --from=fetch /opt/ninfer /opt/ninfer
# fail the build if a shared library is missing ( libcuda comes from the host driver at run time )
RUN if ldd /opt/ninfer/ninfer-serve | grep 'not found' | grep -v libcuda; then exit 1; fi
ENV NINFER_HOST=0.0.0.0 NINFER_MODEL_DIR=/models
WORKDIR /opt/ninfer
EXPOSE 8080
STOPSIGNAL SIGTERM
# tini forwards SIGTERM to run.sh's process group; run.sh pipes the server through tee, so as PID 1 it would swallow the signal
ENTRYPOINT ["tini", "-g", "--", "/opt/ninfer/run.sh"]
CMD ["qwen38-27b"]
