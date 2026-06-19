# Policenauts Disc 1 Translation Build Tools

A small Python toolkit for rebuilding a patched **Policenauts Disc 1** PlayStation BIN/CUE image from a private text-only translation JSON.

This repository is intended for ROM-hacking research and fan-translation workflows. It contains tools only.

## What This Repo Does

- Reads a private master metadata JSON that describes where text lives in a compatible Disc 1 image.
- Reads a plain text-only translation JSON list edited by the translator.
- Fits each translation back into the original fixed-size text space.
- Preserves known in-band game text control opcodes.
- Re-encodes game text that uses negated-byte character storage.
- Patches regular game text, JXS/voiced subtitle text, and movie subtitle text.
- Rebuilds DPK CRC-32/BZIP2 checksums for touched `GAME1.DPK` / `GAME2.DPK` entries.
- Recalculates PlayStation Mode2/Form1 EDC/ECC for changed sectors.
- Verifies that every patched entry appears in the output image at the expected byte location.

## What This Repo Does Not Include

This repository does **not** include:

- Game BIN/CUE, ISO, IMG, or any other disc image.
- Original or patched game data.
- Extracted full game script text.
- A translation dump.
- Font assets or copyrighted images.
- Konami code or assets.
- The English patch itself.

You must provide your own legally obtained disc image and your own private metadata/translation JSON files.

## Repository Layout

```text
build_policenauts_tr_bin.py      Main build script
policenauts_text_format.py       Text fitting and game text encoding helpers
policenauts_disc_tools.py        PSX Mode2/Form1 EDC/ECC and DPK checksum helpers
docs/USAGE.md                    Step-by-step usage guide
examples/                        Tiny fake examples, not real game data
iso_extract/NAUTS/.gitkeep       Placeholder for private GAME1.DPK/GAME2.DPK files
```

## Required Private Files

Place these files next to the scripts before building:

```text
Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].bin
Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].cue
policenauts_texts_complete_rebuilt_all.json
policenauts_texts_tr_edit_with_movies_text_only_fit_truncated_complete.json
iso_extract/NAUTS/GAME1.DPK
iso_extract/NAUTS/GAME2.DPK
```

The JSON files and DPK files are intentionally ignored by git because they are derived from game data.

## Quick Start

```powershell
python -m json.tool ".\policenauts_texts_tr_edit_with_movies_text_only_fit_truncated_complete.json" > $null
python ".\build_policenauts_tr_bin.py"
```

The default output is:

```text
Policenauts (Japan) (Disc 1) [TR Custom].bin
Policenauts (Japan) (Disc 1) [TR Custom].cue
policenauts_tr_custom_build_report.json
policenauts_tr_custom_truncation_report.json
```

Open the generated `.cue` file in your emulator, not the `.bin` directly.

## Custom Output Names

```powershell
python ".\build_policenauts_tr_bin.py" `
  --edit ".\policenauts_texts_tr_edit_with_movies_text_only_fit_truncated_complete.json" `
  --out-bin ".\Policenauts_Disc1_TR_Test.bin" `
  --out-cue ".\Policenauts_Disc1_TR_Test.cue" `
  --report ".\Policenauts_Disc1_TR_Test_report.json" `
  --cuts ".\Policenauts_Disc1_TR_Test_cuts.json"
```

## Expected Report Fields

A successful build prints a JSON report. The most important field is:

```json
"exact_byte_mismatches": 0
```

For the tested private Disc 1 workflow, a successful build also reported:

```text
entry_count: 16251
game_sz: 12995
jxs_voice: 2977
movie_ascii: 279
dpk_crc_updates: 33
fixed_edc_sectors: 1215
fixed_ecc_sectors: 1215
```

Your numbers may differ if your metadata differs, but `exact_byte_mismatches` must be `0`.

## Character Encoding Note

This tool does not patch the game font. For the current ASCII-only font workflow, Turkish characters are converted to safe ASCII equivalents, for example:

```text
ş -> s
ğ -> g
ı -> i
ö -> o
ü -> u
ç -> c
```

If you add a real font hack later, update `policenauts_text_format.py` accordingly.

## Legal / Preservation Note

This project is provided for educational ROM-hacking research. Use it only with disc images and extracted files you are legally allowed to modify. Do not distribute copyrighted game data, full script dumps, patched ISOs/BINs, or translation dumps without permission.

## License

Tool code is released under the MIT License. This license applies only to the code in this repository, not to any game data or third-party assets.
