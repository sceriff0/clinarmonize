# clinarmonize-duckdb

The runtime for the nine `modules/local/*` processes: python3 with **duckdb
1.5.5** and **PyYAML 6.0.2** baked in, plus `bash` and `procps` (`ps`), which
Nextflow's task wrapper calls for resource-metrics collection before the
process script runs.

## Why this exists

The modules previously pinned
`wave.seqera.io/wt/211e562aa32e/wave/build@sha256:af953fd9…`. `nextflow.config`
set `wave.freeze = true` with **no `wave.build.repository`**, so the frozen
image lived only in Wave's own store under `/wt/<token>/`. That token stopped
resolving within four days of being pinned — the registry returns `404` for it,
against `401` for images that do exist — which took out every containerised
test on the 2026-08-17 cluster run.

A digest guarantees *these exact bits*. It never guarantees *these bits still
exist*. A pin is only as durable as the cheapest place someone can still fetch
it from.

## Layout

| File | Role |
|---|---|
| `environment.yml` | **Canonical** conda spec — the single source of truth |
| `Dockerfile` | Builds the image *from* `environment.yml` |
| `../../modules/local/*/environment.yml` | Verbatim copies, for `-profile conda` |

`tools/verify_clinarmonize.sh` fails the run if the copies drift from the
canonical file. Without that check, `-profile conda` and `-profile singularity`
can quietly run different software and the suite cannot tell you which one it
proved anything about.

## Build

The cluster is `x86_64`, so the image must be `linux/amd64`.

```bash
docker buildx build --platform linux/amd64 \
  -t docker.io/bolt3x/clinarmonize-duckdb:1.5.5_pyyaml6.0.2 \
  --load containers/duckdb-pyyaml
```

> On Apple Silicon this needs working `linux/amd64` emulation. If an
> `docker run --platform linux/amd64 alpine uname -m` hangs, emulation is
> broken and the build will hang at the first `RUN` — build on an x86_64
> machine or in CI instead. Docker Desktop ≥ 4.25 with Rosetta enabled is the
> usual local fix.

The Dockerfile asserts `duckdb.__version__`, `yaml.__version__`, and the
presence of `ps` **at build time**, so a broken image fails here rather than on
the cluster.

## Push, then pin

```bash
docker push docker.io/bolt3x/clinarmonize-duckdb:1.5.5_pyyaml6.0.2
./tools/pin_container.sh docker.io/bolt3x/clinarmonize-duckdb:1.5.5_pyyaml6.0.2
```

`pin_container.sh` reads the digest back **from the registry** (pushing can
rewrite a manifest, so local and remote digests need not agree) and rewrites
all nine modules to the digest-only form. It refuses to finish if any module is
left on a tag.

**Keep the repository public.** Otherwise Apptainer needs
`APPTAINER_DOCKER_USERNAME` / `APPTAINER_DOCKER_PASSWORD`; the cluster run had
the `SINGULARITY_*` variants set, which Apptainer ignores with a warning.

## Never reintroduce a tag *and* a digest

`repo:tag@sha256:…` is legal OCI, but `singularity pull` refuses it outright —
*"Docker references with both a tag and digest are currently not supported"*.
That alone accounted for 30 of the 40 failures on the 2026-08-17 run. Pin with
the digest only.
