"""Tkinter GUI for MaliVK13Patcher (used on both Windows and Linux).

Wraps :mod:`core.patcher`: the user picks an input ``vendor.img``, an output
path, optionally tweaks the pack options, and the patch pipeline runs in a
worker thread streaming log lines to a text area.

Built into a one-file binary via PyInstaller (see ``windows/MaliVK13Patcher.spec``
and ``linux/MaliVK13Patcher.spec``). The bundled toolchain plus the patch
payload are added by the spec under ``bin/<OS>/<ARCH>/`` and ``payload/``.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

# When frozen, _MEIPASS is the unpacked tempdir; layout there mirrors the repo:
#   <_MEIPASS>/core/patcher.py
#   <_MEIPASS>/bin/<OS>/<ARCH>/...
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

# Two-theme palette. Pure dark / pure light, no greys-on-greys.
THEMES = {
    "dark": {
        "bg":          "#0e0e10",
        "panel":       "#17171a",
        "fg":          "#f5f5f7",
        "muted":       "#8a8a91",
        "accent":      "#ffffff",
        "accent_fg":   "#0e0e10",
        "border":      "#2a2a2f",
        "log_bg":      "#0a0a0c",
        "log_fg":      "#e6e6ea",
        "entry_bg":    "#1c1c20",
        "entry_fg":    "#f5f5f7",
        "select_bg":   "#3a3a44",
        "trough":      "#1c1c20",
    },
    "light": {
        "bg":          "#ffffff",
        "panel":       "#f6f6f7",
        "fg":          "#111114",
        "muted":       "#6b6b73",
        "accent":      "#111114",
        "accent_fg":   "#ffffff",
        "border":      "#dcdce0",
        "log_bg":      "#ffffff",
        "log_fg":      "#1d1d1f",
        "entry_bg":    "#ffffff",
        "entry_fg":    "#111114",
        "select_bg":   "#dcdce0",
        "trough":      "#eaeaee",
    },
}

EROFS_COMPRESSORS = ("lz4", "lz4hc", "lzma", "deflate", "zstd")


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("840x640")
        self.root.minsize(720, 540)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.theme_var = tk.StringVar(value="dark")

        # Pack-option vars (mirrors MIO-KITCHEN's Pack dialog)
        self.compress_var = tk.StringVar(value="lz4hc")
        self.level_var = tk.IntVar(value=9)        # 0 = mkfs.erofs default
        self.utc_var = tk.StringVar(value="")      # blank = original timestamp
        self.advanced_open = tk.BooleanVar(value=False)

        self.busy = False
        self.worker_done_at: float | None = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.style = ttk.Style()

        self._build_ui()
        self._apply_theme()
        # Make sure the progress bar is fully empty at launch (Tk's
        # indeterminate Progressbars sometimes paint a partial fill on first
        # draw if not explicitly stopped + zeroed).
        self._reset_progress()
        self.root.after(100, self._drain_log_queue)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.root.option_add("*Font", "TkDefaultFont 10")

        outer = ttk.Frame(self.root, style="Bg.TFrame")
        outer.pack(fill="both", expand=True)

        # ---- header -------------------------------------------------
        header = ttk.Frame(outer, style="Bg.TFrame")
        header.pack(fill="x", padx=20, pady=(18, 4))
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        self.theme_btn = ttk.Button(
            header, text="Light", style="Ghost.TButton", command=self._toggle_theme
        )
        self.theme_btn.pack(side="right")

        ttk.Label(
            outer,
            text=(
                "Patches a stock vendor.img (EROFS or ext4, sparse or raw) with the "
                "Mali r38p1 driver overlay so the device reports Vulkan 1.3. "
                "You still flash the output yourself with fastboot — see README."
            ),
            style="Subtitle.TLabel",
            wraplength=760,
        ).pack(fill="x", padx=20, pady=(0, 14))

        # ---- file pickers ------------------------------------------
        files = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        files.pack(fill="x", padx=20, pady=(0, 12))

        ttk.Label(files, text="Input vendor.img", style="FieldLabel.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Entry(files, textvariable=self.input_var, style="Field.TEntry").grid(
            row=1, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(
            files, text="Browse", style="Accent.TButton", command=self.pick_input
        ).grid(row=1, column=1, sticky="ew")

        ttk.Label(files, text="Output vendor.img", style="FieldLabel.TLabel").grid(
            row=2, column=0, sticky="w", pady=(12, 4)
        )
        ttk.Entry(files, textvariable=self.output_var, style="Field.TEntry").grid(
            row=3, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(
            files, text="Save as", style="Accent.TButton", command=self.pick_output
        ).grid(row=3, column=1, sticky="ew")
        files.columnconfigure(0, weight=1)

        # ---- advanced (collapsed by default) -----------------------
        adv_outer = ttk.Frame(outer, style="Bg.TFrame")
        adv_outer.pack(fill="x", padx=20)
        self.adv_toggle_btn = ttk.Button(
            adv_outer,
            text="▸ Advanced pack options",
            style="Ghost.TButton",
            command=self._toggle_advanced,
        )
        self.adv_toggle_btn.pack(anchor="w")

        self.adv_panel = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        # Not packed yet — toggle reveals it.

        ttk.Label(
            self.adv_panel,
            text="EROFS compression",
            style="FieldLabel.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        compress_box = ttk.Combobox(
            self.adv_panel,
            textvariable=self.compress_var,
            values=EROFS_COMPRESSORS,
            state="readonly",
            width=12,
        )
        compress_box.grid(row=0, column=1, sticky="w")

        ttk.Label(
            self.adv_panel, text="Level", style="FieldLabel.TLabel"
        ).grid(row=0, column=2, sticky="e", padx=(20, 8))
        level_spin = ttk.Spinbox(
            self.adv_panel,
            from_=0,
            to=9,
            textvariable=self.level_var,
            width=4,
            justify="center",
        )
        level_spin.grid(row=0, column=3, sticky="w")
        ttk.Label(
            self.adv_panel,
            text="(0 = mkfs default, 9 = max)",
            style="Hint.TLabel",
        ).grid(row=0, column=4, sticky="w", padx=(8, 0))

        ttk.Label(
            self.adv_panel,
            text="Fixed UTC timestamp",
            style="FieldLabel.TLabel",
        ).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(12, 0))
        ttk.Entry(self.adv_panel, textvariable=self.utc_var, width=14).grid(
            row=1, column=1, sticky="w", pady=(12, 0)
        )
        ttk.Button(
            self.adv_panel,
            text="Now",
            style="Ghost.TButton",
            command=lambda: self.utc_var.set(str(int(time.time()))),
        ).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(12, 0))
        ttk.Label(
            self.adv_panel,
            text="(blank = use original image timestamp)",
            style="Hint.TLabel",
        ).grid(row=1, column=3, columnspan=2, sticky="w", padx=(8, 0), pady=(12, 0))

        # ---- run button --------------------------------------------
        action = ttk.Frame(outer, style="Bg.TFrame")
        action.pack(fill="x", padx=20, pady=(14, 8))
        self.run_btn = ttk.Button(
            action, text="Patch", style="Primary.TButton", command=self.run_patch
        )
        self.run_btn.pack(fill="x")

        # ---- progress bar (kept zero until run starts) -------------
        prog_frame = ttk.Frame(outer, style="Bg.TFrame")
        prog_frame.pack(fill="x", padx=20)
        self.progress = ttk.Progressbar(
            prog_frame,
            mode="determinate",
            maximum=100,
            value=0,
            style="App.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x")

        # ---- log -----------------------------------------------------
        self.log_widget = scrolledtext.ScrolledText(
            outer,
            height=18,
            font=("Consolas" if sys.platform.startswith("win") else "Monospace", 10),
            state="disabled",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self.log_widget.pack(fill="both", expand=True, padx=20, pady=(10, 18))

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def _toggle_theme(self) -> None:
        new = "light" if self.theme_var.get() == "dark" else "dark"
        self.theme_var.set(new)
        self._apply_theme()

    def _apply_theme(self) -> None:
        t = THEMES[self.theme_var.get()]
        self.theme_btn.configure(text="Light" if self.theme_var.get() == "dark" else "Dark")

        self.root.configure(bg=t["bg"])
        self.style.theme_use("clam")

        self.style.configure("Bg.TFrame", background=t["bg"])
        self.style.configure(
            "Panel.TFrame",
            background=t["panel"],
            relief="flat",
            borderwidth=1,
        )

        self.style.configure(
            "Title.TLabel",
            background=t["bg"],
            foreground=t["fg"],
            font=("Segoe UI", 16, "bold")
            if sys.platform.startswith("win") else
            ("Helvetica", 15, "bold"),
        )
        self.style.configure(
            "Subtitle.TLabel",
            background=t["bg"],
            foreground=t["muted"],
            font=("TkDefaultFont", 10),
        )
        self.style.configure(
            "FieldLabel.TLabel",
            background=t["panel"],
            foreground=t["fg"],
            font=("TkDefaultFont", 10, "bold"),
        )
        self.style.configure(
            "Hint.TLabel",
            background=t["panel"],
            foreground=t["muted"],
            font=("TkDefaultFont", 9),
        )

        # Entries
        self.style.configure(
            "Field.TEntry",
            fieldbackground=t["entry_bg"],
            foreground=t["entry_fg"],
            insertcolor=t["fg"],
            bordercolor=t["border"],
            lightcolor=t["border"],
            darkcolor=t["border"],
            relief="flat",
            padding=6,
        )
        self.style.map(
            "Field.TEntry",
            fieldbackground=[("focus", t["entry_bg"])],
            bordercolor=[("focus", t["accent"])],
        )

        # Combobox & Spinbox match Field.TEntry visuals
        for sty in ("TCombobox", "TSpinbox"):
            self.style.configure(
                sty,
                fieldbackground=t["entry_bg"],
                background=t["entry_bg"],
                foreground=t["entry_fg"],
                bordercolor=t["border"],
                arrowcolor=t["fg"],
                lightcolor=t["border"],
                darkcolor=t["border"],
                relief="flat",
                padding=4,
            )
            self.style.map(
                sty,
                fieldbackground=[("readonly", t["entry_bg"])],
                foreground=[("readonly", t["entry_fg"])],
                background=[("readonly", t["entry_bg"])],
            )
        # Combobox dropdown list
        self.root.option_add("*TCombobox*Listbox.background", t["entry_bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", t["entry_fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", t["select_bg"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", t["fg"])

        # Buttons
        self.style.configure(
            "Primary.TButton",
            background=t["accent"],
            foreground=t["accent_fg"],
            bordercolor=t["accent"],
            relief="flat",
            padding=(14, 10),
            font=("TkDefaultFont", 10, "bold"),
        )
        self.style.map(
            "Primary.TButton",
            background=[("active", t["accent"]), ("disabled", t["panel"])],
            foreground=[("disabled", t["muted"])],
        )
        self.style.configure(
            "Accent.TButton",
            background=t["panel"],
            foreground=t["fg"],
            bordercolor=t["border"],
            relief="flat",
            padding=(14, 8),
        )
        self.style.map(
            "Accent.TButton",
            background=[("active", t["select_bg"])],
        )
        self.style.configure(
            "Ghost.TButton",
            background=t["bg"],
            foreground=t["fg"],
            bordercolor=t["bg"],
            relief="flat",
            padding=(8, 4),
        )
        self.style.map(
            "Ghost.TButton",
            background=[("active", t["panel"])],
        )

        # Progressbar
        self.style.configure(
            "App.Horizontal.TProgressbar",
            background=t["accent"],
            troughcolor=t["trough"],
            bordercolor=t["bg"],
            lightcolor=t["accent"],
            darkcolor=t["accent"],
            thickness=6,
        )

        # Log widget (plain Tk, not ttk)
        if hasattr(self, "log_widget"):
            self.log_widget.configure(
                bg=t["log_bg"],
                fg=t["log_fg"],
                insertbackground=t["fg"],
                selectbackground=t["select_bg"],
                selectforeground=t["fg"],
            )

    # ------------------------------------------------------------------
    # Advanced panel toggle
    # ------------------------------------------------------------------
    def _toggle_advanced(self) -> None:
        self.advanced_open.set(not self.advanced_open.get())
        if self.advanced_open.get():
            self.adv_panel.pack(fill="x", padx=20, pady=(6, 0), before=self._action_anchor())
            self.adv_toggle_btn.configure(text="▾ Advanced pack options")
        else:
            self.adv_panel.pack_forget()
            self.adv_toggle_btn.configure(text="▸ Advanced pack options")

    def _action_anchor(self):
        # The action frame holding the Patch button is the next sibling we
        # want the advanced panel to land *before*.
        return self.run_btn.master

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------
    def pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select stock vendor.img",
            filetypes=[("Vendor image", "*.img"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                p = Path(path)
                suggested = p.with_name(p.stem + "_mali_vk13" + p.suffix)
                self.output_var.set(str(suggested))

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save patched vendor.img as",
            defaultextension=".img",
            filetypes=[("Vendor image", "*.img"), ("All files", "*.*")],
            initialfile="vendor_mali_vk13.img",
        )
        if path:
            self.output_var.set(path)

    # ------------------------------------------------------------------
    # Log streaming
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Progress bar helpers
    # ------------------------------------------------------------------
    def _reset_progress(self) -> None:
        # `stop()` is a no-op on determinate bars but harmless. Forcing
        # value=0 on every theme apply / reset removes any phantom fill
        # that some Tk themes leave behind on first paint.
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.progress.configure(mode="determinate", maximum=100, value=0)

    def _start_indeterminate(self) -> None:
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)

    # ------------------------------------------------------------------
    # Patch run
    # ------------------------------------------------------------------
    def _build_pack_options(self) -> "patcher.PackOptions | None":
        compress = self.compress_var.get().strip() or "lz4hc"
        try:
            level = int(self.level_var.get())
        except (tk.TclError, ValueError):
            level = 9
        utc_raw = self.utc_var.get().strip()
        ts = None
        if utc_raw:
            try:
                ts = int(utc_raw)
            except ValueError:
                messagebox.showwarning(
                    APP_TITLE, "UTC timestamp must be a Unix integer (or blank)."
                )
                return None
        return patcher.PackOptions(
            erofs_compression=compress,
            erofs_level=None if level == 0 else level,
            timestamp=ts,
        )

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
        pack = self._build_pack_options()
        if pack is None:
            return

        self.busy = True
        self.run_btn.configure(state="disabled", text="Patching…")
        self._start_indeterminate()
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

        t = threading.Thread(
            target=self._patch_worker,
            args=(in_path, out_path, pack),
            daemon=True,
        )
        t.start()

    def _patch_worker(self, in_path: str, out_path: str, pack) -> None:
        try:
            patcher.patch_image(
                Path(in_path),
                Path(out_path),
                repo_root=REPO_ROOT,
                log=self._log,
                pack=pack,
            )
            self.root.after(0, self._on_done, True, None, out_path)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._on_done, False, str(exc), out_path)

    def _on_done(self, ok: bool, err: str | None, out_path: str) -> None:
        self._reset_progress()
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
    raise SystemExit(main())
