#!/usr/bin/env bash
# Pin modules/local/*/main.nf to an immutable digest for IMAGE.
#
#   ./tools/pin_container.sh docker.io/bolt3x/clinarmonize-duckdb:1.5.5_pyyaml6.0.2
#
# Run this AFTER `docker push`. A tag is a mutable pointer -- pinning to one
# means "whatever is behind this name today", which is exactly the property a
# pipeline built on sealed ledgers and hash-matched locks must not have.
#
# The digest is read back from the REGISTRY, not the local image: pushing can
# re-compress or rewrite a manifest, so local and remote digests are not
# guaranteed to agree. The remote one is what Nextflow will resolve.
set -euo pipefail

IMAGE="${1:-}"
[[ -n "$IMAGE" ]] || { echo "usage: $0 <registry/repo:tag>" >&2; exit 2; }
[[ "$IMAGE" == *:* ]] || { echo "FATAL: pass a tagged reference, e.g. repo:tag" >&2; exit 2; }
REPO="${IMAGE%:*}"

command -v docker >/dev/null || { echo "FATAL: docker not found" >&2; exit 1; }

# Which platform's manifest to pin when the reference resolves to an OCI index.
PLATFORM="${PLATFORM:-linux/amd64}"

echo "resolving digest for $IMAGE from the registry ..."
DIGEST="$(docker buildx imagetools inspect "$IMAGE" --format '{{.Manifest.Digest}}' 2>/dev/null || true)"

# If that digest names an INDEX rather than an image, pin the platform-specific
# child instead. buildx publishes attestations (provenance/SBOM) as extra
# manifests inside the index, tagged platform unknown/unknown, and
# Apptainer/Singularity fails to resolve an image from such an index -- it
# pushes and inspects cleanly, then will not pull on the cluster. Pinning the
# child manifest sidesteps that entirely and is strictly more precise: it names
# one image for one platform, not a set to choose from.
RAW="$(docker buildx imagetools inspect "$IMAGE" --raw 2>/dev/null || true)"
if [[ -n "$RAW" ]]; then
  CHILD="$(printf '%s' "$RAW" | PLATFORM="$PLATFORM" python3 -c '
import json, os, sys
want_os, want_arch = os.environ["PLATFORM"].split("/", 1)
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for m in d.get("manifests", []):
    p = m.get("platform", {})
    if p.get("os") == want_os and p.get("architecture") == want_arch:
        print(m["digest"]); break
' 2>/dev/null || true)"
  if [[ "$CHILD" == sha256:* && "$CHILD" != "$DIGEST" ]]; then
    echo "NOTE: $IMAGE is a multi-manifest index; pinning its $PLATFORM image instead."
    DIGEST="$CHILD"
  fi
fi
if [[ -z "$DIGEST" ]]; then
  DIGEST="$(docker image inspect "$IMAGE" --format '{{range .RepoDigests}}{{.}}{{"\n"}}{{end}}' 2>/dev/null \
            | grep "^${REPO}@" | head -1 | cut -d@ -f2 || true)"
  [[ -n "$DIGEST" ]] && echo "NOTE: registry lookup failed; using the local RepoDigest recorded at push."
fi
[[ "$DIGEST" == sha256:* ]] || {
  echo "FATAL: could not resolve a digest for $IMAGE." >&2
  echo "       Has it been pushed? Try: docker push $IMAGE" >&2
  exit 1; }

PINNED="${REPO}@${DIGEST}"
echo "pinning -> $PINNED"

# Values reach perl through the ENVIRONMENT, never interpolated into its
# source. A digest contains '@sha256', and perl reads a bare @sha256 in an
# interpolating string as an array -- which expands to nothing and silently
# writes "repo:<hex>" in place of "repo@sha256:<hex>". Caught in test; the
# script reported success while every module was left mispinned.
n=0
for f in modules/local/*/main.nf; do
  # Matches the tagged form OR an already-pinned digest, so re-running after a
  # rebuild is idempotent rather than a no-op that leaves half the modules
  # pointing at the previous image.
  REPO="$REPO" PINNED="$PINNED" perl -0pi \
    -e 's{container "\Q$ENV{REPO}\E(?::[^"]*|\@sha256:[0-9a-f]{64})?"}{container "$ENV{PINNED}"}g' \
    "$f"
  if grep -q "container \"${PINNED}\"" "$f"; then n=$((n+1)); fi
done

TOTAL=$(ls -1 modules/local/*/main.nf | wc -l | tr -d ' ')
echo "pinned $n/$TOTAL modules"
[[ "$n" == "$TOTAL" ]] || { echo "FATAL: $((TOTAL-n)) module(s) did not update -- inspect them by hand." >&2; exit 1; }

if grep -rn "container \"${REPO}:" modules/local/*/main.nf; then
  echo "FATAL: tagged references remain (listed above)." >&2; exit 1
fi
echo "OK: all $TOTAL modules pinned to $DIGEST"
