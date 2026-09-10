import argparse
import json
import os
import sys


def parser():
    value = argparse.ArgumentParser(description="Run the existing reviewed-template US-equity universe screen.")
    value.add_argument("--universe", default="us_equities,us_etfs", help="Comma-separated us_equities,us_etfs")
    value.add_argument("--feed", choices=("sip", "iex", "delayed_sip"), required=True, help="Configured Alpaca feed entitlement")
    value.add_argument("--days", type=int, default=750, help="Minimum listing-age completed daily bars")
    value.add_argument("--cutoff", default="2025-12-31")
    value.add_argument("--holdout-start", default="2026-01-01")
    value.add_argument("--run-date", default="2026-09-10")
    value.add_argument("--cost", default="base,adverse,severe")
    value.add_argument("--candidates", type=int, default=9)
    value.add_argument("--seed", type=int, default=42, help="Deterministic bootstrap/ranking seed")
    value.add_argument("--top", type=int, default=3)
    value.add_argument("--out", default="ascii,json", help="Comma-separated ascii,json")
    value.add_argument("--offline", action="store_true", help="Reuse cached assets/bars; never fetch")
    value.add_argument("--dry-run", action="store_true", help="Validate/estimate only; never touch database")
    value.add_argument("--symbols", help="Optional comma-separated bounded symbol subset")
    value.add_argument("--maximum-instruments", type=int, default=100)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    if args.dry_run:
        allowed_universe, allowed_cost = {"us_equities", "us_etfs"}, {"base", "adverse", "severe"}
        universe, costs = args.universe.split(","), args.cost.split(",")
        if not set(universe) <= allowed_universe or set(costs) != allowed_cost or args.days < 750 or args.candidates != 9: parser().error("invalid bounded universe specification")
        payload = {"universe": universe, "feed": args.feed, "days": args.days, "cutoff": args.cutoff, "holdout_start": args.holdout_start, "run_date": args.run_date, "cost": costs, "candidates": args.candidates, "seed": args.seed, "top": args.top, "offline": args.offline, "dry_run": True, "symbols": args.symbols.split(",") if args.symbols else None, "maximum_instruments": args.maximum_instruments}
        print(json.dumps({"dry_run": True, "database_touched": False, "specification": payload, "maximum_candidate_tests": args.maximum_instruments * args.candidates}, sort_keys=True, separators=(",", ":"))); return 0
    from backend import app
    spec = app.UniverseRunInput(universe=args.universe.split(","), feed=args.feed, days=args.days, cutoff=args.cutoff, holdout_start=args.holdout_start, run_date=args.run_date, cost=args.cost.split(","), candidates=args.candidates, seed=args.seed, top=args.top, offline=args.offline, dry_run=args.dry_run, symbols=args.symbols.split(",") if args.symbols else None, maximum_instruments=args.maximum_instruments)
    app.init_db(); run_id = str(app.uuid.uuid4()); now = app.iso(); payload = spec.model_dump(mode="json")
    with app.connect() as connection: connection.execute("INSERT INTO universe_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, "PENDING", 0, app.canonical(payload), app.digest(payload), app.UNIVERSE_ENGINE_VERSION, app.UNIVERSE_ENGINE_HASH, spec.seed, None, 0, None, None, "[]", "[]", "[]", None, 0, now, None, None))
    app.process_universe_run(run_id)
    with app.connect() as connection: row = connection.execute("SELECT * FROM universe_runs WHERE id=?", (run_id,)).fetchone()
    if row["state"] != "COMPLETED": print(row["error"], file=sys.stderr); return 1
    outputs = set(args.out.split(","))
    if "ascii" in outputs: print(row["result_ascii"])
    if "json" in outputs: print(row["result_json"])
    return 0


if __name__ == "__main__": raise SystemExit(main())
