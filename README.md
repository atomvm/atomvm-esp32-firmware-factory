# AtomVM ESP32 firmware factory

This repository hosts prebuilt [AtomVM](https://github.com/atomvm/AtomVM)
firmware images for ESP32-family chips.

Images are built by GitHub Actions and published as release assets, one zip
bundle per image. Each bundle contains the `.img` file, the `sdkconfig` and
partition table it was built with, the image's parts as separate binaries, the
ELF and map files to decode crashes, SHA-256 checksums, and `FLASH.txt` with
the flashing and debugging instructions.

[`images.txt`](images.txt) lists the images, for ESP32, ESP32-S2, ESP32-S3,
ESP32-C2, ESP32-C3, ESP32-C5, ESP32-C6, ESP32-C61, ESP32-H2 and ESP32-P4. An
image is named after its chip and the features compiled into it, and every
image also exists with Elixir support (`-elixir`). Features are capabilities,
not requirements: a `psram` image also runs on a board without PSRAM.

- `psram` images run PSRAM at 80 MHz, or at 40 MHz on the ESP32.
- Images marked `-Os` in `images.txt` are optimized for size, because they do
  not fit the app partition at the `-O2` of upstream's release defaults.
- The ESP32-P4 image needs chip revision v3.1 or later, as upstream's does.

`FLASH.txt` describes two ways to flash a board:

- **Install** writes the whole image. It erases the board's NVS data, such as
  Wi-Fi settings.
- **Update** writes only the AtomVM virtual machine and its boot library, on a
  board that already runs AtomVM with the same partition table. NVS and the
  application in `main.avm` are kept.

There are two kinds of releases:

- **`nightly-0.7`** is rebuilt from AtomVM's `release-0.7` branch and replaced
  on every build. It is the repository's latest release.
- **`vX.Y.Z[-pre]`**, such as `v0.7.0-beta.0`, carries the images with
  features for that AtomVM release tag. It is built once, the night after the
  tag appears upstream, and never replaced. Upstream AtomVM publishes the
  images without features for the same tag.

See the [Releases](../../releases) page for downloads.
