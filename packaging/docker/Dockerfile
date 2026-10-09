FROM scratch

ARG TARGETARCH
COPY --chmod=0555 ${TARGETARCH}/lcovmerge /lcovmerge

ENTRYPOINT ["/lcovmerge"]
