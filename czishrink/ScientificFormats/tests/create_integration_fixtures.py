# SPDX-FileCopyrightText: 2026 Amin Khosrozadeh
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create new local fixtures; never edit an existing directory or real dataset."""
from pathlib import Path
import shutil
import sys
import h5py
import numpy as np
import tifffile

destination = Path(sys.argv[1]).resolve()
if destination.exists():
    raise FileExistsError(destination)
destination.mkdir(parents=True)
fixture = Path(__file__).resolve().parents[2] / 'netczicompressTests/mandelbrot.czi'
shutil.copyfile(fixture, destination / 'sample.czi')
with h5py.File(destination / 'sample.h5', 'w') as handle:
    data = handle.create_dataset('recording/samples', data=np.arange(200000, dtype='uint16').reshape(1000, 200))
    data.attrs['units'] = 'microvolts'
(destination / 'TIFF').mkdir()
tifffile.imwrite(destination / 'TIFF/sample.tif', np.arange(512 * 512, dtype='uint16').reshape(512, 512), metadata=None)
(destination / 'empty-folder').mkdir()
(destination / 'notes.txt').write_text('These accompanying notes must be copied unchanged.\n')
print(destination)
