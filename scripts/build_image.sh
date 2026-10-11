#!/usr/bin/env bash
#
# Build one image and its bundle.
#
# usage: build_image.sh ENTRY
#
# ENTRY is one image as printed by images.py, in JSON. Run from
# AtomVM/src/platforms/esp32 after the host-side libs build, with:
#   VERSION     version component of the image names (channel or tag)
#   STAMP       ESP-IDF app version (CONFIG_APP_PROJECT_VER)
#   BUILD_DATE  build date, yyyymmdd
#   ATOMVM_SHA  AtomVM commit, recorded in the metadata
#   ATOMGL_SHA  atomgl commit, recorded in the metadata
#   OUT         receives the bundle <stem>.zip and its metadata <stem>.json,
#               where <stem> is the image name followed by -VERSION
#   DIAG        receives <stem>/ with the logs of a failed configure or build

set -Eeuo pipefail

entry=${1:?usage: build_image.sh ENTRY}
: "${VERSION:?}" "${STAMP:?}" "${BUILD_DATE:?}" "${ATOMVM_SHA:?}" "${ATOMGL_SHA:?}" "${OUT:?}" "${DIAG:?}"
factory=$(cd "$(dirname "$0")/.." && pwd)

field() { jq -r --arg key "$1" '.[$key]' <<< "$entry"; }
name=$(field name)
chip=$(field chip)
features=$(field features)
fragments=$(field fragments)
elixir=$(field elixir)
level=$(field level)
stem="$name-$VERSION"

set +u
# shellcheck disable=SC1091
. "${IDF_PATH:?IDF_PATH is not set}/export.sh" >/dev/null
set -u

collect_diag() {
    local diag="$DIAG/$stem"
    mkdir -p "$diag"
    cp -r build/log "$diag/idf-log" 2>/dev/null || true
    cp build/CMakeFiles/CMakeConfigureLog.yaml build/toolchain/cflags build/toolchain/cxxflags \
       sdkconfig sdkconfig.defaults sdkconfig.defaults.in build.log "$diag/" 2>/dev/null || true
    # compiler output of every failed configure-time check
    if [ -f build/CMakeFiles/CMakeConfigureLog.yaml ]; then
        python3 - <<'PY' || true
import yaml
# one document per configure run: set-target, then reconfigure
for log in yaml.safe_load_all(open("build/CMakeFiles/CMakeConfigureLog.yaml")):
    for event in (log or {}).get("events", []):
        result = event.get("buildResult") or {}
        if event.get("kind", "").startswith("try_compile") and result.get("exitCode", 0) != 0:
            print("::group::failed check", result.get("variable"), event.get("checks"))
            print("C flags:", (event.get("cmakeVariables") or {}).get("CMAKE_C_FLAGS"))
            print(result.get("stdout", ""))
            print("::endgroup::")
PY
    fi
}
trap collect_diag ERR

cp sdkconfig.release-defaults.in sdkconfig.defaults.in
fragment_files=()
for fragment in $fragments; do
    fragment_files+=("$factory/$fragment")
done
python3 "$factory/scripts/apply_fragments.py" sdkconfig.defaults.in \
    --set CONFIG_APP_PROJECT_VER_FROM_CONFIG=y \
    --set "CONFIG_APP_PROJECT_VER=\"$STAMP\"" \
    "${fragment_files[@]}"
if [ "$level" = "-Os" ]; then
    # rewrite the release default in place: the choice is assigned once
    if [ "$(grep -c '^CONFIG_COMPILER_OPTIMIZATION_PERF=y$' sdkconfig.defaults.in || true)" != 1 ]; then
        echo "::error::-Os needs exactly one CONFIG_COMPILER_OPTIMIZATION_PERF=y line to rewrite"
        exit 1
    fi
    sed -i 's/^CONFIG_COMPILER_OPTIMIZATION_PERF=y$/CONFIG_COMPILER_OPTIMIZATION_SIZE=y/' sdkconfig.defaults.in
