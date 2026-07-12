"""Friendly control panel for lethimcook — no terminal required.

A small tkinter window (stdlib, zero extra dependencies) that drives the
soundtrack through the logic in `controls.py`: play/pause, temporary
enable/disable, a volume slider, "change song", and install/uninstall.

It polls the daemon's real state once a second, so the buttons always
reflect what the music is actually doing — even when another Claude Code
session or the CLI changed it.

Launch:  python scripts/gui.py   (or double-click controls.bat / controls.sh)
"""
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import controls  # noqa: E402

POLL_MS = 1000


class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.busy = False  # guards long install/uninstall subprocess runs
        self._suppress_volume_write = False

        root.title("lethimcook 🧑‍🍳")
        root.resizable(False, False)

        frame = ttk.Frame(root, padding=16)
        frame.grid(sticky="nsew")

        self.status_var = tk.StringVar(value="checking…")
        ttk.Label(frame, textvariable=self.status_var, font=("", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )

        self.play_btn = ttk.Button(frame, text="Play", command=self.on_toggle_play)
        self.play_btn.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=4)

        self.enable_btn = ttk.Button(frame, text="Disable", command=self.on_toggle_enabled)
        self.enable_btn.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="Volume").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.volume_label = ttk.Label(frame, text="100%")
        self.volume_label.grid(row=2, column=1, sticky="e", pady=(12, 0))

        self.volume = tk.IntVar(value=100)
        self.volume_scale = ttk.Scale(
            frame, from_=0, to=100, orient="horizontal",
            variable=self.volume, command=self.on_volume_move,
        )
        self.volume_scale.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.volume_scale.bind("<ButtonRelease-1>", self.on_volume_commit)

        ttk.Button(frame, text="Change song…", command=self.on_change_song).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=4
        )

        ttk.Separator(frame, orient="horizontal").grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=10
        )

        self.install_btn = ttk.Button(frame, text="Install / re-run setup", command=self.on_install)
        self.install_btn.grid(row=6, column=0, columnspan=2, sticky="ew", pady=4)

        self.uninstall_btn = ttk.Button(frame, text="Uninstall", command=self.on_uninstall)
        self.uninstall_btn.grid(row=7, column=0, columnspan=2, sticky="ew", pady=4)

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self.refresh()

    # --- rendering --------------------------------------------------------

    def refresh(self):
        """Pull the real daemon state and repaint; reschedules itself."""
        if not self.busy:
            status = controls.get_status()
            self._render(status)
        self.root.after(POLL_MS, self.refresh)

    def _render(self, status):
        if not status["installed"]:
            headline = "Not installed — click Install below"
        elif not status["enabled"]:
            headline = "Disabled (muted)"
        elif status["playing"]:
            headline = "🎶 Playing"
        elif status["running"]:
            headline = "Paused"
        else:
            headline = "Idle (daemon not running)"
        if not status["song"]:
            headline += "  ·  no song yet"
        self.status_var.set(headline)

        self.play_btn.config(
            text="Pause" if status["playing"] else "Play",
            state="normal" if status["enabled"] else "disabled",
        )
        self.enable_btn.config(text="Disable" if status["enabled"] else "Enable")

        # Reflect volume from disk without triggering a write back.
        self._suppress_volume_write = True
        self.volume.set(status["volume"])
        self.volume_label.config(text="%d%%" % status["volume"])
        self._suppress_volume_write = False

    # --- handlers ---------------------------------------------------------

    def on_toggle_play(self):
        controls.toggle_play()
        self.refresh_now()

    def on_toggle_enabled(self):
        controls.set_enabled(not controls.get_status()["enabled"])
        self.refresh_now()

    def on_volume_move(self, _value):
        self.volume_label.config(text="%d%%" % int(float(self.volume.get())))

    def on_volume_commit(self, _event):
        if self._suppress_volume_write:
            return
        controls.set_volume(int(float(self.volume.get())))

    def on_change_song(self):
        path = filedialog.askopenfilename(
            title="Pick your thinking song",
            filetypes=[("MP3 audio", "*.mp3"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            controls.set_song(path)
        except ValueError as err:
            messagebox.showerror("lethimcook", str(err))
            return
        messagebox.showinfo("lethimcook", "Song updated. It plays on the next turn.")
        self.refresh_now()

    def on_install(self):
        self._run_async("Installing…", controls.install, "Install")

    def on_uninstall(self):
        if not messagebox.askyesno(
            "lethimcook",
            "Remove the hooks, stop the music daemon, and clean up?\n"
            "(Your other Claude settings are left untouched.)",
        ):
            return
        self._run_async("Uninstalling…", controls.uninstall, "Uninstall")

    # --- long-running work ------------------------------------------------

    def _run_async(self, banner, action, label):
        """Run install/uninstall off the UI thread so the window stays live."""
        if self.busy:
            return
        self.busy = True
        self.status_var.set(banner)
        for btn in (self.install_btn, self.uninstall_btn, self.play_btn, self.enable_btn):
            btn.config(state="disabled")

        def worker():
            try:
                result = action()
                ok = result.returncode == 0
                message = (result.stdout or result.stderr or "").strip()
            except Exception as err:  # never let a worker crash the app
                ok, message = False, str(err)
            self.root.after(0, lambda: self._finish(label, ok, message))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, label, ok, message):
        self.busy = False
        for btn in (self.install_btn, self.uninstall_btn, self.play_btn, self.enable_btn):
            btn.config(state="normal")
        if ok:
            messagebox.showinfo("lethimcook", "%s complete." % label)
        else:
            messagebox.showerror("lethimcook", "%s failed:\n%s" % (label, message or "unknown error"))
        self.refresh_now()

    def refresh_now(self):
        if not self.busy:
            self._render(controls.get_status())


def main():
    root = tk.Tk()
    ControlPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
