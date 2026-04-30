"""Tkinter GUI for MaliVK13Patcher (Windows EXE).

The GUI wraps :mod:`core.patcher`: the user picks an input ``vendor.img`` and
an output path; the patch pipeline runs in a worker thread and streams log
lines to a text area in real time.

Built into a one-file EXE via PyInstaller (see windows/build_exe.py and the
.spec file). All bundled binaries (extract.erofs.exe, mkfs.erofs.exe,
simg2img.exe, img2simg.exe, mke2fs.exe, e2fsdroid.exe) plus the patch payload
are added by the spec under ``bin/Windows/AMD64/`` and ``payload/``.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

# When frozen, _MEIPASS is the unpacked tempdir; layout there mirrors the repo:
#   <_MEIPASS>/core/patcher.py
#   <_MEIPASS>/bin/Windows/AMD64/...
#   <_MEIPASS>/payload/...
# When running from source (python -m windows.src.gui), repo root is two levels up.

if getattr(sys, "frozen", False):
    REPO_ROOT = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Make `core` importable
sys.path.insert(0, str(REPO_ROOT))

from core import patcher  # noqa: E402

APP_TITLE = "Mali-G57 MC2 / Vulkan 1.3 vendor.img Patcher"


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("780x560")
        self.root.minsize(640, 480)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.busy = False
        self.log_queue: "queue.Queue[str]" = queue.Queue()

        self._build_ui()
        self.root.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        title = ttk.Label(
            frame,
            text=APP_TITLE,
            font=("Segoe UI", 13, "bold"),
        )
        title.grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        subtitle = ttk.Label(
            frame,
            text=(
                "Patches a stock vendor.img (EROFS or ext4, sparse or raw) with the "
                "Mali 38p1 driver overlay so the device reports Vulkan 1.3.\n"
                "You still need to flash the output yourself with fastboot — see README."
            ),
            wraplength=720,
            foreground="#444",
        )
        subtitle.grid(row=1, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(frame, text="Input vendor.img:").grid(row=2, column=0, sticky="e", **pad)
        ttk.Entry(frame, textvariable=self.input_var, width=70).grid(
            row=2, column=1, sticky="ew", **pad
        )
        ttk.Button(frame, text="Browse…", command=self.pick_input).grid(row=2, column=2, **pad)

        ttk.Label(frame, text="Output vendor.img:").grid(row=3, column=0, sticky="e", **pad)
        ttk.Entry(frame, textvariable=self.output_var, width=70).grid(
            row=3, column=1, sticky="ew", **pad
        )
        ttk.Button(frame, text="Save as…", command=self.pick_output).grid(row=3, column=2, **pad)

        self.run_btn = ttk.Button(frame, text="Patch", command=self.run_patch)
        self.run_btn.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew", **pad)

        self.log_widget = scrolledtext.ScrolledText(
            frame, height=18, font=("Consolas", 9), state="disabled"
        )
        self.log_widget.grid(row=6, column=0, columnspan=3, sticky="nsew", **pad)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(6, weight=1)

    # -- file pickers ----------------------------------------------------

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select stock vendor.img",
            filetypes=[("Vendor image", "*.img"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)
            # Suggest a default output filename next to the input
            if not self.output_var.get():
                p = Path(path)
                suggested = p.with_name(p.stem + "_mali_vk13" + p.suffix)
                self.output_var.set(str(suggested))

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save patched vendor.img as…",
            defaultextension=".img",
            filetypes=[("Vendor image", "*.img"), ("All files", "*.*")],
            initialfile="vendor_mali_vk13.img",
        )
        if path:
            self.output_var.set(path)

    # -- log streaming ---------------------------------------------------

    def _log(self, line: str) -> None:
        self.log_queue.put(line)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_widget.configure(state="normal")
                self.log_widget.insert("end", line + "\n")
                self.log_widget.see("end")
                self.log_widget.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    # -- patch run -------------------------------------------------------

    def run_patch(self) -> None:
        if self.busy:
            return
        in_path = self.input_var.get().strip()
        out_path = self.output_var.get().strip()
        if not in_path:
            messagebox.showwarning(APP_TITLE, "Please select an input vendor.img")
            return
        if not out_path:
            messagebox.showwarning(APP_TITLE, "Please choose an output path")
            return
        if os.path.abspath(in_path) == os.path.abspath(out_path):
            messagebox.showerror(APP_TITLE, "Input and output paths must differ")
            return

        self.busy = True
        self.run_btn.configure(state="disabled", text="Patching…")
        self.progress.start(10)
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

        t = threading.Thread(
            target=self._patch_worker, args=(in_path, out_path), daemon=True
        )
        t.start()

    def _patch_worker(self, in_path: str, out_path: str) -> None:
        try:
            patcher.patch_image(
                Path(in_path),
                Path(out_path),
                repo_root=REPO_ROOT,
                log=self._log,
            )
            self.root.after(0, self._on_done, True, None, out_path)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._on_done, False, str(exc), out_path)

    def _on_done(self, ok: bool, err: str | None, out_path: str) -> None:
        self.progress.stop()
        self.run_btn.configure(state="normal", text="Patch")
        self.busy = False
        if ok:
            messagebox.showinfo(
                APP_TITLE,
                f"Patched image written to:\n{out_path}\n\n"
                "Now boot your phone into fastboot and flash this image. "
                "Bootloader must be unlocked and vbmeta verity disabled — see README.",
            )
        else:
            messagebox.showerror(APP_TITLE, f"Patch failed:\n{err}")

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    App().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
