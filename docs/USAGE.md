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

The master stores source offsets and original bytes. The `$edit` array is the older direct-edit format; do not add, delete, or reorder its elements. Re-running the extractor preserves edits from an existing `$master`/`$edit` pair only when original offsets and byte spans still match. Keep a separate backup before large translation revisions.

### Start All Translations Over With REX

```powershell
$rexInput = Join-Path $game 'policenauts_rex_english.json'
py "$tools\make_policenauts_rex_json.py" --master $master --out $rexInput
```

The generator refuses to overwrite an existing file unless you explicitly pass `--overwrite`. The actual file contains 16,370 entries in this project. Its root `count` equals the array length; each entry has a stable `id` and `index`, a `kind`, an English `source`, and a null `translation`. This is a new English-only starting point, not a copy of the old Turkish edit.

The JSON shape is deliberately explicit:

```json
{
  "format": "policenauts-rex-v1",
  "count": 1,
  "source_language": "en",
  "target_language": "tr",
  "entries": [
    {
      "id": "p00000",
      "index": 0,
      "kind": "menu",
      "source": "OK",
      "translation": null
    }
  ]
}
```

The example is a valid one-entry illustration; the real file has `count: 16370`. `count` is the total number of entries, not text to translate. `id`/`index` fix the original order, `kind` classifies the text, and `source` is read-only English. Only `translation` is the editable Turkish field. Keep JSON escapes such as `\n` intact; REX handles line breaks for the model.

Install `rex_plugin/policenauts_disc1.py` in the REX `Eklentiler` folder and select that plugin. Open `$rexInput` in REX, not `$master` or the old Turkish `$edit`. The plugin sends only `source` to the model and writes each answer into the matching `translation` field. In non-overwrite mode REX creates `TR_policenauts_rex_english.json` beside the input. To build that result, set:

```powershell
$edit = Join-Path $game 'TR_policenauts_rex_english.json'
```

Missing AI answers leave `translation: null`; the builder stops until all entries have nonblank translations. It also checks IDs, order, source text, and entry kind against the master. REX never changes those fields.

The source has real JSON newline characters. The plugin displays each one to the model as `|`, then writes it back as a real newline. `%d` remains a short, separate runtime placeholder. The plugin does not reject an answer just because the model omitted a break or placeholder; however, the disc builder may reject a changed `printf` placeholder in a fixed menu field. Review the cuts report before rebuilding.

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
- `Text count mismatch`: your edit list or structured REX JSON and master are out of sync.
- `REX JSON has untranslated entries`: finish the reported `translation` fields in REX or manually before building. Do not fill missing entries with the old Turkish file if you want a fresh translation.
- `Read Error 4097`: confirm the emulator loaded the new CUE, check the verifier, and keep both files in the same directory. A passing verifier narrows the problem but does not replace gameplay testing.
- Text still English: inspect the audit report, then determine whether it lives in menu ASCII, `.SZ`, `jXS`, movie ASCII, or an image/font asset before patching.
