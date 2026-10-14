FROM scratch

# The release pipeline compiles these Rust executables for musl targets.

ARG TARGETARCH
COPY --chmod=0555 ${TARGETARCH}/lcovmerge /lcovmerge
COPY --chmod=1777 tmp/ /tmp/

ENTRYPOINT ["/lcovmerge"]
