# Policenauts Disc 1 Studio

Policenauts Disc 1 Studio is an English-language, local-only workbench for
extracting a **private** English-patched PlayStation BIN/CUE into a source-pinned
editing project. It reconstructs 16,533 audited text entries from the user's
own BIN using a bundled **text-free offset map**, then exports every DPK entry,
six 12x12 font atlases, and the separate 118-glyph 6x6 RUBI atlas. It can
reassemble an unchanged disc bit-for-bit and attempt a **diagnostic** translated
build with strict static checks. It does **not** certify a complete localization
or crash-free gameplay.

No game image, text dump, font bitmap, or translation is included in this
repository. Do not commit an extracted project or output image to GitHub.

## Requirements

- Python 3.10 or newer, Tkinter, and Pillow (`python -m pip install Pillow`).
- A privately owned English-patched Policenauts Disc 1 BIN and matching CUE.
- The bundled geometry map supports only the exact English-patched BIN with
  SHA-256 `bb73fc1df85241bc5f5faab6912c2d34b77c59a757245d8aa29416e80952eb45`.
  A separately audited private catalog may be supplied instead for another
  verified Disc 1 build; arbitrary image versions are not supported.
- Optional structured translation JSON with the same IDs and source strings.
- Free disk space for the DPK/font export (roughly 60 MB for the known disc),
  and another full BIN's size for any rebuilt image.

This workbench presently targets **Disc 1 only**. The public geometry contains
byte positions and lengths but **no source dialogue**. It reconstructs those
catalogued strings from the user's own image. It does not prove that every
visible text or movie pixel is in the map, nor handle other regional releases.
Wrong-source images are rejected before extraction.

## GUI

```powershell
py policenauts_studio.py
```

1. Choose **Create Project** and select the source BIN, CUE, optional audited master,
   optional prior translations, and a **new** project directory outside the
   repository. Neither the source image nor prior translations are modified.
2. Use the filter to find an ID or English phrase. Edit only the Translation
   field. **Apply Row** updates the in-memory project; **Save Translations**
   atomically writes `translations.json`. If another program changed that
   file meanwhile, saving refuses rather than overwriting it.
   **Font Atlases** opens a read-only in-app preview of the six exported main
   fonts; the original PNG and raw bytes remain in the project directory.
3. **Validate Rows** reports missing, invalid, and potentially growing rows.
   This is a local hint, not a whole-bank or runtime proof. Preserve formatting
   placeholders such as `%d` and inspect line breaks in context.
4. **Scan Unmapped Text** writes a private `coverage_review_NNN.json` report.
   Short script fragments, overlay strings, and movie candidates need manual
   classification; never import all matches blindly.
5. **Build Preflight** checks full-bank and sector fit, movie/menu conflicts,
   known `.SZ` boundary risks, and fixed-width loader fragments without
   writing a BIN. It saves a private detailed report. The CLI exits nonzero
   when the result is `BLOCKED`.
6. **Build Diagnostic BIN/CUE** creates a *new* image only if the experimental
   assemblers and structural checks accept the translations. It may refuse
   apparently reasonable text because some loader fragments have fixed
   widths or addresses. A successful diagnostic still needs gameplay QA.

The GUI intentionally says **Diagnostic**, not Final ROM. It never runs an
emulator or plays the game on the user's behalf.

## CLI

```powershell
py policenauts_studio_cli.py extract `
  --source-bin "D:\games\Policenauts English.bin" `
  --source-cue "D:\games\Policenauts English.cue" `
  --edit "D:\private\translations.json" `
  --project "D:\private\policenauts_studio_project"

py policenauts_studio_cli.py verify `
  --project "D:\private\policenauts_studio_project"

py policenauts_studio_cli.py scan `
  --project "D:\private\policenauts_studio_project"

py policenauts_studio_cli.py preflight `
  --project "D:\private\policenauts_studio_project"

py policenauts_studio_cli.py roundtrip `
  --project "D:\private\policenauts_studio_project" `
  --out-bin "D:\private\unchanged_roundtrip.bin"

py policenauts_studio_cli.py build-diagnostic `
  --project "D:\private\policenauts_studio_project" `
  --out-bin "D:\private\translation_diagnostic.bin"
