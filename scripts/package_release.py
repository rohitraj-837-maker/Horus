"""Build the single-file push bundle: dist/horus-push-bundle.tar.gz + dist/PUSH_MANIFEST.txt.

    uv run python scripts/package_release.py [--out dist]

File list = what git would push from the working tree: tracked files plus untracked
files that are not gitignored (`git ls-files --cached --others --exclude-standard`).
On top of .gitignore, a deny-list refuses secrets and bulk data outright (the build
FAILS rather than silently skipping), and any single file > 5 MB fails the build.

The archive is reproducible: sorted entries under `horus/`, fixed owner, mtime
from the HEAD commit, gzip header without a timestamp. Nothing is uploaded; this
only writes into dist/.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_NAME = "horus-push-bundle.tar.gz"
MANIFEST_NAME = "PUSH_MANIFEST.txt"
PREFIX = "horus"
MAX_FILE_BYTES = 5_000_000

# bulk data / outputs: excluded at the repo root only (horus/data/ and cases/ are small, bundled)
ROOT_EXCLUDED_DIRS = {"data", "dist", "build"}
# tool/cache/secret directories: excluded at any depth
EXCLUDED_DIRS = {
    ".git", ".venv", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".claude", "claude",
}  # fmt: skip
FORBIDDEN_NAMES = {"credentials.json", ".env", "settings.local.json", ".netrc", "id_rsa"}
FORBIDDEN_SUFFIXES = {".parquet", ".pyc", ".log", ".pid", ".out", ".bundle", ".pem", ".key"}


class BundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Entry:
    path: str  # repo-relative, POSIX
    size: int
    sha256: str
    executable: bool


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def candidate_files(root: Path = ROOT) -> list[str]:
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({p for p in out.split("\0") if p and (root / p).is_file()})


def check_path(path: str, size: int) -> None:
    parts = Path(path).parts
    name = parts[-1]
    if EXCLUDED_DIRS.intersection(parts[:-1]) or (
        len(parts) > 1 and parts[0] in ROOT_EXCLUDED_DIRS
    ):
        raise BundleError(f"refusing {path}: inside an excluded directory")
    if name in FORBIDDEN_NAMES or name.startswith(".env"):
        raise BundleError(f"refusing {path}: secret/credential file name")
    fixture = parts[:2] == ("tests", "fixtures")  # recorded real samples the tests need
    # fitted per-type normalisation (9 KB each): committed because the bulk data that
    # fits it is gone; small Parquet fixtures are recorded real samples like the logs
    model = len(parts) == 2 and parts[0] == "models" and name.startswith("type_norm")
    allowed = (fixture and Path(name).suffix in {".log", ".parquet"}) or (
        model and name.endswith(".parquet")
    )
    if Path(name).suffix in FORBIDDEN_SUFFIXES and not allowed:
        raise BundleError(f"refusing {path}: data/log/binary type")
    if size > MAX_FILE_BYTES:
        raise BundleError(f"refusing {path}: {size:,} bytes > {MAX_FILE_BYTES:,}")


def collect(root: Path = ROOT, files: list[str] | None = None) -> list[Entry]:
    entries = []
    for rel in files if files is not None else candidate_files(root):
        p = root / rel
        data = p.read_bytes()
        check_path(rel, len(data))
        entries.append(
            Entry(rel, len(data), hashlib.sha256(data).hexdigest(), bool(p.stat().st_mode & 0o111))
        )
    if not entries:
        raise BundleError("no files to bundle")
    return entries


def write_archive(root: Path, entries: list[Entry], archive: Path, mtime: int) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for e in entries:
            info = tarfile.TarInfo(f"{PREFIX}/{e.path}")
            info.size, info.mtime = e.size, mtime
            info.mode = 0o755 if e.executable else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO((root / e.path).read_bytes()))
    tmp = archive.with_suffix(".tmp")
    with open(tmp, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as gz:
        gz.write(buf.getvalue())
    tmp.replace(archive)


def manifest_text(entries: list[Entry], archive: Path, commit: str, dirty: bool) -> str:
    data = archive.read_bytes()
    total = sum(e.size for e in entries)
    lines = [
        "Horus push bundle manifest",
        f"archive: {archive.name}",
        f"archive_sha256: {hashlib.sha256(data).hexdigest()}",
        f"archive_bytes: {len(data)}",
        f"files: {len(entries)}",
        f"total_uncompressed_bytes: {total}",
        f"source_commit: {commit}{' (working tree had uncommitted changes)' if dirty else ''}",
        "",
        "sha256  bytes  path",
    ]
    lines += [f"{e.sha256}  {e.size}  {e.path}" for e in entries]
    return "\n".join(lines) + "\n"


def build(root: Path = ROOT, out_dir: Path | None = None) -> tuple[Path, Path, list[Entry]]:
    out = out_dir or root / "dist"
    out.mkdir(parents=True, exist_ok=True)
    entries = collect(root)
    commit = _git(root, "rev-parse", "--short", "HEAD").strip()
    dirty = bool(_git(root, "status", "--porcelain").strip())
    mtime = int(_git(root, "log", "-1", "--format=%ct").strip())
    archive = out / ARCHIVE_NAME
    write_archive(root, entries, archive, mtime)
    manifest = out / MANIFEST_NAME
    manifest.write_text(manifest_text(entries, archive, commit, dirty))
    return archive, manifest, entries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=None, help="output directory (default: dist/)")
    args = ap.parse_args()
    try:
        archive, manifest, entries = build(ROOT, args.out)
    except BundleError as e:
        print(f"bundle: {e}", file=sys.stderr)
        return 1
    total = sum(e.size for e in entries)
    print(
        f"{archive.relative_to(ROOT) if archive.is_relative_to(ROOT) else archive}: "
        f"{len(entries)} files, {total / 1e6:.2f} MB uncompressed, "
        f"{archive.stat().st_size / 1e6:.2f} MB compressed"
    )
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
