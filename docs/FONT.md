# Policenauts PS1 Font Work

The English Disc 1 has `FONT.DPK` and `SHOTPAC.DPK` font copies. This tool exports six verified 12x12, 2-bit grayscale fixed-cell atlases. Three `KANJIFNT` atlases (one in `FONT.DPK`, two in `SHOTPAC.DPK`) contain 3,444 cells each. The other three `FONT.DPK` RB/KPR atlases are intentionally limited to the first 1,755 verified cells: the English patch places executable data later in those files, so treating their tails as glyphs would corrupt the game. Their variable-width Latin sections are preserved in the raw files but are not exposed as editable fixed cells. `RUBI.DAT` is preserved as raw bytes only because its pixel layout has not been verified. Font and atlas outputs are game-derived private data and must not be committed to this repository.

The verified fixed-cell starts in the Slowbeef v1.0 English Disc 1 files are `0xF61` for `FONT.DPK/KANJIFNT.MDB`, `0x924` for the three `FONT.DPK` RB/KPR copies, and `0xE8C` for both `SHOTPAC.DPK/KANJIFNT` copies. Each cell is exactly 36 bytes: 12 rows x 12 pixels x 2 bits, most-significant pixel first. The tool checks file sizes, DPK checksums, and source hashes instead of assuming these offsets apply to every release.

## Export And Edit

This workflow requires Pillow (`py -m pip install Pillow`) and a TrueType font with Turkish glyphs. The example uses the locally installed Arial Bold; another font can be supplied with `--ttf`.

```powershell
$tools = 'D:\Policenauts-TR\policenauts-disc1-translation-tools'
$game = 'D:\Policenauts-TR\Policenauts (Japan) (Disc 1)'
$source = Join-Path $game 'Policenauts (Japan) (Disc 1) [En by Slowbeef v1.0].bin'
$export = Join-Path $game 'font_work\export'
$edited = Join-Path $game 'font_work\turkish'

py "$tools\policenauts_font_tools.py" extract --source-bin $source --out-dir $export
py "$tools\policenauts_font_tools.py" prepare --export-dir $export --out-dir $edited `
  --ttf 'C:\Windows\Fonts\arialbd.ttf' --size 12
```

Each PNG in `$export` is an editable grayscale atlas with 64 columns and exact 12x12 cells, no gaps. `$edited` contains the same atlases with Turkish glyphs in slots 1700-1717. `mapping.json` identifies the character assigned to each slot; `turkish_preview.png` shows the displaced original cells and replacements. The 18 characters are `çÇğĞıİöÖşŞüÜâÂîÎûÛ`. The source atlases and `.raw` files remain unchanged. The renderer quantizes pixels to the game's four levels: 0, 85, 170, and 255.

To adjust pixels by hand, edit only those 18 cells in each PNG under `$edited`. Preserve the image dimensions, grayscale mode, cell positions, and four exact grayscale levels. The builder rejects changed cells outside the approved range and validates the original atlas against the raw font bytes. Do not move, resize, or resave the source atlases with a different palette.

## Apply To A New Image

```powershell
$out = Join-Path $game 'Policenauts (Japan) (Disc 1) [TR Font Candidate].bin'
py "$tools\policenauts_font_tools.py" patch-bin --source-bin $source `
  --out-bin $out --export-dir $export --edit-dir $edited
```

Use a new output path. The command refuses to overwrite the source or an existing output. It compares every input font record with the exported SHA-256, writes only approved 36-byte cells, updates DPK CRC-32/BZIP2 checksums, and repairs modified CD sectors' EDC/ECC. It does not edit any other graphics or text. It also writes a new CUE beside the BIN, using the source CUE's track layout and the new BIN filename. Open that new CUE in the emulator.

**Important:** replacing Japanese glyph pixels does **not** yet map Unicode Turkish characters to these slots. The existing text builder transliterates Turkish to ASCII. The patched font image is therefore a font candidate and is not a complete Turkish-character game patch. The runtime character-code mapping and each text renderer (dialogue, voice, movies, menus, shooting scenes) still need in-game validation. Some Japanese cells may be displayed by remaining Japanese content, so the replacement can change those screens. Keep the unmodified source image and exported `.raw` files as backups.