```

`roundtrip` applies **zero translations** and requires the new BIN's SHA-256 to
equal the source image's. It proves source copying and CUE reconstruction, not
translation reinsertion. `build-diagnostic` applies catalogued edits through
the experimental `.SZ`, voice, menu, movie, and font assemblers; it reports
untranslated source fallbacks and checks changed Mode2/Form1 sectors, DPK
checksums, and selected movie timing markers. No command overwrites an input
or an existing output.

For another audited English Disc 1 revision, add `--master` to `extract` with
your private catalog. Do not publish a catalog containing game dialogue.

## Reproducible Text-Free Geometry

Maintainers can generate the public offset map from a private audited catalog
without copying English dialogue into the repository:

```powershell
py policenauts_studio_geometry.py generate `
  --source-bin "D:\private\English Disc 1.bin" `
  --master "D:\private\audited_master.json" `
  --out-geometry "D:\private\new_geometry.json"

py policenauts_studio_geometry.py compare `
  --source-bin "D:\private\English Disc 1.bin" `
  --geometry "D:\private\new_geometry.json" `
  --master "D:\private\audited_master.json"
```

The bundled `data/disc1_english_geometry.json` contains only kinds, byte
lengths, addresses, DPK filenames, and movie prefix lengths. On the known
English-patched source, all 16,533 reconstructed entries match the audited
catalog's source bytes, visible text, control text, locations, and voice
block indices. This is **catalog reconstruction**, not proof of total
on-screen coverage. Keep the generated private `catalog.json` out of Git.

## Project Layout

```text
project.json             Source hashes, DPK inventory, known coverage gaps
catalog.json             Private audited English source and byte locations
translations.json        Editable translation fields, stable IDs
assets/*.DPK/*           Exact extracted DPK entries, indexed by record number
fonts/*.raw, *.png       Six private 12x12 font exports and manifest
rubi/*.raw, *.png        Separate 6x6 RUBI export and manifest
coverage_review_*.json   Optional review-only candidate scans
```

Source DPK records are compared with their on-disc CRC-32/BZIP2 fields. The
known English source has some nonmatching DPK fields in `DATA.DPK`; Studio
records each mismatch instead of changing the original bytes or pretending
that all source checksums pass. Exported assets are read-only references for
this version. Editing a raw asset will make `verify`/`build-diagnostic` refuse
it. Generic DPK replacement without format-specific pointer analysis would
be unsafe.

If extraction fails, the unfinished new directory is deliberately left in
place for inspection rather than deleted automatically. Choose a different
new directory after resolving the error; a valid project has `project.json`.

## Fonts and Encoding

The six main atlas PNGs show 12x12, four-level grayscale cells. The RUBI atlas
is a separate 6x6 font and is currently **inspection-only**. Keep the original
`.raw` files: they anchor every pixel and SHA-256 check. Directly editing an
atlas PNG does not automatically change the disc. `policenauts_font_tools.py`
offers an explicit guarded `prepare`/`patch-bin` experiment for approved
cells, but occupied cells are rejected by default. Do not overwrite occupied
Japanese glyphs simply because a replacement character looks correct in the
atlas; those slots can contain live glyphs or pre-rendered fragments.

The current experimental Turkish encoder maps a limited set of Turkish glyphs.
Every edit must still pass encoding, byte-fit, font, and on-screen width QA.
The GUI never silently removes accents or cuts text.

## Why a Diagnostic Can Still Fail in Game

- Existing `.SZ` banks have script references beyond recognized text pointers.
- Voice sectors have fixed record chains and timing; one incomplete source
  record still needs review.
- Movie subtitles can be embedded in compressed video, and some words are
  baked into images rather than stored as editable strings.
- Menu overlays contain executable MIPS code and apparently empty space can
  be used at runtime; the title save-list incident is a concrete example.
- A static sector/CRC pass cannot prove every game path, font glyph, menu
  layout, or animation timing.

For a release-quality tool, the remaining work is to establish complete text
and image coverage, formalize every address class, remove unapproved source
fallbacks, and run end-to-end game QA. Until those are done, do not label a
translated output as final or crash-free.
