"""Download the IEEE-CIS Fraud Detection dataset from Kaggle.

Requires a Kaggle API token. To create one:

  1. Sign in at https://www.kaggle.com
  2. Go to Settings -> API -> "Create New Token"
  3. Save the downloaded kaggle.json to:
         C:\\Users\\<you>\\.kaggle\\kaggle.json
     (or set KAGGLE_USERNAME and KAGGLE_KEY in the environment)
  4. Accept the competition rules, or the download returns 403:
         https://www.kaggle.com/c/ieee-fraud-detection/rules

Usage:
    python scripts/fetch_dataset.py                # train files only (default)
    python scripts/fetch_dataset.py --all          # train + test + submission
    python scripts/fetch_dataset.py --dest ./data/hhgoa
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_ROOT, get_settings  # noqa: E402

COMPETITION = "ieee-fraud-detection"

# The labelled training files are what an investigation graph is built from.
# The test files carry no isFraud label, so they are optional.
TRAIN_FILES = ["train_transaction.csv", "train_identity.csv"]
EXTRA_FILES = ["test_transaction.csv", "test_identity.csv", "sample_submission.csv"]

CREDENTIAL_HELP = """
No Kaggle API credentials found.

  1. Sign in at https://www.kaggle.com/settings
  2. API section -> "Create New Token"
  3. Either:
       - save the token to  {kaggle_dir}\\access_token  (newer KGAT_... tokens), or
       - set KAGGLE_API_TOKEN in your environment, or
       - use the older kaggle.json at  {kaggle_dir}\\kaggle.json
  4. Accept the competition rules (required, or downloads return 403):
     https://www.kaggle.com/c/ieee-fraud-detection/rules
"""


def have_credentials() -> bool:
    # Newer Kaggle token auth (KGAT_... tokens), checked by kaggle>=1.7 /
    # kagglesdk via get_access_token_from_env(): KAGGLE_API_TOKEN env var,
    # or a plain-text token at ~/.kaggle/access_token[.txt].
    if os.getenv("KAGGLE_API_TOKEN"):
        return True
    kaggle_dir = Path.home() / ".kaggle"
    if (kaggle_dir / "access_token").exists() or (kaggle_dir / "access_token.txt").exists():
        return True
    # Legacy username+key auth.
    if os.getenv("KAGGLE_USERNAME") and os.getenv("KAGGLE_KEY"):
        return True
    return (kaggle_dir / "kaggle.json").exists()


def human(n: int) -> str:
    step = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if step < 1024:
            return f"{step:.1f} {unit}"
        step /= 1024
    return f"{step:.1f} TB"


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Fetch IEEE-CIS fraud dataset")
    parser.add_argument("--dest", default=settings.dataset_root, help="destination directory")
    parser.add_argument("--all", action="store_true", help="also fetch test + submission files")
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    args = parser.parse_args()

    dest = Path(args.dest)
    if not dest.is_absolute():
        dest = (BACKEND_ROOT / dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    if not have_credentials():
        print(CREDENTIAL_HELP.format(kaggle_dir=Path.home() / ".kaggle"), file=sys.stderr)
        return 2

    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ImportError:
        print("The kaggle package is not installed. Run: pip install kaggle", file=sys.stderr)
        return 2

    api = KaggleApi()
    try:
        api.authenticate()
    except Exception as exc:
        print(f"Kaggle authentication failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(CREDENTIAL_HELP.format(kaggle_dir=Path.home() / ".kaggle"), file=sys.stderr)
        return 2

    wanted = TRAIN_FILES + (EXTRA_FILES if args.all else [])
    print(f"Downloading {len(wanted)} file(s) from '{COMPETITION}' into {dest}\n")

    failures: list[str] = []
    for name in wanted:
        target = dest / name
        if target.exists() and not args.force:
            print(f"  skip     {name}  (exists, {human(target.stat().st_size)})")
            continue
        print(f"  fetching {name} ...", flush=True)
        try:
            api.competition_download_file(COMPETITION, name, path=str(dest), quiet=False)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            failures.append(f"{name}: {msg}")
            print(f"  FAILED   {name}  {msg}", file=sys.stderr)
            continue

        # Kaggle delivers single files zipped.
        zipped = dest / f"{name}.zip"
        if zipped.exists():
            with zipfile.ZipFile(zipped) as zf:
                zf.extractall(dest)
            zipped.unlink()

        if target.exists():
            print(f"  ok       {name}  ({human(target.stat().st_size)})")
        else:
            failures.append(f"{name}: downloaded but not found after extraction")

    print()
    if failures:
        print("Some files failed:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\nA 403 almost always means the competition rules have not been "
            f"accepted: https://www.kaggle.com/c/{COMPETITION}/rules",
            file=sys.stderr,
        )
        return 1

    present = sorted(p for p in dest.iterdir() if p.is_file() and p.suffix == ".csv")
    total = sum(p.stat().st_size for p in present)
    print(f"Dataset ready in {dest} - {len(present)} CSV files, {human(total)}")
    for p in present:
        print(f"  {p.name:<28} {human(p.stat().st_size):>10}")
    print("\nNext: python scripts/inspect_dataset.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
