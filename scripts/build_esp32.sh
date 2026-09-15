#!/usr/bin/env bash
#
# Build the AtomVM ESP32 firmware for one image.
#
# usage: build_esp32.sh <soc> [idf.py -D flags...]
#
# Run from AtomVM/src/platforms/esp32 after the host-side libs build, with
# sdkconfig.defaults.in already prepared (release defaults + feature fragments).
#
# The optimization level is whatever sdkconfig.defaults.in selects (upstream's
# release defaults choose CONFIG_COMPILER_OPTIMIZATION_PERF, -O2); it is never
# changed automatically. If the app does not fit the factory partition the
# build fails, and a human tunes the optimization level explicitly.
#
# Prints "optimization=<DEBUG|SIZE|PERF|NONE>" and appends it to $GITHUB_OUTPUT
# when that is set.

set -euo pipefail

soc=${1:?usage: build_esp32.sh <soc> [idf.py -D flags...]}
shift
flags=("$@")

if ! command -v idf.py >/dev/null 2>&1; then
    set +u
    # shellcheck disable=SC1091
    . "${IDF_PATH:?IDF_PATH is not set}/export.sh" >/dev/null
    set -u
fi

# The image needs the boot library produced by the host-side build; without
# it AtomVM's CMake only warns and mkimage fails much later.
boot_lib=esp32boot.avm
for flag in "${flags[@]}"; do
    case "$flag" in
        -DATOMVM_ELIXIR_SUPPORT=on|-DATOMVM_ELIXIR_SUPPORT=ON) boot_lib=elixir_esp32boot.avm ;;
    esac
done
boot_path="../../../build/libs/esp32boot/$boot_lib"
if [ ! -f "$boot_path" ]; then
    echo "error: $boot_path is missing; run the host-side libs build first" >&2
    exit 1
fi

log=build.log
rm -rf build sdkconfig sdkconfig.old
if ! {
        idf.py "${flags[@]}" set-target "$soc" &&
        idf.py "${flags[@]}" reconfigure &&
        idf.py "${flags[@]}" build
    } 2>&1 | tee "$log"
then
    if grep -q 'too small for binary' "$log"; then
        echo "error: the app does not fit the factory partition at the configured optimization level;" >&2
        echo "       tune it explicitly, for instance CONFIG_COMPILER_OPTIMIZATION_SIZE=y (-Os)" >&2
    fi
    exit 1
fi

if grep -q 'A generic_unix build must be done first' "$log"; then
    echo "error: the build did not find $boot_path" >&2
    exit 1
fi

optimization=$(sed -n 's/^CONFIG_COMPILER_OPTIMIZATION_\(DEBUG\|SIZE\|PERF\|NONE\)=y$/\1/p' sdkconfig)
echo "optimization=$optimization"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "optimization=$optimization" >> "$GITHUB_OUTPUT"
fi
