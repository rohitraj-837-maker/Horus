"""Push bundle: contents, manifest checksums, and a clean-extraction test run.

Marked `bundle` (excluded by default; `make gate-demo` runs it): it builds the real
archive and runs the whole offline suite again from an extracted copy.
"""

import hashlib
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package_release as pr  # noqa: E402

pytestmark = pytest.mark.bundle


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dist")
    archive, manifest, entries = pr.build(pr.ROOT, out)
    return archive, manifest, entries


def test_bundle_contents(built):
    archive, _, entries = built
    assert archive.name == "horus-push-bundle.tar.gz"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    rel = {n.removeprefix("horus/") for n in names}
    for must in (
        "LICENSE",
        "README.md",
        "Makefile",
        ".gitignore",
        "pyproject.toml",
        "uv.lock",
        "horus/__init__.py",
        "demo/server.py",
        "demo/static/app.js",
        "demo/data/scenario-jet-cat.json",
        "scripts/package_release.py",
        "tests/fixtures/pmset_sleep_sample.log",
    ):
        assert must in rel, must
    for n in rel:
        parts = Path(n).parts
        assert parts[0] not in {"data", "dist", ".git", ".venv"}, n
        assert "__pycache__" not in parts and ".claude" not in parts, n
        assert Path(n).name not in {"credentials.json", ".env"}, n
    assert len(rel) == len(entries)


def test_manifest_checksums_match_archive(built):
    archive, manifest, entries = built
    text = manifest.read_text()
    assert f"archive_sha256: {hashlib.sha256(archive.read_bytes()).hexdigest()}" in text
    assert f"files: {len(entries)}" in text
    listed = {}
    for line in text.split("sha256  bytes  path\n", 1)[1].splitlines():
        digest, size, path = line.split("  ", 2)
        listed[path] = (digest, int(size))
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            data = tar.extractfile(m).read()
            digest, size = listed[m.name.removeprefix("horus/")]
            assert hashlib.sha256(data).hexdigest() == digest and len(data) == size
            assert m.uid == 0 and m.uname == ""


def test_archive_is_reproducible(built, tmp_path):
    archive, _, _ = built
    again, _, _ = pr.build(pr.ROOT, tmp_path)
    assert again.read_bytes() == archive.read_bytes()


@pytest.mark.parametrize(
    "path",
    [
        "credentials.json",
        "sub/.env",
        "data/raw/x.json",
        "a/__pycache__/m.pyc",
        "big.bin",
        "logs/run.log",
        ".claude/settings.local.json",
    ],
)
def test_deny_list_refuses(path):
    size = pr.MAX_FILE_BYTES + 1 if path == "big.bin" else 10
    with pytest.raises(pr.BundleError):
        pr.check_path(path, size)


def test_clean_extraction_passes_pytest(built, tmp_path):
    archive, _, _ = built
    with tarfile.open(archive) as tar:
        tar.extractall(tmp_path, filter="data")
    root = tmp_path / "horus"
    env = {**os.environ, "PYTHONPATH": str(root)}
    where = subprocess.run(
        [sys.executable, "-c", "import horus, demo; print(horus.__file__); print(demo.__file__)"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert all(Path(p).resolve().is_relative_to(root.resolve()) for p in where), where
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            "not trino and not network and not bundle",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert " passed" in run.stdout
