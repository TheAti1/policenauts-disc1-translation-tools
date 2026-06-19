# Usage Guide

This guide explains the private file workflow for rebuilding a translated Disc 1 image.

## 1. Prepare The Folder

Clone or download this repository, then put your private files next to the scripts:

```text
Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].bin
Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].cue
policenauts_texts_complete_rebuilt_all.json
policenauts_texts_tr_edit_with_movies_text_only_fit_truncated_complete.json
iso_extract/NAUTS/GAME1.DPK
iso_extract/NAUTS/GAME2.DPK
```

Do not commit those files. `.gitignore` is set up to keep them out of git.

## 2. Edit The Translation JSON

Only edit this file:

```text
policenauts_texts_tr_edit_with_movies_text_only_fit_truncated_complete.json
```

It is a plain JSON array. Do not reorder it. Do not remove entries. Do not add entries.

## 3. Validate JSON Syntax

PowerShell:

```powershell
python -m json.tool ".\policenauts_texts_tr_edit_with_movies_text_only_fit_truncated_complete.json" > $null
```

If the command prints no error, the JSON syntax is valid.

## 4. Build The Patched Image

```powershell
python ".\build_policenauts_tr_bin.py"
```

Default outputs:

```text
Policenauts (Japan) (Disc 1) [TR Custom].bin
Policenauts (Japan) (Disc 1) [TR Custom].cue
policenauts_tr_custom_build_report.json
policenauts_tr_custom_truncation_report.json
```

## 5. Read The Build Report

```powershell
python -c "import json, pathlib; r=json.loads(pathlib.Path('policenauts_tr_custom_build_report.json').read_text(encoding='utf-8')); print('Entry count:', r['entry_count']); print('Sources:', r['counts_by_source']); print('Byte mismatches:', r['exact_byte_mismatches']); print('DPK CRC updates:', r['patch_report']['dpk_crc_updates']); print('EDC sectors:', r['patch_report']['fixed_edc_sectors']); print('ECC sectors:', r['patch_report']['fixed_ecc_sectors']); print('SHA256:', r['out_bin_sha256'])"
```

The key line must be:

```text
Byte mismatches: 0
```

## 6. Check Truncated Lines

Long translations are fitted into fixed-size original slots. If a line is too long, the tool truncates it from the end and logs it.

```powershell
python -c "import json, pathlib; cuts=json.loads(pathlib.Path('policenauts_tr_custom_truncation_report.json').read_text(encoding='utf-8')); print('Truncated entries:', len(cuts)); [print('\nIndex:', x['index'], '\nSource:', x['source'], '\nOriginal:', x['original'], '\nFitted:', x['fitted']) for x in cuts[:30]]"
```

If a line is cut badly, shorten that same index in the text-only JSON and build again.

## 7. Emulator Note

Open the generated CUE file:

```text
Policenauts (Japan) (Disc 1) [TR Custom].cue
```

Do not open an old CUE that still points at another BIN.

## 8. Troubleshooting

### Text count mismatch

The text-only JSON list has a different number of strings than the master metadata. Restore the original list length.

### Exact byte verification failed

The output is not safe. Do not use the generated BIN. Inspect the generated `.mismatches.json` file.

### Read Error 4097

Usually caused by a bad CUE/BIN pair, stale emulator cache, or damaged sector/checksum data. This tool updates DPK CRCs and Mode2/Form1 EDC/ECC for touched sectors, so also make sure the emulator is loading the newly generated CUE.
