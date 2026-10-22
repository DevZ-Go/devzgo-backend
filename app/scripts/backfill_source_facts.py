"""Record source facts for projects uploaded or imported before they were kept.

Reads the stored copy of each project's last upload or import and writes only source_facts,
source_analyzed_at and source_facts_version. Files, languages and tech are left as they are.
Dry run by default; pass --apply to write.

  python -m app.scripts.backfill_source_facts            # list what would change
  python -m app.scripts.backfill_source_facts --apply
"""

import sys

from app.db import base  # noqa: F401  registers every model
from app.db.session import SessionLocal
from app.models.file import File as FileRecord
from app.models.project import Project
from app.routes.workspace_upload import reanalyse_source


def main(apply: bool) -> None:
    db = SessionLocal()
    try:
        with_files = {row[0] for row in db.query(FileRecord.project_id).distinct()}
        pending = [p for p in db.query(Project).filter(Project.source_facts.is_(None)).all()
                   if p.id in with_files or p.github_url]
        print(f"{len(pending)} project(s) without recorded source facts")
        done = missing = 0
        for project in pending:
            label = f"{project.id} {project.title!r}"
            if not apply:
                print(f"  would analyse {label}")
                continue
            try:
                if reanalyse_source(db, project):
                    done += 1
                    print(f"  analysed {label}")
                else:
                    missing += 1
                    print(f"  no stored source for {label}")
            except Exception as exc:  # one bad archive should not stop the rest
                db.rollback()
                print(f"  failed {label}: {type(exc).__name__}")
        if apply:
            print(f"analysed {done}, no stored source {missing}")
    finally:
        db.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
