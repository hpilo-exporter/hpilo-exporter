FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

ENV DEBIAN_FRONTEND noninteractive

# Install the xz-utils package
RUN apt-get update && \
    apt-get install -y --no-install-recommends ack entr  && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
