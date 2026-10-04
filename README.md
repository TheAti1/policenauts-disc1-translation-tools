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
| `rex_plugin/policenauts_disc1.py` | Optional REX plugin for the ordered text-only JSON. |
| `docs/USAGE.md` | Complete command-line walkthrough. |

## Private Inputs

Supply your own source BIN/CUE and private JSON files. The audited extractor takes:

- An English-patched Disc 1 BIN.
- A base master JSON containing `entries` with source offsets and original byte spans.
- A same-length text-only edit JSON array.
- A private overrides JSON containing translations for newly discovered text and approval for candidate movie subtitles.

Keep game images, extracted text, translations, and reports outside the repository. `.gitignore` excludes common disc images and JSON dumps, but check `git status` before publishing.

See [the usage guide](docs/USAGE.md) for commands and the overrides format.

## Optional REX Plugin

Copy `rex_plugin/policenauts_disc1.py` into REX's `Eklentiler` directory. Select the audited text-only edit JSON as input and keep the audited master JSON beside it. The plugin reads **English source text from the master**, not the current Turkish values; REX writes a separate `TR_`-prefixed text-only JSON. It shows actual source line breaks as `|` to the model and restores them to JSON newlines on output. Missing optional breaks or placeholders never cause the plugin to discard an answer. The disc builder still checks fixed fields such as menu `printf` placeholders and available byte capacity.

## Verification and Limits

A successful build requires zero fitted-text cuts and zero byte mismatches. Run `verify_policenauts_image.py` afterwards; it rejects changed non-Mode2/Form1 sectors, bad EDC/ECC, bad DPK checksums, and changed timing-header bytes.

These structural checks do **not** prove that every visible word has been extracted or that every subtitle looks correct in an emulator. Text baked into images, uncertain compressed-video candidates, and runtime display timing still require playtesting. Do not claim a crash-free or complete translation based on a build report alone.

## License

The tool code is MIT-licensed. The license does not cover the game, the English patch, or user-supplied translations.
