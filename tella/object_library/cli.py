"""Repeatable command line workflows for the semantic object library."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tella.object_library.contact_sheet import generate_contact_sheet
from tella.object_library.registry import ObjectRegistry, build_registry
from tella.object_library.service import ObjectIngestionService
from tella.object_library.sources import IconifyAdapter, NounProjectAdapter
from tella.object_library.storage import ObjectStore


def _json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tella-objects", description="Ingest and query semantic composition objects"
    )
    parser.add_argument(
        "--root", type=Path, default=None, help="Library root (or TELLA_OBJECT_LIBRARY_ROOT)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Search source APIs without downloading")
    search.add_argument("query")
    search.add_argument("--source", choices=["iconify", "noun_project", "all"], default="all")
    search.add_argument("--limit", type=int, default=20)
    ingest = commands.add_parser("ingest", help="Search, download, normalize, and register objects")
    ingest.add_argument("query")
    ingest.add_argument("--source", choices=["iconify", "noun_project", "all"], default="all")
    ingest.add_argument("--count", type=int, default=10)
    ingest.add_argument("--no-process", action="store_true")
    commands.add_parser("process", help="Process pending or failed raw objects")
    commands.add_parser("build-registry", help="Atomically rebuild manifests and semantic index")
    lookup = commands.add_parser("lookup", help="Query the local semantic registry")
    lookup.add_argument("query", nargs="?", default="")
    lookup.add_argument("--mood", action="append", default=[])
    lookup.add_argument("--context", action="append", default=[])
    lookup.add_argument("--category", action="append", default=[])
    lookup.add_argument("--source", choices=["iconify", "noun_project", "local"])
    lookup.add_argument("--include-review", action="store_true")
    lookup.add_argument("--limit", type=int, default=20)
    sheet = commands.add_parser("contact-sheet", help="Build a PNG QC sheet from raster previews")
    sheet.add_argument("--output", type=Path, required=True)
    sheet.add_argument("--columns", type=int, default=5)
    sheet.add_argument("--background", default="#1B1512")
    return parser


def _sources(value: str) -> list[str]:
    return ["iconify", "noun_project"] if value == "all" else [value]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = ObjectStore(args.root)
    service = ObjectIngestionService(store, [IconifyAdapter(), NounProjectAdapter()])
    try:
        if args.command == "search":
            _json(
                [
                    item.model_dump(mode="json", exclude={"raw_metadata"})
                    for item in service.search(args.query, args.limit, _sources(args.source))
                ]
            )
        elif args.command == "ingest":
            records = service.ingest_keyword(
                args.query,
                count=args.count,
                sources=_sources(args.source),
                process=not args.no_process,
            )
            _json([item.model_dump(mode="json") for item in records])
        elif args.command == "process":
            _json([item.model_dump(mode="json") for item in service.process_pending()])
        elif args.command == "build-registry":
            _json(build_registry(store))
        elif args.command == "lookup":
            results = ObjectRegistry.from_root(store.root).search(
                args.query,
                moods=args.mood,
                contexts=args.context,
                categories=args.category,
                source=args.source,
                production_only=not args.include_review,
                limit=args.limit,
            )
            _json([item.model_dump(mode="json") for item in results])
        elif args.command == "contact-sheet":
            print(
                generate_contact_sheet(
                    store.load_records(), args.output, args.columns, background=args.background
                )
            )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0
