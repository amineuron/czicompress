# Scientific format processing

The CziShrink GUI can opt into processing modern Nikon ND2, HDF5 and TIFF files.
The original CZI engine is unchanged. All additional formats default to off.
The portable Windows build includes its own Python runtime and native HDF5 tools;
users do not need Python, administrator rights, or separate codec installations.

## Formats and validation

- **ND2:** supports modern Ver3.0 files with standard `ImageDataSeq` frames and
  uncompressed or lossless image data. Writes zlib-compressed image chunks,
  preserves each active non-image chunk byte-for-byte, and changes only the
  compression scalar in `ImageAttributesLV!`. Before success, checks every
  metadata chunk, frame timestamp and decoded frame buffer, then uses the
  independent `nd2` reader to compare every frame, dtype, shape and calibration.
  Legacy JPEG2000 files, lossy files and unsupported tiled layouts fail explicitly.
  Validation with Nikon NIS-Elements remains a manual compatibility check.
- **HDF5:** uses the official HDF5 1.14.6 `h5repack` and `h5diff` programs. Numeric
  datasets receive shuffle and gzip. Scalars, empty datasets and object datatypes
  are copied by HDF5. Attributes and references are checked by `h5diff`; fixed-width
  dataset values are additionally compared bit-for-bit in bounded memory blocks,
  including floating-point NaN payloads and signed zeros. User blocks, external
  links, external storage and virtual datasets are
  rejected rather than relocated incorrectly. Long filter lists use an options
  file. Very long lists with whitespace in dataset names are unsupported.
- **TIFF:** uses `tifffile` and `imagecodecs` for lossless Deflate. Supports ordinary
  TIFF, BigTIFF, multi-page files, OME metadata and SubIFD pyramids. Verifies every
  page's pixel bytes and metadata tags; storage offsets, layout and compression
  tags are regenerated. Unsupported EXIF/GPS pointer structures, implicit ImageJ
  hyperstacks, and selected proprietary variants fail explicitly. Any additional
  metadata that cannot be retained fails verification instead of being dropped.

Compression levels are clamped to 1–9 for these formats; CZI keeps its Zstandard
0–22 range. The existing uncompressed modes skip already-compressed ND2 files,
TIFFs containing compressed pages, and HDF5 datasets that already have filters.
**Compress all data** recompresses supported files, and **Decompress all data**
removes supported compression. A compression result that would be larger is
replaced with a byte-for-byte copy of the original instead.

The GUI processes additional formats one at a time to bound memory consumption.
Stopping kills the worker and its child tools, waits for exit, and removes the
temporary output. Failures appear in the normal CSV and error list. The existing
copy-failed option can preserve the original file on processing failure.

All successful operations record source/output SHA-256 hashes. Native repacking
verifies decoded content rather than promising original container byte identity.
The original CZI engine's results are identified separately because its decoded
pixels are not independently checked per file. Original-file copies are verified
byte-for-byte. The CSV includes actual codec, action, verification, hashes and
destination for each file. Development uses local fixture copies; NAS input is
strictly read-only and is never a development output or cleanup target.

## Build and reproduce

1. Use the .NET SDK pinned by `../global.json` (currently 8.0.403).
2. Run `./prepare-windows-tools.ps1` from PowerShell. It downloads a project-local
   Python 3.13.17 embedded runtime, pinned Python wheels, and the official HDF5
   Windows tools into `../../artifacts/format-tools`. It does not install system
   software. Network access is required.
3. From `czishrink`, publish:

   ```powershell
   dotnet publish netczicompress.Desktop/netczicompress.Desktop.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:PublishReadyToRun=true -p:VersionSuffix=multiformat -o ../artifacts/publish/CziShrink-multiformat-win-x64
   ```

The desktop project copies the worker sources and all files under the tools
directory into `FormatTools` beside the executable. Override
`-p:ScientificToolsDirectory=...` to supply another prepared directory.
`CZISHRINK_FORMAT_TOOLS` can point to that directory for development and testing.

Run the codec tests with the prepared runtime, setting `CZISHRINK_FORMAT_TOOLS`
to the absolute tools directory:

```powershell
../../artifacts/format-tools/python/python.exe -B tests/test_formats.py -v
```

The C# tests cover GUI parameter propagation, file routing, dry-run behavior,
recursion, copying, cancellation, collision handling and the CZI engine. The codec
tests cover TIFF metadata, OME, pyramids, RGB, BigTIFF, float bit patterns, HDF5
attributes and references, filter preservation and unsupported external links.

The independent reader and codec dependencies retain their original licenses in
the bundled Python `*.dist-info` directories; Python and HDF5 license notices
are also included. The worker source is GPL-3.0-or-later. When distributing a
modified app, provide the matching source and build instructions, and retain
the original and dependency license notices.

Dependency sources and versions:

- Python 3.13.17: https://www.python.org/downloads/release/python-31317/
- HDF5 1.14.6: https://github.com/HDFGroup/hdf5/releases/tag/hdf5_1.14.6
- All Python package versions are pinned in `requirements-lock.txt`. Their source
  distributions and project links are available at
  `https://pypi.org/project/<package>/<version>/`; the bundled `*.dist-info/METADATA`
  files retain upstream project information and license notices.
- The unchanged native CZI package is `libczicompressc` 0.5.2. Its NuGet metadata
  identifies the source commit as
  https://github.com/ZEISS/czicompress/tree/40a2ba8489544626c2481ca95177b3235b5c0de7.
  The repository includes its native build configuration and original notices.
