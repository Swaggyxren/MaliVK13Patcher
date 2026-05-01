"""Mali-G57 MC2 / Vulkan 1.3 vendor.img patcher.

Pipeline:
    detect format (sparse vs raw, EROFS vs ext4)
    -> sparse-to-raw if needed
    -> extract to a working dir
    -> overlay Mali libs from payload/vendor/
    -> merge system.prop key=value pairs into vendor/build.prop
    -> repack to a fresh image (matching the original format)
    -> sparse-back if the input was sparse

The patcher shells out to the bundled MIO-KITCHEN toolchain
(extract.erofs, mkfs.erofs, simg2img, img2simg, mke2fs, e2fsdroid).
The same script is used by the Windows EXE and by CI for verification.

Logging is line-oriented and goes through a configurable callback so the
GUI front-ends can stream progress to the user.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

LOG = Callable[[str], None]

SPARSE_MAGIC = b"\x3a\xff\x26\xed"
EROFS_MAGIC = b"\xe2\xe1\xf5\xe0"
EROFS_MAGIC_OFFSET = 1024
EXT4_MAGIC = b"\x53\xef"
EXT4_MAGIC_OFFSET = 1080  # superblock at byte 1024, magic at offset 56

REPO_ROOT = Path(__file__).resolve().parent.parent


class PatchError(RuntimeError):
    """Raised on any failure in the patch pipeline."""


@dataclass
class Manifest:
    name: str
    version: str
    source_module: str
    overlay_root: str
    vendor_relative_files: list[str]
    build_prop_path: str
    system_prop_source: str
    build_prop_merge_strategy: str
    compatible: dict
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=data["name"],
            version=str(data["version"]),
            source_module=data["source_module"],
            overlay_root=data["overlay_root"],
            vendor_relative_files=list(data["vendor_relative_files"]),
            build_prop_path=data["build_prop_path"],
            system_prop_source=data["system_prop_source"],
            build_prop_merge_strategy=data["build_prop_merge_strategy"],
            compatible=dict(data.get("compatible", {})),
            raw=data,
        )


def _detect_platform_dir() -> Path:
    """Return the bin/<OS>/<ARCH>/ subdir matching the current host."""
    sysname = platform.system()
    machine = platform.machine().lower()
    if sysname == "Linux":
        return REPO_ROOT / "bin" / "Linux" / "x86_64"
    if sysname == "Windows":
        return REPO_ROOT / "bin" / "Windows" / "AMD64"
    if sysname == "Darwin":
        # Not officially supported by this patcher; user should use Linux/Win.
        return REPO_ROOT / "bin" / "Linux" / "x86_64"
    if sysname == "Android" or "aarch64" in machine or machine == "arm64":
        return REPO_ROOT / "bin" / "Android" / "aarch64"
    raise PatchError(f"Unsupported host platform: {sysname}/{machine}")


def _bin(name: str, bin_dir: Optional[Path] = None) -> Path:
    """Resolve a tool name to an absolute path in the platform bin dir."""
    bin_dir = bin_dir or _detect_platform_dir()
    if platform.system() == "Windows":
        candidate = bin_dir / f"{name}.exe"
    else:
        candidate = bin_dir / name
    if not candidate.exists():
        raise PatchError(f"Required tool not found: {candidate}")
    return candidate


def _run(cmd: list[str], log: LOG, cwd: Optional[Path] = None) -> None:
    log(f"$ {' '.join(str(c) for c in cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PatchError(f"Tool not found: {cmd[0]}") from exc
    if result.stdout:
        for line in result.stdout.splitlines():
            log(f"  {line}")
    if result.returncode != 0:
        raise PatchError(
            f"Command {cmd[0]} exited with status {result.returncode}"
        )


# ---------------------------------------------------------------------------
# Format detection


@dataclass
class ImageInfo:
    is_sparse: bool
    fs: str  # "erofs" or "ext4"
    size: int


def detect(image: Path) -> ImageInfo:
    """Inspect image header bytes to determine sparse-ness and FS type."""
    if not image.exists():
        raise PatchError(f"Input image does not exist: {image}")
    size = image.stat().st_size
    if size < 4096:
        raise PatchError(f"Input image is suspiciously small ({size} bytes): {image}")
    with image.open("rb") as fh:
        head = fh.read(4096)
        is_sparse = head[:4] == SPARSE_MAGIC
        # for fs detection we look at the raw, but if sparse we have to peek
        # past the sparse header into the first chunk. Easier: convert sparse
        # -> raw first, then re-detect.
        fs = "unknown"
        if not is_sparse:
            if head[EROFS_MAGIC_OFFSET:EROFS_MAGIC_OFFSET + 4] == EROFS_MAGIC:
                fs = "erofs"
            elif head[EXT4_MAGIC_OFFSET:EXT4_MAGIC_OFFSET + 2] == EXT4_MAGIC:
                fs = "ext4"
    return ImageInfo(is_sparse=is_sparse, fs=fs, size=size)


# ---------------------------------------------------------------------------
# Sparse <-> raw helpers


def simg2img(image: Path, out: Path, log: LOG) -> None:
    _run([str(_bin("simg2img")), str(image), str(out)], log)


def img2simg(image: Path, out: Path, log: LOG) -> None:
    _run([str(_bin("img2simg")), str(image), str(out)], log)


# ---------------------------------------------------------------------------
# EROFS unpack/repack


def extract_erofs(image: Path, outdir: Path, log: LOG) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            str(_bin("extract.erofs")),
            "-i", str(image),
            "-x",
            "-f",  # overwrite existing
            "-s",  # silent (no progress bar -> friendlier logging)
            "-o", str(outdir),
        ],
        log,
    )


def mkfs_erofs(
    src_dir: Path,
    out_image: Path,
    log: LOG,
    compress: str = "lz4hc,9",
    mount_point: str = "/vendor",
    fs_config: Optional[Path] = None,
    file_contexts: Optional[Path] = None,
    fs_uuid: Optional[str] = None,
    timestamp: Optional[int] = None,
) -> None:
    cmd: list[str] = [str(_bin("mkfs.erofs"))]
    cmd += [f"-z{compress}"]
    cmd += [f"--mount-point={mount_point}"]
    if fs_uuid:
        cmd += [f"-U{fs_uuid}"]
    if timestamp is not None:
        cmd += [f"-T{int(timestamp)}"]
    if fs_config and fs_config.exists():
        cmd += [f"--fs-config-file={fs_config}"]
    if file_contexts and file_contexts.exists():
        cmd += [f"--file-contexts={file_contexts}"]
    cmd += [str(out_image), str(src_dir)]
    _run(cmd, log)


# ---------------------------------------------------------------------------
# ext4 unpack/repack


def extract_ext4(image: Path, outdir: Path, log: LOG) -> None:
    """Use debugfs (e2fsprogs) to dump the entire fs into outdir.

    Note: debugfs is part of e2fsprogs; on Windows we ship it via the
    bundled mke2fs build. If unavailable, raise.
    """
    debugfs = shutil.which("debugfs")
    if debugfs is None:
        raise PatchError(
            "ext4 input requires debugfs (e2fsprogs) on PATH. "
            "Convert vendor.img to EROFS or extract via your own tool."
        )
    outdir.mkdir(parents=True, exist_ok=True)
    _run([debugfs, "-R", f"rdump / {outdir}", str(image)], log)


def mkfs_ext4(
    src_dir: Path,
    out_image: Path,
    log: LOG,
    label: str = "vendor",
    fs_uuid: Optional[str] = None,
    fs_config: Optional[Path] = None,
    file_contexts: Optional[Path] = None,
    block_size: int = 4096,
) -> None:
    # Calculate target size as 110% of the source dir size, rounded up to block.
    src_size = sum(f.stat().st_size for f in src_dir.rglob("*") if f.is_file())
    target = int(src_size * 1.10)
    target = max(target, 32 * 1024 * 1024)  # at least 32 MiB
    blocks = (target + block_size - 1) // block_size
    cmd: list[str] = [
        str(_bin("mke2fs")),
        "-t", "ext4",
        "-L", label,
        "-b", str(block_size),
        "-I", "256",
        "-M", "/vendor",
        "-O", "^has_journal",
        "-d", str(src_dir),
    ]
    if fs_uuid:
        cmd += ["-U", fs_uuid]
    cmd += [str(out_image), str(blocks)]
    _run(cmd, log)
    e2fsdroid_cmd = [str(_bin("e2fsdroid")), "-e"]
    if file_contexts and file_contexts.exists():
        e2fsdroid_cmd += ["-S", str(file_contexts)]
    if fs_config and fs_config.exists():
        e2fsdroid_cmd += ["-C", str(fs_config)]
    e2fsdroid_cmd += [str(out_image)]
    _run(e2fsdroid_cmd, log)


# ---------------------------------------------------------------------------
# Patch logic


def overlay_files(
    payload_root: Path,
    dest_vendor_dir: Path,
    files: Iterable[str],
    log: LOG,
) -> "list[str]":
    """Copy each `relpath` from payload_root into dest_vendor_dir/relpath, overwriting.

    Returns the list of relpaths actually copied (used so we can ensure they
    end up in fs_config and file_contexts when repacking).
    """
    copied: list[str] = []
    for rel in files:
        src = payload_root / rel
        dst = dest_vendor_dir / rel
        if not src.exists():
            log(f"  WARNING: payload file missing, skipping: {src}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        log(f"  overlay: {rel}  ({src.stat().st_size:,} bytes)")
        copied.append(rel)
    return copied


def normalize_vendor_layout(
    unpack_dir: Path,
    source_basename: str,
    log: LOG,
) -> "tuple[Path, Optional[Path], Optional[Path]]":
    """Rename the source-name subdir to ``vendor`` and rewrite its fs_config /
    file_contexts to use the canonical ``vendor/`` prefix instead of
    ``<source-basename>/``. Returns (vendor_root, fs_config_path, file_contexts_path).
    """
    src_root = unpack_dir / source_basename
    canonical_root = unpack_dir / "vendor"
    if src_root.exists() and src_root != canonical_root:
        if canonical_root.exists():
            shutil.rmtree(canonical_root)
        src_root.rename(canonical_root)
        log(f"  normalized vendor root: {src_root.name} -> vendor")
    elif canonical_root.exists():
        pass
    else:
        # no rename needed; source basename already happened to be "vendor"
        if (unpack_dir / "vendor").exists():
            canonical_root = unpack_dir / "vendor"

    config_dir = unpack_dir / "config"
    fs_config_path: Optional[Path] = None
    file_contexts_path: Optional[Path] = None
    if config_dir.exists():
        for p in list(config_dir.iterdir()):
            if p.name.endswith("_fs_config"):
                fs_config_path = p
            elif p.name.endswith("_file_contexts"):
                file_contexts_path = p

    if fs_config_path is not None:
        text = fs_config_path.read_text(encoding="utf-8", errors="replace")
        rewritten_lines = []
        old_prefix = f"{source_basename}/"
        old_dir_entry = f"{source_basename} "
        for line in text.splitlines():
            if line.startswith(old_prefix):
                rewritten_lines.append("vendor/" + line[len(old_prefix):])
            elif line.startswith(old_dir_entry):
                rewritten_lines.append("vendor " + line[len(old_dir_entry):])
            else:
                rewritten_lines.append(line)
        new_path = config_dir / "vendor_fs_config"
        new_path.write_text("\n".join(rewritten_lines) + "\n", encoding="utf-8")
        if new_path != fs_config_path:
            try:
                fs_config_path.unlink()
            except OSError:
                pass
        fs_config_path = new_path
        log(f"  normalized fs_config -> {fs_config_path}")

    if file_contexts_path is not None:
        # file_contexts entries use absolute paths (/vendor/...) so they
        # don't depend on the source-dir name. Just rename the file.
        new_path = config_dir / "vendor_file_contexts"
        if new_path != file_contexts_path:
            shutil.copy2(file_contexts_path, new_path)
            try:
                file_contexts_path.unlink()
            except OSError:
                pass
        file_contexts_path = new_path

    return canonical_root, fs_config_path, file_contexts_path


def ensure_fs_config_entries(
    fs_config_path: Path,
    vendor_root: Path,
    overlaid_relpaths: Iterable[str],
    log: LOG,
) -> None:
    """For every overlaid file (and its parent directories), make sure there
    is an entry in fs_config. mkfs.erofs requires every file in the source
    tree to have a canned fs_config row.
    """
    text = fs_config_path.read_text(encoding="utf-8", errors="replace") if fs_config_path.exists() else ""
    existing_keys: set[str] = set()
    lines = text.splitlines()
    for line in lines:
        s = line.strip()
        if not s:
            continue
        first = s.split(" ", 1)[0]
        existing_keys.add(first.rstrip("/"))

    additions: list[str] = []
    needed_paths: list[tuple[str, bool]] = []  # (key, is_dir)
    for rel in overlaid_relpaths:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            d = "vendor/" + "/".join(parts[:i])
            needed_paths.append((d, True))
        f = "vendor/" + rel
        needed_paths.append((f, False))

    seen: set[str] = set()
    for key, is_dir in needed_paths:
        if key in seen:
            continue
        seen.add(key)
        if key in existing_keys:
            continue
        if is_dir:
            additions.append(f"{key} 0 2000 0755")
        else:
            additions.append(f"{key} 0 0 0644")

    if additions:
        # mkfs.erofs is strict: it rejects blank lines and comment lines in
        # fs_config. Keep only existing data lines, then append new entries.
        kept = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        kept.extend(additions)
        fs_config_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        log(f"  added {len(additions)} fs_config entries for overlay")


def parse_prop(text: str) -> "list[tuple[str, str]]":
    """Parse key=value lines, preserving order; comments/blank lines skipped."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        k, _, v = s.partition("=")
        out.append((k.strip(), v.strip()))
    return out


