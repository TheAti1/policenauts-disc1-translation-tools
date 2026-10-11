#!/usr/bin/env python3
"""English-language desktop workbench for private Policenauts Disc 1 projects."""

from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from policenauts_lossless_movie import encode_movie_slot
from policenauts_lossless_sz import encode_game_chunk
from policenauts_lossless_voice import encode_voice
from policenauts_turkish_font import encode_text
from policenauts_studio_core import (
    build_diagnostic, create_project, file_sha256, open_project,
    preflight_project, save_translations, scan_coverage,
)


def entry_status(entry: dict, translation: str | None) -> tuple[str, str]:
    if translation is None or not translation.strip():
        return "UNTRANSLATED", "No translation saved"
    source = entry["source"]
    old = bytes.fromhex(entry["old_bytes_hex"])
    try:
        if source == "game_sz":
            encoded, _command = encode_game_chunk(entry, translation)
            return ("GROWS" if len(encoded) > len(old) else "FITS",
                    f"{len(encoded)} / {len(old)} source bytes; bank preflight required")
        if source == "jxs_voice":
            encoded = encode_voice(translation, old)
            start = int(entry["raw_offset"]) % 2352
            capacity = ((start + len(old) + 4) & ~3) - start - 1
            return ("GROWS" if len(encoded) > capacity else "FITS",
                    f"{len(encoded)} / {capacity} aligned voice bytes; sector preflight required")
        if source == "movie_ascii":
            encoded = encode_movie_slot(entry, translation)
            return "FITS", f"{len(encoded)} / {len(old) + 1} movie slot bytes"
        if source == "bin_ascii":
            source_formats = re.findall(r"%[-+0-9.]*[A-Za-z]", entry["text_with_breaks"])
            target_formats = re.findall(r"%[-+0-9.]*[A-Za-z]", translation)
            if source_formats != target_formats:
                raise ValueError("printf placeholders differ from source")
            encoded = encode_text(translation)
            return ("GROWS" if len(encoded) > len(old) else "FITS",
                    f"{len(encoded)} / {len(old)} source bytes; overlay preflight required")
        raise ValueError(f"Unsupported source type: {source}")
    except (ValueError, AssertionError) as exc:
        return "INVALID", str(exc)


class NewProjectDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, on_create):
        super().__init__(parent)
        self.title("Create Private Disc Project")
        self.transient(parent)
        self.grab_set()
        self.on_create = on_create
        self.values = {name: tk.StringVar() for name in
                       ("Source BIN", "Source CUE", "Audited master JSON (optional)",
                        "Existing translation JSON (optional)", "New project directory")}
        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)
        for row, (name, variable) in enumerate(self.values.items()):
            ttk.Label(form, text=name).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(form, textvariable=variable, width=75).grid(row=row, column=1, sticky="ew", padx=8)
            ttk.Button(form, text="Browse", command=lambda n=name: self.browse(n)).grid(row=row, column=2)
        form.columnconfigure(1, weight=1)
        self.fonts = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Extract all supported font atlases and RUBI glyphs",
                        variable=self.fonts).grid(row=5, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Label(form, text="Private disc data and extracted text stay outside the code repository.",
                  foreground="#7a3e19").grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Button(form, text="Extract Project", command=self.submit).grid(row=7, column=2, pady=12)

    def browse(self, name: str) -> None:
        if name == "New project directory":
            path = filedialog.askdirectory(parent=self, title="Choose the parent directory")
            if path:
                self.values[name].set(str(Path(path) / "policenauts-disc1-project"))
        else:
            path = filedialog.askopenfilename(parent=self, title=name)
            if path:
                self.values[name].set(path)
                if name == "Source BIN" and not self.values["Source CUE"].get():
                    self.values["Source CUE"].set(str(Path(path).with_suffix(".cue")))

    def submit(self) -> None:
        values = {name: value.get().strip() for name, value in self.values.items()}
        required = ("Source BIN", "Source CUE", "New project directory")
        if any(not values[name] for name in required):
            messagebox.showerror("Missing input", "BIN, CUE, and a new project directory are required.", parent=self)
            return
        self.on_create(values, self.fonts.get())
        self.destroy()


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Policenauts Disc 1 Studio | Diagnostic Workbench")
        self.geometry("1150x740")
        self.project: Path | None = None
        self.manifest: dict | None = None
        self.entries: list[dict] = []
        self.document: dict | None = None
        self.document_sha256 = ""
        self.selected: int | None = None
        self.dirty = False
        self.filter = tk.StringVar()
        self._layout()

    def _layout(self) -> None:
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Create Project", command=lambda: NewProjectDialog(self, self.create)).pack(side="left")
        ttk.Button(toolbar, text="Open Project", command=self.choose_project).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Save Translations", command=self.save).pack(side="left")
        ttk.Button(toolbar, text="Validate Rows", command=self.validate_rows).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Scan Unmapped Text", command=self.scan).pack(side="left")
        ttk.Button(toolbar, text="Build Preflight", command=self.preflight).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Font Atlases", command=self.show_fonts).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Build Diagnostic BIN/CUE", command=self.build).pack(side="left")
        self.status = tk.StringVar(value="Open or create a private project. No game files are included with this app.")
        ttk.Label(self, textvariable=self.status, padding=(10, 3)).pack(fill="x")
        outer = ttk.Panedwindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True, padx=10, pady=8)
        left = ttk.Frame(outer)
        right = ttk.Frame(outer, padding=(12, 0))
        outer.add(left, weight=2)
        outer.add(right, weight=3)
        search = ttk.Frame(left)
        search.pack(fill="x", pady=(0, 7))
        ttk.Label(search, text="Filter ID or English text:").pack(side="left")
        field = ttk.Entry(search, textvariable=self.filter)
        field.pack(side="left", fill="x", expand=True, padx=7)
        field.bind("<Return>", lambda _event: self.refresh_list())
        ttk.Button(search, text="Find", command=self.refresh_list).pack(side="left")
        self.tree = ttk.Treeview(left, columns=("kind", "state", "source"), show="tree headings")
        self.tree.heading("#0", text="ID")
        self.tree.heading("kind", text="Type")
        self.tree.heading("state", text="Status")
        self.tree.heading("source", text="English source")
        self.tree.column("#0", width=84, stretch=False)
        self.tree.column("kind", width=120, stretch=False)
        self.tree.column("state", width=95, stretch=False)
        self.tree.column("source", width=380)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        ttk.Label(right, text="English source", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.source_box = tk.Text(right, height=7, wrap="word", state="disabled")
        self.source_box.pack(fill="x", pady=(5, 15))
        ttk.Label(right, text="Translation", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.translation_box = tk.Text(right, height=9, wrap="word")
        self.translation_box.pack(fill="both", expand=True, pady=(5, 7))
        self.translation_box.bind("<KeyRelease>", self.changed)
        self.fit = tk.StringVar(value="Choose an entry to inspect byte fit.")
        ttk.Label(right, textvariable=self.fit, wraplength=580).pack(anchor="w", pady=4)
        ttk.Button(right, text="Apply Row", command=self.apply_row).pack(anchor="e", pady=8)
        ttk.Label(right, text="Row fit is advisory. A diagnostic build still checks full banks, sectors and checksums.\n"
                  "Image-baked text and gameplay safety remain unverified.",
                  foreground="#8a4a23").pack(anchor="w", pady=10)

    def run_task(self, label: str, action, completed) -> None:
        self.status.set(label)
        def worker():
            try:
                value = action()
            except Exception as exc:
                self.after(0, lambda error=str(exc): (self.status.set("Failed"),
                    messagebox.showerror("Operation failed", error, parent=self)))
            else:
                self.after(0, lambda: completed(value))
        threading.Thread(target=worker, daemon=True).start()

    def create(self, values: dict[str, str], fonts: bool) -> None:
        directory = Path(values["New project directory"])
        edit = values["Existing translation JSON (optional)"]
        master = values["Audited master JSON (optional)"]
        self.run_task("Extracting and verifying private disc resources...",
                      lambda: create_project(Path(values["Source BIN"]), Path(values["Source CUE"]),
                                             Path(master) if master else None, directory,
                                             Path(edit) if edit else None, fonts),
                      lambda _manifest: self.load(directory))

    def choose_project(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Open a Policenauts Studio project")
        if path:
            self.run_task("Checking project source hash...", lambda: open_project(Path(path)),
                          lambda _value: self.load(Path(path)))

    def load(self, path: Path) -> None:
        if self.dirty and not messagebox.askyesno("Unsaved changes", "Discard unsaved row edits?", parent=self):
            return
        self.manifest, self.entries, self.document = open_project(path, verify_source_image=False)
        self.project = path
        self.document_sha256 = file_sha256(path / "translations.json")
        self.selected = None
        self.dirty = False
        self.refresh_list()
        self.status.set(f"{path} | {len(self.entries):,} catalogued rows | "
                        f"{len(self.manifest['dpk_assets']):,} extracted DPK assets | NOT RELEASE READY")

    def refresh_list(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.document is None:
            return
        query = self.filter.get().casefold().strip()
        count = 0
        for index, (entry, row) in enumerate(zip(self.entries, self.document["entries"])):
            ident = row["id"]
            if query and query not in ident.casefold() and query not in row["source"].casefold():
                continue
            state = "EDITED" if row["translation"] else "PENDING"
            snippet = row["source"].replace("\n", " ")[:70]
            self.tree.insert("", "end", iid=str(index), text=ident,
                             values=(entry["source"], state, snippet))
            count += 1
            if count >= 500:
                break

    def select(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if self.selected is not None and self.selected != index:
            self.apply_row(refresh=False)
        self.selected = index
        row = self.document["entries"][index]
        self.source_box.config(state="normal")
        self.source_box.delete("1.0", "end")
        self.source_box.insert("1.0", row["source"])
        self.source_box.config(state="disabled")
        self.translation_box.delete("1.0", "end")
        self.translation_box.insert("1.0", row["translation"] or "")
        self.update_fit()

    def changed(self, _event=None) -> None:
        self.update_fit()

    def update_fit(self) -> None:
        if self.selected is None:
            return
        text = self.translation_box.get("1.0", "end-1c")
        state, detail = entry_status(self.entries[self.selected], text)
        self.fit.set(f"{state}: {detail}")

    def apply_row(self, refresh: bool = True) -> None:
        if self.selected is None:
            return
        text = self.translation_box.get("1.0", "end-1c")
        value = text if text.strip() else None
        row = self.document["entries"][self.selected]
        if row["translation"] != value:
            row["translation"] = value
            self.dirty = True
        if refresh:
            self.refresh_list()
            self.status.set("Unsaved translation changes" if self.dirty else "No new changes")

    def save(self) -> bool:
        if self.project is None:
            return False
        self.apply_row(refresh=False)
        if not self.dirty:
            self.status.set("Translations already saved")
            return True
        try:
            self.document_sha256 = save_translations(self.project, self.document,
                                                      self.document_sha256)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return False
        self.dirty = False
        self.refresh_list()
        self.status.set("Translations saved atomically")
        return True

    def validate_rows(self) -> None:
        if self.document is None:
            return
        self.apply_row(refresh=False)
        counts = {}
        examples = []
        for entry, row in zip(self.entries, self.document["entries"]):
            state, detail = entry_status(entry, row["translation"])
            counts[state] = counts.get(state, 0) + 1
            if state in ("INVALID", "GROWS") and len(examples) < 12:
                examples.append(f"{row['id']}: {state} - {detail}")
        summary = "\n".join(f"{state}: {count}" for state, count in sorted(counts.items()))
        messagebox.showinfo("Row validation (not a full-disc proof)",
                            summary + "\n\n" + "\n".join(examples), parent=self)

    def build(self) -> None:
        if self.project is None or not self.save():
            return
        if not messagebox.askyesno("Experimental diagnostic only",
            "This rebuild may fail or crash in game despite static checks. It is NOT a final ROM.\n\n"
            "Build a separate diagnostic BIN/CUE without touching the source?", parent=self):
            return
        filename = filedialog.asksaveasfilename(parent=self, title="New diagnostic BIN",
                                                 defaultextension=".bin",
                                                 filetypes=[("PlayStation BIN", "*.bin")])
        if not filename:
            return
        path = Path(filename)
        self.run_task("Planning, rebuilding, and verifying diagnostic image...",
                      lambda: build_diagnostic(self.project, path),
                      lambda report: (self.status.set(f"Diagnostic created: {path}"),
                                      messagebox.showinfo("Diagnostic only",
                                        f"Created {path.with_suffix('.cue')}\n\n"
                                        f"Untranslated fallbacks: {report['untranslated_fallbacks']}\n"
                                        "Static checks passed. Gameplay is NOT verified.", parent=self)))

    def scan(self) -> None:
        if self.project is None or not self.save():
            return
        self.run_task("Scanning uncatalogued script, movie, overlay and voice candidates...",
                      lambda: scan_coverage(self.project),
                      lambda result: (self.status.set(f"Review report: {result[0]}"),
                                      messagebox.showinfo("Review-only coverage scan",
                                        "\n".join(f"{key}: {value}" for key, value in result[1].items())
                                        + f"\n\nReport: {result[0]}\n"
                                        "Candidates are not automatically approved as text.",
                                        parent=self)))

    def preflight(self) -> None:
        if self.project is None or not self.save():
            return
        self.run_task("Auditing full bank, sector, movie, menu and loader fit...",
                      lambda: preflight_project(self.project),
                      lambda result: (self.status.set(f"Preflight: {result[1]['status']}"),
                                      messagebox.showinfo("Build preflight",
                                        f"Status: {result[1]['status']}\n"
                                        + "\n".join(f"{key}: {value}" for key, value in result[1]["blockers"].items())
                                        + f"\n\nReport: {result[0]}", parent=self)))

    def show_fonts(self) -> None:
        if self.project is None:
            return
        manifest_path = self.project / "fonts" / "manifest.json"
        if not manifest_path.is_file():
            messagebox.showinfo("Fonts not exported", "Create a project with font extraction enabled.", parent=self)
            return
        from PIL import Image, ImageTk
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        window = tk.Toplevel(self)
        window.title("Extracted Font Atlases | Read-only Preview")
        window.geometry("850x780")
        top = ttk.Frame(window, padding=10)
        top.pack(fill="x")
        choices = [f"{item['container']} / {item['name']}" for item in files]
        selected = tk.StringVar(value=choices[0])
        ttk.Combobox(top, values=choices, textvariable=selected, state="readonly",
                     width=38).pack(side="left")
        ttk.Label(top, text="12x12 cells; preview only. Occupied cells need explicit review.",
                  foreground="#8a4a23").pack(side="left", padx=12)
        canvas = tk.Canvas(window, background="#20232b")
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def display(_event=None):
            item = files[choices.index(selected.get())]
            path = self.project / "fonts" / item["image"]
            picture = Image.open(path).convert("RGB")
            photo = ImageTk.PhotoImage(picture, master=window)
            canvas.delete("all")
            canvas.image = photo
            canvas.create_image(14, 14, anchor="nw", image=photo)
            canvas.configure(scrollregion=(0, 0, picture.width + 28, picture.height + 28))

        selected.trace_add("write", lambda *_args: display())
        display()


def main() -> int:
    Studio().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
