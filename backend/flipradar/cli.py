"""``flipradar`` command line interface.

Examples::

    flipradar init                          # create the database and seed fee profiles/sources
    flipradar create-token --name phone     # prints a token ONCE; store it in the app
    flipradar serve --host 0.0.0.0          # dashboard + API (keep auth on when not on localhost)
    flipradar collect "iphone 15 pro" --source mercadolibre --limit 50
    flipradar import-csv precios.csv
    flipradar seed-demo                     # synthetic demo data, clearly labelled
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import __version__
from .config import Settings, get_settings
from .db import SessionLocal, create_all, init_engine, session_scope
from .services.timeutil import install_utc_listeners

log = logging.getLogger("flipradar")


def _setup(settings: Settings) -> None:
    install_utc_listeners()
    init_engine(settings)
    create_all()
    from .services.bootstrap import bootstrap

    with session_scope() as session:
        bootstrap(session, settings)


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


# ------------------------------------------------------------------------------------------------
# commands
# ------------------------------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    _setup(settings)
    _print(f"Database ready: {settings.sqlite_path or settings.database_url}")
    _print("Next: 'flipradar create-token --name <device>' and 'flipradar serve'.")
    return 0


def cmd_create_token(args: argparse.Namespace, settings: Settings) -> int:
    from .security import create_token

    _setup(settings)
    with session_scope() as session:
        try:
            row, token = create_token(session, args.name)
        except ValueError as exc:
            _err(f"error: {exc}")
            return 2
        prefix = row.token_prefix
    _print(f"Token '{args.name}' created (prefix {prefix}). Copy it now - it is not stored and cannot be shown again:")
    _print(token)
    return 0


def cmd_list_tokens(args: argparse.Namespace, settings: Settings) -> int:
    from .security import list_tokens

    _setup(settings)
    with session_scope() as session:
        rows = list_tokens(session)
        if not rows:
            _print("No tokens. Create one with 'flipradar create-token --name <device>'.")
            return 0
        _print(f"{'PREFIX':<10} {'NAME':<24} {'CREATED':<26} {'LAST USED':<26} STATUS")
        for row in rows:
            _print(
                f"{row.token_prefix:<10} {row.name[:24]:<24} {str(row.created_at)[:25]:<26} "
                f"{str(row.last_used_at or '-')[:25]:<26} {'revoked' if row.revoked else 'active'}"
            )
    return 0


def cmd_revoke_token(args: argparse.Namespace, settings: Settings) -> int:
    from .security import revoke_token

    _setup(settings)
    with session_scope() as session:
        count = revoke_token(session, args.prefix)
    if count == 0:
        _err(f"No active token with prefix '{args.prefix}'.")
        return 1
    _print(f"Revoked {count} token(s) with prefix '{args.prefix}'.")
    return 0


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    host = args.host or settings.host
    port = args.port or settings.port
    if not _is_loopback(host) and not settings.require_auth:
        _err("Refusing to listen on a non-loopback address with FLIPRADAR_REQUIRE_AUTH=false.")
        return 2
    if not _is_loopback(host):
        _print("Listening on the network: every API call needs a token (flipradar create-token).")
    uvicorn.run("flipradar.main:get_app", factory=True, host=host, port=port,
                log_level=settings.log_level.lower(), proxy_headers=False)
    return 0


def cmd_collect(args: argparse.Namespace, settings: Settings) -> int:
    from .services.collection import run_collection

    _setup(settings)
    session = SessionLocal()
    try:
        result = asyncio.run(run_collection(session, args.query, args.source or [], args.limit, settings=settings))
    finally:
        session.close()
    if not result.runs:
        _print("No configured source supports search. Configure a collector (see docs) or use --source.")
        return 1
    for run in result.runs:
        detail = f" - {run.error}" if run.error else ""
        _print(f"[{run.status:>7}] {run.source_key}: fetched {run.items_fetched}, new {run.items_new}, "
               f"updated {run.items_updated}{detail}")
    _print(f"Opportunities: {result.opportunities_created} new, {result.opportunities_updated} updated; "
           f"notifications: {result.notifications_created}")
    return 0 if any(r.status == "ok" for r in result.runs) else 1


def cmd_recompute(args: argparse.Namespace, settings: Settings) -> int:
    from .services.analysis import recompute

    _setup(settings)
    session = SessionLocal()
    try:
        result = recompute(session, None, settings=settings)
    finally:
        session.close()
    _print(f"Products analysed: {result.products_analyzed}; opportunities created {result.opportunities_created}, "
           f"updated {result.opportunities_updated}, expired {result.opportunities_expired}; "
           f"notifications {result.notifications_created}")
    return 0


def cmd_import_csv(args: argparse.Namespace, settings: Settings) -> int:
    from .services.imports import MAX_BYTES, CsvImportError, import_csv

    path = Path(args.path)
    if not path.is_file():
        _err(f"File not found: {path}")
        return 2
    if path.stat().st_size > MAX_BYTES:
        _err("File is larger than 5 MB.")
        return 2
    _setup(settings)
    session = SessionLocal()
    try:
        result = import_csv(session, path.read_bytes(), default_source=args.source, settings=settings)
    except CsvImportError as exc:
        _err(f"Import failed: {exc}")
        return 2
    finally:
        session.close()
    _print(f"Rows: {result.rows_total} - imported {result.rows_imported}, updated {result.rows_updated}, "
           f"rejected {result.rows_rejected}")
    for error in result.errors:
        _print(f"  {error}")
    return 0


def cmd_seed_demo(args: argparse.Namespace, settings: Settings) -> int:
    from .services.demo import seed_demo

    _setup(settings)
    session = SessionLocal()
    try:
        counts = seed_demo(session, settings)
    finally:
        session.close()
    _print("SYNTHETIC demo data created (titles start with [DEMO]; not real market prices): "
           + ", ".join(f"{k} {v}" for k, v in counts.items()))
    _print("include_demo_data is now ON; run 'flipradar clear-demo' to remove it.")
    return 0


def cmd_clear_demo(args: argparse.Namespace, settings: Settings) -> int:
    from .services.demo import clear_demo

    _setup(settings)
    session = SessionLocal()
    try:
        counts = clear_demo(session)
    finally:
        session.close()
    _print("Demo data removed: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


def cmd_fx_refresh(args: argparse.Namespace, settings: Settings) -> int:
    from .collectors.base import CollectorError
    from .services.fx import fx_refresh_enabled, refresh_usd_mxn

    if not fx_refresh_enabled(settings):
        _err("No FX source enabled: set FLIPRADAR_BANXICO_TOKEN (free) or FLIPRADAR_FX_PUBLIC_SOURCES_ENABLED=true.")
        return 1
    _setup(settings)
    session = SessionLocal()
    try:
        row = asyncio.run(refresh_usd_mxn(session, settings))
        session.commit()
    except CollectorError as exc:
        _err(f"FX request failed: {exc}")
        return 1
    finally:
        session.close()
    if row is None:
        return 1
    _print(f"USD/MXN {row.rate} ({row.rate_date}, {row.source})")
    return 0


def cmd_run_searches(args: argparse.Namespace, settings: Settings) -> int:
    from .services.collection import run_due_searches

    _setup(settings)
    session = SessionLocal()
    try:
        results = asyncio.run(run_due_searches(session, settings=settings))
    finally:
        session.close()
    _print(f"Ran {len(results)} due tracked search(es).")
    for result in results:
        for run in result.runs:
            _print(f"  [{run.status:>7}] {run.source_key} '{run.query}': new {run.items_new}, updated {run.items_updated}"
                   + (f" - {run.error}" if run.error else ""))
    return 0


# ------------------------------------------------------------------------------------------------
# parser
# ------------------------------------------------------------------------------------------------


Handler = Callable[[argparse.Namespace, Settings], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flipradar", description="FLIP RADAR - resale arbitrage for Mexico (MXN)")
    parser.add_argument("--version", action="version", version=f"flipradar {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, handler: Handler, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(handler=handler)
        return p

    add("init", cmd_init, "create the database and seed built-in fee profiles and sources")
    p = add("create-token", cmd_create_token, "create an API token (printed once)")
    p.add_argument("--name", required=True, help="a label for the device/person using the token")
    add("list-tokens", cmd_list_tokens, "list API tokens (prefixes only)")
    p = add("revoke-token", cmd_revoke_token, "revoke tokens by their 8-character prefix")
    p.add_argument("--prefix", required=True)
    p = add("serve", cmd_serve, "run the API + web dashboard")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p = add("collect", cmd_collect, "search a query on configured sources now")
    p.add_argument("query")
    p.add_argument("--source", action="append", help="source key (repeatable); default: all configured")
    p.add_argument("--limit", type=int, default=50)
    add("recompute", cmd_recompute, "recompute risk flags and opportunities for every product")
    p = add("import-csv", cmd_import_csv, "import listings from a CSV file (see docs/API_CONTRACT.md)")
    p.add_argument("path")
    p.add_argument("--source", default="manual", help="default source key for rows without a source column")
    add("seed-demo", cmd_seed_demo, "create the SYNTHETIC demo dataset")
    add("clear-demo", cmd_clear_demo, "remove the demo dataset")
    add("fx-refresh", cmd_fx_refresh, "fetch the latest Banxico FIX USD/MXN rate")
    add("run-searches", cmd_run_searches, "run every tracked search that is due, once")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    logging.basicConfig(level=logging.DEBUG if args.verbose else settings.log_level.upper(),
                        format="%(levelname)s %(name)s: %(message)s")
    if getattr(args, "limit", None) is not None and not 1 <= args.limit <= 200:
        parser.error("--limit must be within 1..200")
    try:
        return int(args.handler(args, settings))
    except KeyboardInterrupt:
        return 130
    except NotImplementedError:
        _err("This command needs a component that is not implemented yet in this build.")
        return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
