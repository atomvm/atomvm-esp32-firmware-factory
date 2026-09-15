# AtomVM ESP32 firmware factory

This repository hosts prebuilt [AtomVM](https://github.com/atomvm/AtomVM)
firmware images for ESP32-family chips.

Images are built by GitHub Actions and published as release assets, one zip
bundle per image. Each bundle contains the `.img` file, the `sdkconfig` and
partition table it was built with, the image's parts as separate binaries,
SHA-256 checksums, and `FLASH.txt` with the flashing instructions.

`FLASH.txt` describes two ways to flash a board:

- **Install** writes the whole image. It erases the board's NVS data, such as
  Wi-Fi settings.
- **Update** writes only the AtomVM virtual machine and its boot library, on a
  board that already runs AtomVM with the same partition table. NVS and the
  application in `main.avm` are kept.

See the [Releases](../../releases) page for downloads.
