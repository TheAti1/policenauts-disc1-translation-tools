# Policenauts Disc 1 Translation Tools

Python tools for auditing text and rebuilding a fixed-layout PlayStation Disc 1 BIN/CUE from a privately owned English-patched image. This repository contains **code only**, not game data or a script dump.

## What Is Covered

- Existing `GAME1.DPK` / `GAME2.DPK` `.SZ` entries, with negated-byte encoding and in-band control opcodes preserved.
- Voiced `jXS` text in `PN_VOX1.PAC`, including special dash and CO2 styling bytes missed by a plain ASCII scanner.
- ASCII menu labels in `BIN.DPK/MENU.BIN`.
- Existing and reviewed new ASCII subtitles inside `MOVIE/*.MOV`.
- DPK CRC-32/BZIP2, Mode2/Form1 EDC/ECC, source-byte checks, and output-byte checks.
- A separate verifier for changed sectors, DPK checksums, and movie timing headers.

Text is still written **in place**. The tool does not expand pointers or install a Turkish font. By default, a translation that does not fit causes the build to stop; it is **not** silently truncated. Turkish-specific glyphs are transliterated to ASCII until a font patch exists.

## Files

| File | Purpose |
| --- | --- |
| `extract_policenauts_audited.py` | Extend an existing private catalog with menu, voiced, and approved movie text. |
| `audit_policenauts_game.py` | Compare source `.SZ` strings against a private catalog. |
| `audit_policenauts_movies.py` | Report possible missing movie subtitles for human review. |
| `build_policenauts_tr_bin.py` | Rebuild BIN/CUE without shifting source file extents. |
| `verify_policenauts_image.py` | Independently check the patched image. |
| `policenauts_disc_tools.py` | ISO, DPK, sector, EDC/ECC helpers. |
| `policenauts_text_format.py` | Text layout and negated-byte encoding helpers. |
| `make_policenauts_rex_json.py` | Create a fresh English-source REX JSON with empty translation fields. |
| `rex_plugin/policenauts_disc1.py` | REX plugin for the structured JSON (legacy arrays also work). |
| `docs/USAGE.md` | Complete command-line walkthrough. |

## Private Inputs

Supply your own source BIN/CUE and private JSON files. The audited extractor takes:

- An English-patched Disc 1 BIN.
- A base master JSON containing `entries` with source offsets and original byte spans.
- A same-length text-only edit JSON array.
- A private overrides JSON containing translations for newly discovered text and approval for candidate movie subtitles.

Keep game images, extracted text, translations, and reports outside the repository. `.gitignore` excludes common disc images and JSON dumps, but check `git status` before publishing.

See [the usage guide](docs/USAGE.md) for commands and the overrides format.

## Fresh REX Translation

Use `make_policenauts_rex_json.py` to create a private English-source document from the audited master. Every entry has a stable `id`, `index`, `kind`, English `source`, and initially null `translation`; the root records the format and total count. No previous Turkish edits are copied into it. Copy `rex_plugin/policenauts_disc1.py` into REX's `Eklentiler` directory, then select the English document. The plugin sends only `source` to the model and writes answers only to `translation`. REX's `TR_`-prefixed output can be passed **directly** to the builder as `--edit`. Keep the audited master JSON beside it.

Actual source line breaks are shown to the model as `|` and restored to newlines on output. Missing optional breaks or placeholders never cause the plugin to discard an answer. The builder refuses incomplete structured files, changed IDs/source text, changed fixed menu `printf` placeholders, and translations that do not fit. The old plain-array edit format remains supported.

## Verification and Limits

A successful build requires zero fitted-text cuts and zero byte mismatches. Run `verify_policenauts_image.py` afterwards; it rejects changed non-Mode2/Form1 sectors, bad EDC/ECC, bad DPK checksums, and changed timing-header bytes.

These structural checks do **not** prove that every visible word has been extracted or that every subtitle looks correct in an emulator. Text baked into images, uncertain compressed-video candidates, and runtime display timing still require playtesting. Do not claim a crash-free or complete translation based on a build report alone.

## License

The tool code is MIT-licensed. The license does not cover the game, the English patch, or user-supplied translations.