def merge_build_prop(build_prop: Path, props_to_set: list[tuple[str, str]], log: LOG) -> None:
    """Replace existing keys, append new ones. Idempotent across re-runs."""
    if build_prop.exists():
        existing = build_prop.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        existing = []
    new_lines: list[str] = []
    seen: dict[str, str] = {k: v for k, v in props_to_set}
    written: set[str] = set()

    for line in existing:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            new_lines.append(line)
            continue
        k, _, _ = s.partition("=")
        k = k.strip()
        if k in seen:
            new_lines.append(f"{k}={seen[k]}")
            written.add(k)
        else:
            new_lines.append(line)

    appended: list[str] = []
    for k, v in props_to_set:
        if k not in written:
            appended.append(f"{k}={v}")

    if appended:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append("# Mali-G57 MC2 / Vulkan 1.3 patch (MaliVK13Patcher)")
        new_lines.extend(appended)

    build_prop.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    log(f"  merged {len(props_to_set)} prop key(s); replaced {len(written)}, appended {len(appended)}")


# ---------------------------------------------------------------------------
# Top-level entrypoint


@dataclass
class PackOptions:
    """User-tunable pack settings, mirroring MIO-KITCHEN's Pack dialog.

    Defaults reproduce the patcher's previous behavior (lz4hc level 9, no
    fixed timestamp). Setting any field to ``None`` means "use the tool's
    default".
    """

    erofs_compression: str = "lz4hc"   # lz4 | lz4hc | lzma | deflate | zstd
    erofs_level: Optional[int] = 9     # None = use mkfs.erofs default
    timestamp: Optional[int] = None    # fixed UTC for all files; None = mkfs default

    def erofs_compress_arg(self) -> str:
        if self.erofs_level is None:
            return self.erofs_compression
        return f"{self.erofs_compression},{self.erofs_level}"


