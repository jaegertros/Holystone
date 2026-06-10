"""holystone CLI.

    holystone strip raw/*.md -o out/
    holystone condense out/session.stripped.md -o out/
    holystone verify out/session.condensed.md --source out/session.stripped.md
    holystone init-db
    holystone embed out/session.condensed.md --project vault49 --session play2
    holystone recall "what did Mott say about the registry" --project vault49
    holystone eval --project vault49 --queries eval/queries.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="holystone", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("strip", help="deterministic cleanup pass (no model)")
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("-o", "--out", type=Path, default=Path("out"))
    p.add_argument("--keep-ooc", action="store_true")
    p.add_argument("--keep-brackets", action="store_true")

    p = sub.add_parser("condense", help="extractive second-model pass")
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("-o", "--out", type=Path, default=Path("out"))
    p.add_argument("--model", default=None)

    p = sub.add_parser("verify", help="check the condenser's extractive contract")
    p.add_argument("condensed", type=Path)
    p.add_argument("--source", type=Path, required=True)

    p = sub.add_parser("init-db", help="create hs_chunks table + FTS index")

    p = sub.add_parser("embed", help="chunk, embed as passages, upsert to Postgres")
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--project", required=True)
    p.add_argument("--session", default=None,
                   help="session label (default: file stem)")
    p.add_argument("--date", default=None, help="in-fiction date for the session")

    p = sub.add_parser("recall", help="vector + full-text recall, side by side")
    p.add_argument("query")
    p.add_argument("--project", required=True)
    p.add_argument("-k", type=int, default=5)

    p = sub.add_parser("eval", help="score a query set: vector vs full-text")
    p.add_argument("--project", required=True)
    p.add_argument("--queries", type=Path, default=Path("eval/queries.yaml"))
    p.add_argument("-k", type=int, default=5)

    args = parser.parse_args(argv)

    if args.command == "strip":
        from .strip import strip_file
        for f in args.files:
            strip_file(f, args.out, keep_ooc=args.keep_ooc,
                       keep_brackets=args.keep_brackets)
        return 0

    if args.command == "condense":
        from .condense import condense_file
        for f in args.files:
            condense_file(f, args.out, model=args.model)
        return 0

    if args.command == "verify":
        from .verify import verify_files
        return verify_files(args.condensed, args.source)

    if args.command == "init-db":
        from . import db
        with db.connect() as conn:
            db.init_schema(conn)
        return 0

    if args.command == "embed":
        from .corpus import embed_files
        embed_files(args.files, project_id=args.project,
                    session_label=args.session, in_fiction_date=args.date)
        return 0

    if args.command == "recall":
        from .corpus import recall
        recall(args.query, project_id=args.project, k=args.k)
        return 0

    if args.command == "eval":
        from .corpus import run_eval
        run_eval(project_id=args.project, queries_path=args.queries, k=args.k)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
