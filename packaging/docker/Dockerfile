FROM scratch

ARG TARGETARCH
COPY --chmod=0555 ${TARGETARCH}/lcovmerge /lcovmerge
COPY --chmod=1777 tmp/ /tmp/

ENTRYPOINT ["/lcovmerge"]
