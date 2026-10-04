# Disc 1 Audit and Build Guide

This guide uses PowerShell and keeps private game data in a sibling directory. Adjust the paths if your directories differ. Python 3.10+ is recommended.

## 1. Set Paths

```powershell
$game = 'D:\Policenauts-TR\Policenauts (Japan) (Disc 1)'
$tools = 'D:\Policenauts-TR\policenauts-disc1-translation-tools'
Set-Location -LiteralPath $game
$source = Join-Path $game 'Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].bin'
$sourceCue = Join-Path $game 'Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].cue'
$master = Join-Path $game 'policenauts_texts_master_audited.json'
$edit = Join-Path $game 'policenauts_texts_tr_edit_audited.json'
```

You also need a private *base* master/edit pair with matching entry counts. This audit tool extends that pair; it does not reconstruct every existing translation from nothing. Keep the original English BIN unchanged.

## 2. Review Newly Found Text

```powershell
py "$tools\audit_policenauts_game.py" --source-bin $source `
  --master (Join-Path $game 'policenauts_texts_complete_rebuilt_all.json') `
  --report (Join-Path $game 'policenauts_game_audit_report.json')

py "$tools\audit_policenauts_movies.py" --source-bin $source `
  --master (Join-Path $game 'policenauts_texts_complete_rebuilt_all.json') `
  --report (Join-Path $game 'policenauts_movie_audit_report.json')
```

These reports may contain compressed-video false positives. Do not automatically patch every candidate. The audited extractor adds movie candidates only when they have an exact translation entry in the private overrides file.

## 3. Prepare Private Overrides

Create `policenauts_audit_overrides.json` in the game directory, **not** in the repository. Use this shape:

```json
{
  "menu": {"Original UI label": "Translated UI label"},
  "voice": {"Original voiced line": "Translated voiced line"},
  "movie": {"Original subtitle": "Translated subtitle"},
  "movie_source_cleanup": {"Raw subtitle with guard bytes": "Clean subtitle"},
  "existing": {"Existing original line": "Corrected translation"},
  "intentionally_unchanged": []
}
```

For voice/movie keys, line breaks and repeated whitespace are collapsed to one space when looking up translations. Menu strings are matched exactly first because leading spaces can be meaningful. Approve movie candidates one by one after reviewing their report and source bytes.

## 4. Generate the Audited Catalog

```powershell
py "$tools\extract_policenauts_audited.py" `
  --source-bin $source `
  --base-master (Join-Path $game 'policenauts_texts_complete_rebuilt_all.json') `
  --base-edit (Join-Path $game 'policenauts_texts_tr_edit_with_movies_text_only_fit_truncated_complete.json') `
  --overrides (Join-Path $game 'policenauts_audit_overrides.json') `
  --out-master $master --out-edit $edit `
  --report (Join-Path $game 'policenauts_texts_audit_report.json')
```

The master stores source offsets and original bytes. **Only translate the text-only `$edit` JSON array.** Do not add, delete, or reorder elements. Re-running the extractor preserves edits from an existing `$master`/`$edit` pair only when original offsets and byte spans still match. Keep a separate backup before large translation revisions.

## 5. Build Without Silent Truncation

```powershell
py -m json.tool $edit > $null
py "$tools\build_policenauts_tr_bin.py" `
  --source-bin $source --source-cue $sourceCue `
  --master $master --edit $edit `
  --out-bin (Join-Path $game 'Policenauts (Japan) (Disc 1) [TR Audited].bin') `
  --out-cue (Join-Path $game 'Policenauts (Japan) (Disc 1) [TR Audited].cue') `
  --report (Join-Path $game 'policenauts_tr_audited_build_report.json') `
  --cuts (Join-Path $game 'policenauts_tr_audited_cuts.json')
```

If a translation does not fit, the build stops and writes the cuts report. Shorten those translations and rerun. Avoid `--allow-truncate`; it intentionally discards words. Do not assume a shorter byte count guarantees good line wrapping: review the rendered game too.

## 6. Independently Verify the Image

```powershell
py "$tools\verify_policenauts_image.py" `
  --source-bin $source `
  --patched-bin (Join-Path $game 'Policenauts (Japan) (Disc 1) [TR Audited].bin') `
  --master $master
```

Expect successful exit and nonzero verified sector/DPK/timing counts. Open the **new CUE**, not an old CUE or the BIN directly, in your emulator. Play through menu, dialogue, voice and movie scenes. Static checks cannot guarantee that every image-based label is translated or that runtime subtitle timing and wrapping are correct.

## 7. Troubleshooting

- `Source bytes mismatch`: metadata does not match this exact English BIN; do not force it.
- `Text count mismatch`: your text-only edit list and master are out of sync.
- `Read Error 4097`: confirm the emulator loaded the new CUE, check the verifier, and keep both files in the same directory. A passing verifier narrows the problem but does not replace gameplay testing.
- Text still English: inspect the audit report, then determine whether it lives in menu ASCII, `.SZ`, `jXS`, movie ASCII, or an image/font asset before patching.