"""Verify build inputs and stage Boost headers / official LightGBM source."""
import hashlib
import io
from pathlib import Path
import tarfile
import zipfile
import zstandard

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / '.local-runtime/gpu-validation/.venv'
ARCHIVES = {
    'khronos-opencl-icd-loader-2024.10.24-hfd05255_2.conda': '245bd150a9f0b922dc34f173027631a20f18b009604d827a796d792a79bac178',
    'libboost-headers-1.84.0-h57928b3_7.conda': 'c557021b8fa383475956effc75f3082e865d25256c0eb69fc8c0fc94ed8ec19a',
}
DEST = ENV / 'Lib/site-packages/lightgbm/bin'
assert ENV.resolve().is_relative_to(ROOT.resolve()) and not ENV.is_symlink()
for name, sha in ARCHIVES.items():
    path = ENV / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, name
    with zipfile.ZipFile(path) as archive:
        payload = next(n for n in archive.namelist() if n.startswith('pkg-'))
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(archive.read(payload))) as reader:
            with tarfile.open(fileobj=reader, mode='r|') as package:
                for member in package:
                    if name.startswith(('libboost-devel', 'libboost-headers')) and member.isfile():
                        target = ENV / 'boost184' / member.name
                        if not target.resolve().is_relative_to((ENV / 'boost184').resolve()):
                            raise ValueError('Unsafe archive entry')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(package.extractfile(member).read())
                        continue
                    if member.isfile() and member.name.startswith('Library/bin/') and member.name.endswith('.dll'):
                        target = DEST / Path(member.name).name
                        target.write_bytes(package.extractfile(member).read())
                        print(target.name, hashlib.sha256(target.read_bytes()).hexdigest())
source = ENV / 'lightgbm-4.7.0.tar.gz'
assert hashlib.sha256(source.read_bytes()).hexdigest() == 'f8e20f682c9aabd000bcf4a7ed8aa6f473c1adfecccae34ec24e823d156f4af0'
if not (ENV / 'source/lightgbm-4.7.0/CMakeLists.txt').exists():
    with tarfile.open(source) as archive:
        archive.extractall(ENV / 'source', filter='data')
# Build-only mechanical substitutions: no loss/feature/tree math is modified.
source_root = ENV / 'source/lightgbm-4.7.0'
header = source_root / 'src/treelearner/gpu_tree_learner.h'
text = header.read_text()
text = text.replace('#define BOOST_COMPUTE_USE_OFFLINE_CACHE',
                    '// Local GPU validation: optional offline disk kernel cache disabled.')
header.write_text(text)
cmake = source_root / 'CMakeLists.txt'
text = cmake.read_text()
text = text.replace('find_package(Boost 1.56.0 COMPONENTS filesystem system REQUIRED)',
                    'set(Boost_INCLUDE_DIRS ${GPU_VALIDATION_BOOST_INCLUDE})')
cmake.write_text(text)