def patch_image(
    input_image: Path,
    output_image: Path,
    repo_root: Path = REPO_ROOT,
    log: LOG = print,
    keep_workdir: bool = False,
    workdir: Optional[Path] = None,
    pack: Optional["PackOptions"] = None,
) -> dict:
    """Patch a stock vendor.img and emit `output_image`. Returns metadata dict."""
    if pack is None:
        pack = PackOptions()
    input_image = Path(input_image).resolve()
    output_image = Path(output_image).resolve()
    if input_image == output_image:
        raise PatchError("input and output paths must differ")

    manifest_path = repo_root / "payload" / "payload_manifest.json"
    manifest = Manifest.load(manifest_path)

    payload_root = repo_root / manifest.overlay_root
    system_prop_path = repo_root / manifest.system_prop_source
    if not payload_root.exists():
        raise PatchError(f"Payload root missing: {payload_root}")
    if not system_prop_path.exists():
        raise PatchError(f"system.prop source missing: {system_prop_path}")

    info = detect(input_image)
    log(f"input: {input_image}")
    log(f"  size: {info.size:,} bytes")
    log(f"  sparse: {info.is_sparse}")

    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="malivk13_"))
    workdir.mkdir(parents=True, exist_ok=True)
    log(f"workdir: {workdir}")

    try:
        raw_image = input_image
        if info.is_sparse:
            raw_image = workdir / "vendor.raw.img"
            log("[1/6] de-sparsing input image (simg2img)")
            simg2img(input_image, raw_image, log)
            with raw_image.open("rb") as fh:
                head = fh.read(4096)
                if head[EROFS_MAGIC_OFFSET:EROFS_MAGIC_OFFSET + 4] == EROFS_MAGIC:
                    info.fs = "erofs"
                elif head[EXT4_MAGIC_OFFSET:EXT4_MAGIC_OFFSET + 2] == EXT4_MAGIC:
                    info.fs = "ext4"
        else:
            log("[1/6] image is already raw, skipping sparse conversion")

        log(f"  fs: {info.fs}")
        if info.fs not in ("erofs", "ext4"):
            raise PatchError(
                "Could not detect vendor filesystem type. Only EROFS and ext4 are supported."
            )

        unpack_dir = workdir / "vendor_unpacked"
        cfg_dir = workdir / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)

        log(f"[2/6] unpacking {info.fs} image into {unpack_dir}")
        if info.fs == "erofs":
            extract_erofs(raw_image, unpack_dir, log)
            # extract.erofs -x writes config/ next to the output dir (or inside)
            # we'll discover fs_config and file_contexts after extraction
        else:
            extract_ext4(raw_image, unpack_dir, log)

        # extract.erofs lays out the unpacked tree as:
        #   <unpack_dir>/<image-basename>/...      (vendor file tree)
        #   <unpack_dir>/config/<image-basename>_fs_config
        #   <unpack_dir>/config/<image-basename>_file_contexts
        #   <unpack_dir>/config/<image-basename>_fs_options
        # Source basename comes from the image filename (without .img).
        source_basename = raw_image.stem
        # Strip common ".raw" / ".patched" suffixes for our temp files
        for trim in (".raw", ".patched"):
            if source_basename.endswith(trim):
                source_basename = source_basename[: -len(trim)]
        # If extract.erofs wrote out a directory matching the basename,
        # use that as the vendor root and rewrite fs_config/file_contexts
        # to use canonical "vendor/" prefix + mount-point /vendor.
        if (unpack_dir / source_basename).is_dir():
            vendor_root, fs_config, file_contexts = normalize_vendor_layout(
                unpack_dir, source_basename, log,
            )
        elif (unpack_dir / "vendor").is_dir():
            vendor_root, fs_config, file_contexts = normalize_vendor_layout(
                unpack_dir, "vendor", log,
            )
        elif (unpack_dir / "lib64").exists() or (unpack_dir / "lib").exists():
            # rare: extract.erofs flattened the content directly
            vendor_root = unpack_dir
            config_dir = unpack_dir / "config"
            fs_config = None
            file_contexts = None
            if config_dir.exists():
                for p in config_dir.iterdir():
                    if p.name.endswith("_fs_config") and fs_config is None:
                        fs_config = p
                    elif p.name.endswith("_file_contexts") and file_contexts is None:
                        file_contexts = p
        else:
            non_config_dirs = [
                p for p in unpack_dir.iterdir()
                if p.is_dir() and p.name != "config"
            ]
            if len(non_config_dirs) == 1:
                vendor_root, fs_config, file_contexts = normalize_vendor_layout(
                    unpack_dir, non_config_dirs[0].name, log,
                )
            else:
                raise PatchError(
                    f"Could not locate vendor root in unpacked tree under {unpack_dir}. "
                    f"Children: {[p.name for p in unpack_dir.iterdir()]}"
                )

        log(f"  vendor_root: {vendor_root}")
        if fs_config:
            log(f"  fs_config: {fs_config}")
        if file_contexts:
            log(f"  file_contexts: {file_contexts}")

        log(f"[3/6] overlaying Mali payload from {payload_root}")
        overlaid = overlay_files(payload_root, vendor_root, manifest.vendor_relative_files, log)
        if fs_config and overlaid:
            ensure_fs_config_entries(fs_config, vendor_root, overlaid, log)

        log("[4/6] merging system.prop into vendor build.prop")
        build_prop = vendor_root / manifest.build_prop_path
        props = parse_prop(system_prop_path.read_text(encoding="utf-8", errors="replace"))
        merge_build_prop(build_prop, props, log)

        out_raw = workdir / "vendor.patched.raw.img"
        log(f"[5/6] repacking {info.fs} -> {out_raw}")
        # Try to keep UUID stable so dm-verity hash differs only by content
        fs_uuid = str(uuid.uuid4())
        if info.fs == "erofs":
            mkfs_erofs(
                vendor_root, out_raw, log,
                compress=pack.erofs_compress_arg(),
                fs_config=fs_config,
                file_contexts=file_contexts,
                fs_uuid=fs_uuid,
                timestamp=pack.timestamp,
            )
        else:
            mkfs_ext4(
                vendor_root, out_raw, log,
                fs_uuid=fs_uuid,
                fs_config=fs_config,
                file_contexts=file_contexts,
            )

        log(f"[6/6] writing final image to {output_image}")
        output_image.parent.mkdir(parents=True, exist_ok=True)
        if info.is_sparse:
            img2simg(out_raw, output_image, log)
        else:
            shutil.copy2(out_raw, output_image)

        result = {
            "input": str(input_image),
            "output": str(output_image),
            "input_format": info.fs,
            "input_was_sparse": info.is_sparse,
            "input_size": info.size,
            "output_size": output_image.stat().st_size,
            "patch_name": manifest.name,
            "patch_version": manifest.version,
        }
        log(f"DONE: {json.dumps(result)}")
        return result
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Patch a stock Mali-G57 MC2 vendor.img with the 38p1 / Vulkan 1.3 driver overlay."
    )
    p.add_argument("input", type=Path, help="Path to stock vendor.img (sparse or raw, EROFS or ext4)")
    p.add_argument("output", type=Path, help="Path for the patched output vendor.img")
    p.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Override repo root (for testing)")
    p.add_argument("--keep-workdir", action="store_true", help="Don't delete the temp work dir on exit")
    p.add_argument("--workdir", type=Path, default=None, help="Override work dir location")
    args = p.parse_args(argv)
    try:
        patch_image(
            args.input, args.output,
            repo_root=args.repo_root,
            keep_workdir=args.keep_workdir,
            workdir=args.workdir,
        )
        return 0
    except PatchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
