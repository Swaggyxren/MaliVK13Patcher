# MaliVK13Patcher

Patch a stock **`vendor.img`** for any Mali-G57 MC2 device with the **Mali r38p1 driver overlay**, so your device starts reporting **Vulkan 1.3**.

Ships as:
- **`MaliVK13Patcher.exe`** — Windows GUI. Pick a `vendor.img`, pick where to save, click Patch.
- **`MaliVK13Patcher.apk`** — Android app. Same UX, runs entirely on the phone, no root needed for the patcher itself.

The output is **another `vendor.img`** — this tool does *not* flash anything. You flash the result yourself with `fastboot`.

> ![NOTE]
> Both the APK and the EXE produce the **same patched image**. Use whichever is more convenient — they wrap a shared core that bundles MIO-KITCHEN's vendor image toolchain (`extract.erofs`, `mkfs.erofs`, `simg2img`, `img2simg`, `mke2fs`, `e2fsdroid`).

## What the patch does

The patch is the **dobrogrind `Mali_38p1_VK_1-3` Magisk module**, applied directly into a vendor image (instead of as a Magisk overlay) so it survives reboots without root. The patch:

1. **Replaces** these Mali userspace blobs with the r38p1 build (the same blobs that MillenniumOSS uses in [their mt6789-common device tree](https://github.com/MillenniumOSS/android_device_tecno_mt6789-common/commit/28be9e37ef73b8bb4c0341eb0a7095429b626d84)):
   - `vendor/lib(64)/egl/libGLES_mali.so`
   - `vendor/lib(64)/hw/vulkan.mali.so`
   - `vendor/lib(64)/libarm_egl_properties_sysprop.so`
   - `vendor/lib(64)/libarm_gralloc_properties_sysprop.so`
   - `vendor/lib(64)/liblibarm_mali_config_sysprops.so`
   - `vendor/lib64/libgpumem.so`, `libgpuservice.so`, `libgpuwork.so`
2. **Merges** ~64 GPU/SurfaceFlinger props into `vendor/build.prop` (EGL hal_format configs, dynamic frame durations, opengles version, etc.).
3. **Repacks** to the original filesystem (EROFS or ext4) with the original UUID rerolled.

The `vendor` partition is the only thing modified — `boot`, `system`, `super`, `userdata` are untouched.

## Prerequisites (read this!)

This tool only does the *image patching* step. You still need:

- **Unlocked bootloader.** Required to flash anything to `vendor`.
- **A stock `vendor.img`.** Either dump it from your device (rooted: `dd if=/dev/block/by-name/vendor of=/sdcard/vendor.img`) or extract it from your phone's stock firmware ZIP.
- **`fastboot`** to flash the patched image.
- **`vbmeta` with verity disabled.** AOSP's verified-boot rejects modified `vendor.img` against the stock signed `vbmeta`. You must reflash `vbmeta` with verity off:
  ```
  fastboot flash vbmeta --disable-verity --disable-verification vbmeta.img
  ```
  …using the matching stock `vbmeta.img` for your build.

If any of those isn't true, the patched image **will not boot**. This is not a tool limitation — it's how Android Verified Boot works.

> ![WARNING]
> Modifying `vendor` can brick your device. Always keep an unmodified vendor image and `vbmeta` for recovery. **You are responsible.** Test on a development device before flashing your daily.

## Usage — Windows EXE

1. Download `MaliVK13Patcher.exe` from the latest [GitHub Release](../../releases).
2. Double-click. Pick your stock `vendor.img`. Pick an output path. Click **Patch**.
3. Flash with fastboot:
   ```bash
   fastboot flash vendor MaliVK13Patcher_output.img
   fastboot reboot
   ```

## Usage — Linux

1. Download `MaliVK13Patcher` (single-file ELF) from the latest [GitHub Release](../../releases).
2. `chmod +x MaliVK13Patcher && ./MaliVK13Patcher`. Pick your stock `vendor.img`. Pick an output path. Click **Patch**.
3. Flash with fastboot:
   ```bash
   fastboot flash vendor MaliVK13Patcher_output.img
   fastboot reboot
   ```

The Linux build is x86_64 only and uses the same Tkinter GUI as the Windows version; if your distro doesn't already have Tk libs, install `python3-tk` (Debian/Ubuntu) or `tk` (Arch / Fedora `python3-tkinter`). The bundled toolchain inside the ELF is statically built and works on glibc 2.28+ (Debian 10+ / Ubuntu 18.04+ / etc.).

## Usage — Android APK

1. Download `MaliVK13Patcher.apk` from the latest [GitHub Release](../../releases). Sideload it.
2. Open the app. Tap **Pick input vendor.img** and select your stock vendor image (anywhere via the system file picker).
3. Tap **Pick output location** and pick where to save the patched image.
4. Tap **Patch**. Wait for it to finish.
5. Pull the patched image to a PC, boot the phone into fastboot, flash with `fastboot flash vendor`.

The APK does **not** require root — it only needs read access to your input file and write access to your output location, both negotiated through the system file picker. (We can't flash from inside the app anyway — Android won't let user-space apps write to `/dev/block/by-name/vendor` without root.)

### Limitations of the APK build

- **Sparse images aren't supported on Android** in this build (we don't bundle an arm64 `simg2img`). If your stock `vendor.img` is sparse, run `simg2img` on a PC first or use the Windows EXE.
- **`ext4` vendors aren't supported on Android** either (no `debugfs` arm64 binary). Same workaround. Most modern mt6789 devices ship EROFS, so this is rarely a problem.

## Compatibility

Designed for any device with a **Mali-G57 MC2 GPU**. The patch is GPU-binding-level, so any Mali-G57 MC2 device should work in theory. The list below tracks the SoC families that have actually been tested.

### Tested

| SoC | Tested devices |
|---|---|
| MediaTek Helio G99 (mt6789) | TECNO POVA 4 / 4 Pro, TECNO POVA 5 (`LH7n`), other Helio G99 phones |
| MediaTek Dimensity 6080 (MT6855) | TECNO POVA 5 Pro (`LH8n`), other Dimensity 6080 phones |

### Untested but should work

Any other phone where:
- `getprop ro.hardware.egl` returns `mali`
- The GPU is **Mali-G57 MC2** (not MC1, not MC4)

The patcher does **not** hard-gate by device — you decide whether your device is compatible. If you successfully patch and boot a device that isn't on the tested list, please open an issue / PR adding it to the table.

## Building from source

### Windows EXE / Linux ELF

Same stack on both platforms (Python + Tkinter + PyInstaller); only the spec file differs (it bundles the matching `bin/<OS>/<ARCH>/` toolchain).

**Windows:**
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r windows/requirements.txt
pyinstaller windows/MaliVK13Patcher.spec --noconfirm
:: Output: dist/MaliVK13Patcher.exe
```

**Linux:**
```bash
sudo apt-get install -y python3-tk      # or your distro's tk package
python3 -m venv .venv
. .venv/bin/activate
pip install -r windows/requirements.txt
chmod +x bin/Linux/x86_64/*
pyinstaller linux/MaliVK13Patcher.spec --noconfirm
# Output: dist/MaliVK13Patcher
```

### APK

```bash
cd apk
./gradlew assembleRelease
# Output: app/build/outputs/apk/release/app-release-unsigned.apk
```

You'll need JDK 17+ and the Android SDK (the Gradle build downloads everything else automatically). Use the same `payload/` and `bin/` directories from the repo root.

## How it works under the hood

```
+------------------+    +------------------+    +------------------+
|  Pick vendor.img | -> |   Detect format  | -> | Sparse -> raw    |
|  (EXE / APK SAF) |    | (sparse, EROFS,  |    | (simg2img)       |
|                  |    |  ext4 magic)     |    +------------------+
+------------------+    +------------------+              |
                                                          v
+------------------+    +------------------+    +------------------+
| Output vendor.img| <- |  Repack same FS  | <- |  Unpack FS       |
|  (EXE save / SAF)|    |  (mkfs.erofs or  |    |  (extract.erofs  |
|                  |    |   mke2fs+e2fsdr) |    |   or debugfs)    |
+------------------+    +------------------+    +------------------+
                                                          |
                                                          v
                                                 +------------------+
                                                 | Overlay payload  |
                                                 | + merge build.   |
                                                 | prop props       |
                                                 +------------------+
```

The Windows EXE drives this with Python (`core/patcher.py`); the APK does it with a Kotlin port (`apk/app/src/main/.../core/Patcher.kt`). Both share the same `payload/` tree (Mali libs + `system.prop`) and the same `bin/` toolchain (built from sekaiacg/erofs-utils and android-tools, vendored from MIO-KITCHEN).

## Credits & Licenses

This project bundles work from several upstream projects. **All credit for the actual image-tooling goes to them.**

- **Mali r38p1 blobs**: from the Motorola XT2513-1 stock firmware as identified by [Shirayuki39 in MillenniumOSS](https://github.com/MillenniumOSS/android_device_tecno_mt6789-common/commit/28be9e37ef73b8bb4c0341eb0a7095429b626d84). Magisk module repackaging by **dobrogrind**.
- **`extract.erofs`, `mkfs.erofs`**: from [sekaiacg/erofs-utils](https://github.com/sekaiacg/erofs-utils) (GPL-2.0).
- **`simg2img`, `img2simg`, `mke2fs`, `e2fsdroid`**: from [nmeum/android-tools](https://github.com/nmeum/android-tools) and upstream e2fsprogs (GPL-2.0 / Apache-2.0).
- **Cross-platform binaries** vendored from [ColdWindScholar/MIO-KITCHEN-SOURCE](https://github.com/ColdWindScholar/MIO-KITCHEN-SOURCE) (AGPL-3.0).

This patcher itself is licensed **AGPL-3.0** (see [LICENSE](LICENSE)) to remain compatible with MIO-KITCHEN's terms. Source is provided in this repository.

## FAQ

**Q: Do I need root to use the APK?**
A: No. The APK only patches a `vendor.img` file you give it. You still need root or fastboot to actually *flash* the result to your device.

**Q: Will this brick my phone?**
A: It can if your bootloader is locked, vbmeta verity is on, or you flash the wrong image. See Prerequisites.

**Q: Is this a Magisk module replacement?**
A: Yes — same effect (Vulkan 1.3 reported, r38p1 blobs loaded), but applied to the vendor image directly instead of overlaying via Magisk. So it works without Magisk on the device.

**Q: Why ship both an APK and an EXE?**
A: The EXE is the simpler, more reliable path — it runs `simg2img` and handles ext4 too. The APK is for users who don't have a PC handy. Both produce the same output for EROFS-raw vendors.