fi
echo "::group::$stem: sdkconfig.defaults.in"
cat sdkconfig.defaults.in
echo "::endgroup::"
case " $features " in
    *" libsodium "*)
        idf.py add-dependency "espressif/libsodium^1.0.20~4"
        cat main/idf_component.yml
        ;;
esac

support=off
if [ "$elixir" = "true" ]; then support=on; fi
flags=("-DATOMVM_ELIXIR_SUPPORT=$support" -DATOMVM_DEV=ON "-DATOMVM_BUILD_METADATA=$BUILD_DATE")
case " $features " in
    *" libsodium "*) flags+=(-DAVM_USE_LIBSODIUM=ON) ;;
esac
# With PSRAM enabled on the ESP32 and ESP32-S3, ESP-IDF compiles everything
# with -mdisable-hardware-atomics (CONFIG_STDATOMIC_S32C1I_SPIRAM_WORKAROUND:
# the S32C1I instruction cannot operate on PSRAM) and provides C11 atomics
# in software. Since ESP-IDF 5.5 that flag is visible to AtomVM's
# configure-time check for lock-free atomics, which then fails, although
# the same software atomics worked with ESP-IDF 5.4 (HAVE_ATOMIC path).
# Pre-seed the check result so that path keeps being used. Drop this once
# upstream accepts ESP-IDF's software atomics in that check.
case "$chip" in
    esp32|esp32s3)
        case " $features " in
            *" psram "*|*" octalpsram "*) flags+=(-DATOMIC_POINTER_LOCK_FREE_IS_TWO=1) ;;
        esac
        ;;
esac
"$factory/scripts/build_esp32.sh" "$chip" "${flags[@]}"
trap - ERR

flavor=""
if [ "$elixir" = "true" ]; then flavor="-elixir"; fi
(cd build && ./mkimage.sh && mv "atomvm-$chip$flavor.img" "$stem.img")
ls -l "build/$stem.img"

epoch=$(date -u -d "$BUILD_DATE" +%s)
python3 "$factory/scripts/make_bundle.py" \
    --stem "$stem" --chip "$chip" --img "build/$stem.img" --sdkconfig sdkconfig \
    --partitions-dir . --build-dir build \
    --source-date-epoch "$epoch" --out "$OUT"
escript "$factory/scripts/verify_bundle.escript" "$OUT/$stem.zip" "$stem"
unzip -l "$OUT/$stem.zip"
echo "::group::$stem: FLASH.txt"
unzip -p "$OUT/$stem.zip" FLASH.txt
echo "::endgroup::"

optimization=$(sed -n 's/^CONFIG_COMPILER_OPTIMIZATION_\(DEBUG\|SIZE\|PERF\|NONE\)=y$/\1/p' sdkconfig)
idf_version=$(idf.py --version | tail -n1)
elf_sha256=$(sha256sum build/atomvm-esp32.elf | cut -d' ' -f1)
sha256=$(sha256sum "$OUT/$stem.zip" | cut -d' ' -f1)
size=$(stat -c %s "$OUT/$stem.zip")
jq -n \
    --arg stem "$stem" --arg chip "$chip" --arg features "$features" --arg elixir "$elixir" \
    --arg optimization "$optimization" --arg sha256 "$sha256" --arg size "$size" \
    --arg atomvm_sha "$ATOMVM_SHA" --arg atomgl_sha "$ATOMGL_SHA" \
    --arg idf_version "$idf_version" --arg stamp "$STAMP" --arg elf_sha256 "$elf_sha256" \
    '{stem: $stem, chip: $chip, features: $features, elixir: ($elixir == "true"),
      optimization: $optimization, sha256: $sha256, size: ($size | tonumber),
      atomvm_sha: $atomvm_sha, atomgl_sha: $atomgl_sha, idf_version: $idf_version, stamp: $stamp,
      elf_sha256: $elf_sha256}' \
    > "$OUT/$stem.json"
cat "$OUT/$stem.json"
