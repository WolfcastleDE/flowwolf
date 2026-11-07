# syntax=docker/dockerfile:1.7
FROM rust:1-bookworm AS build
# Optional cargo features, e.g. --build-arg FEATURES=python
ARG FEATURES=""
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src src
COPY web web
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked --features "$FEATURES" && cp target/release/flowwolf /flowwolf && mkdir -p /data

# distroless/cc ships glibc, CA certificates and tzdata — nothing else (no shell).
FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /flowwolf /usr/local/bin/flowwolf
COPY --from=build --chown=nonroot:nonroot /data /data
ENV FLOWWOLF_DATA_DIR=/data \
    FLOWWOLF_PORT=5678 \
    RUST_LOG=info
VOLUME /data
EXPOSE 5678
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s CMD ["/usr/local/bin/flowwolf", "healthcheck"]
ENTRYPOINT ["/usr/local/bin/flowwolf"]
