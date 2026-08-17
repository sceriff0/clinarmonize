# syntax=docker/dockerfile:1.7
#
# The profiling/ledger runtime for clinarmonize's nine local modules.
#
# Replaces wave.seqera.io/wt/211e562aa32e/wave/build@sha256:af953fd9..., which
# was a Wave *ephemeral* build: nextflow.config set `wave.freeze = true` with no
# `wave.build.repository`, so the frozen image lived only in Wave's own store
# under /wt/<token>/. That token stopped resolving within four days of being
# pinned (HTTP 404 from the registry, against 401 for images that do exist),
# taking every containerised test on the cluster with it. A digest is only as
# durable as the cheapest place someone can still fetch it from.
#
# Base pinned by digest, not tag: mambaorg/micromamba:2.0.5 is a moving target,
# the digest is not.
FROM mambaorg/micromamba:2.0.5@sha256:c6c6daf6d2dbeb3be78b0bce740f19dbdbd51422e1dd9e56439efec0dbcdb9bb

# Built from the same environment.yml the modules declare via
# `conda "${moduleDir}/environment.yml"`. One spec, two delivery mechanisms.
COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml /tmp/environment.yml

# MAMBA_DOCKERFILE_ACTIVATE makes the base env active for subsequent RUN steps;
# without it micromamba installs correctly but the assertions below run against
# the system python and pass or fail for the wrong reasons.
ARG MAMBA_DOCKERFILE_ACTIVATE=1
RUN micromamba install -y -n base -f /tmp/environment.yml \
    && micromamba clean --all --yes \
    && rm /tmp/environment.yml

# Nextflow does not use the image ENTRYPOINT. Under `singularity exec` the
# task wrapper is invoked directly, so any image that relies on an activation
# hook to put conda on PATH resolves the WRONG python and fails in a way that
# looks like a code bug. Setting PATH explicitly is what makes this image
# usable from Nextflow at all.
ENV PATH=/opt/conda/bin:$PATH

# Assert at build time rather than discovering it on the cluster. conda-forge
# ships the duckdb Python bindings under the plain `duckdb` name; if that ever
# changes, this build fails here instead of producing an image that imports
# nothing. `ps` is checked because Nextflow's trace collection needs it.
RUN python -c "import duckdb, yaml, sys; \
assert duckdb.__version__ == '1.5.5', 'duckdb ' + duckdb.__version__; \
assert yaml.__version__ == '6.0.2', 'pyyaml ' + yaml.__version__; \
print('duckdb', duckdb.__version__, '| pyyaml', yaml.__version__, '| python', sys.version.split()[0])" \
    && command -v bash && command -v ps \
    && ps --version

LABEL org.opencontainers.image.title="clinarmonize-duckdb" \
      org.opencontainers.image.description="duckdb + pyyaml runtime for clinarmonize local modules" \
      org.opencontainers.image.source="https://github.com/bolt3x/clinarmonize" \
      org.opencontainers.image.licenses="MIT"
