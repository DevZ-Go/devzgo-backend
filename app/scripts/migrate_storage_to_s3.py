"""
Copy everything under local storage/ into the configured bucket, keeping the same keys.
Safe to re-run (objects are overwritten). Database URLs stay the same.

    STORAGE_BACKEND=s3 python -m app.scripts.migrate_storage_to_s3
"""

from dotenv import load_dotenv

load_dotenv()

from app.services import storage

if __name__ == "__main__":
    if not storage.is_s3():
        raise SystemExit("Set STORAGE_BACKEND=s3 and the S3_* variables first.")
    count = sum(1 for p in storage.ROOT.rglob("*") if p.is_file())
    storage.upload_dir(storage.ROOT)
    print(f"Uploaded {count} files from {storage.ROOT}/ to bucket {storage._bucket()}")
