"""
couchbase_to_mongo.py
Migrates documents from Couchbase Community Edition → MongoDB.
Tracks start time, end time, and duration per collection and overall.

Source  : Couchbase travel_bucket (type field = airline | route | booking)
Target  : MongoDB  demo database  (airlines | routes | bookings collections)
"""

import time
from datetime import datetime, timezone, timedelta

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions
from couchbase.exceptions import CouchbaseException

from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError

# ── Settings ───────────────────────────────────────────────────────────────────
CB_HOST   = "couchbase://localhost"
CB_USER   = "Administrator"
CB_PASS   = "Password123!"
CB_BUCKET = "travel_bucket"

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB  = "demo"

BATCH_SIZE = 500   # documents per Mongo insert_many call


# ── Helpers ────────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


# ── Core migration function ────────────────────────────────────────────────────
def migrate_type(
    cb_cluster,
    cb_bucket_name: str,
    doc_type: str,
    mongo_col,
    label: str,
) -> dict:
    """
    Read all documents of doc_type from Couchbase via N1QL and
    insert them into a MongoDB collection in batches.
    Returns a timing + count summary dict.
    """
    start_dt = now_utc()
    start_ts = time.perf_counter()
    print(f"\n  [{label}] Starting   : {fmt_dt(start_dt)}")

    # Count source documents first
    count_result = list(cb_cluster.query(
        f"SELECT RAW COUNT(*) FROM `{cb_bucket_name}` WHERE type = '{doc_type}'"
    ))
    source_total = count_result[0] if count_result else 0

    # Stream all docs from Couchbase via N1QL
    query = f"SELECT META().id AS _cb_id, * FROM `{cb_bucket_name}` WHERE type = '{doc_type}'"
    rows  = cb_cluster.query(query)

    migrated = 0
    errors   = 0
    batch    = []

    for row in rows:
        # Unwrap the bucket-name wrapper Couchbase adds to SELECT *
        doc = row.get(cb_bucket_name, row)
        doc.pop("_cb_id", None)         # drop internal CB key

        # Use a meaningful Mongo _id from the doc's own ID field
        cb_id = row.get("_cb_id", "")
        doc["_id"] = cb_id if cb_id else None

        batch.append(doc)

        if len(batch) >= BATCH_SIZE:
            try:
                mongo_col.insert_many(batch, ordered=False)
                migrated += len(batch)
            except PyMongoError as exc:
                errors += len(batch)
                if errors <= 5:
                    print(f"    [WARN] batch insert error: {exc}")
            batch = []

    # Flush remaining
    if batch:
        try:
            mongo_col.insert_many(batch, ordered=False)
            migrated += len(batch)
        except PyMongoError as exc:
            errors += len(batch)

    end_dt   = now_utc()
    end_ts   = time.perf_counter()
    duration = end_ts - start_ts

    print(f"  [{label}] Completed  : {fmt_dt(end_dt)}")
    print(f"  [{label}] Duration   : {fmt_dur(duration)}")
    print(f"  [{label}] Migrated   : {migrated:,} / {source_total:,}  (errors: {errors})")

    return {
        "label":    label,
        "total":    source_total,
        "migrated": migrated,
        "errors":   errors,
        "start":    fmt_dt(start_dt),
        "end":      fmt_dt(end_dt),
        "duration": fmt_dur(duration),
        "seconds":  duration,
    }


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print()
    print("=" * 65)
    print("  Couchbase → MongoDB Migration")
    print("=" * 65)

    overall_start_dt = now_utc()
    overall_start_ts = time.perf_counter()
    print(f"\n  Overall Start : {fmt_dt(overall_start_dt)}")

    # ── Connect Couchbase ──────────────────────────────────────────
    print("\n  Connecting to Couchbase...")
    auth    = PasswordAuthenticator(CB_USER, CB_PASS)
    cluster = Cluster(CB_HOST, ClusterOptions(auth))
    cluster.wait_until_ready(timeout=timedelta(seconds=15))
    print("  Couchbase connection OK")

    # ── Connect MongoDB ────────────────────────────────────────────
    print("\n  Connecting to MongoDB...")
    mongo_client = MongoClient(MONGO_URI)
    db           = mongo_client[MONGO_DB]
    print("  MongoDB connection OK")

    # Drop target collections for a clean run
    for col_name in ("airlines", "routes", "bookings"):
        db[col_name].drop()

    # ── Migrate ────────────────────────────────────────────────────
    print("\n  Starting migration...\n" + "-" * 65)

    results = []
    results.append(migrate_type(cluster, CB_BUCKET, "airline",  db["airlines"], "Airlines"))
    results.append(migrate_type(cluster, CB_BUCKET, "route",    db["routes"],   "Routes"))
    results.append(migrate_type(cluster, CB_BUCKET, "booking",  db["bookings"], "Bookings"))

    # ── Create indexes on MongoDB ──────────────────────────────────
    print("\n  Creating MongoDB indexes...")
    db["airlines"].create_index([("airline_code", ASCENDING)], unique=True, background=True)
    db["routes"].create_index([("route_id", ASCENDING)], unique=True, background=True)
    db["routes"].create_index([("airline_code", ASCENDING)], background=True)
    db["bookings"].create_index([("booking_ref", ASCENDING)], unique=True, background=True)
    db["bookings"].create_index([("route_id", ASCENDING)], background=True)
    print("  Indexes created.")

    # ── Totals ─────────────────────────────────────────────────────
    overall_end_dt = now_utc()
    overall_end_ts = time.perf_counter()
    overall_dur    = overall_end_ts - overall_start_ts

    total_docs     = sum(r["total"]    for r in results)
    total_migrated = sum(r["migrated"] for r in results)
    total_errors   = sum(r["errors"]   for r in results)

    # ── Report ─────────────────────────────────────────────────────
    col_w = 12
    print()
    print("=" * 65)
    print("  Migration Summary")
    print("=" * 65)
    hdr = f"  {'Collection':<{col_w}} {'Docs':>8}  {'Start':<24} {'End':<24} {'Dur':>8}"
    print(hdr)
    print(f"  {'-'*col_w} {'-'*8}  {'-'*24} {'-'*24} {'-'*8}")
    for r in results:
        print(f"  {r['label']:<{col_w}} {r['migrated']:>8,}  {r['start']:<24} {r['end']:<24} {r['duration']:>8}")
    print(f"  {'-'*col_w} {'-'*8}")
    print(f"  {'TOTAL':<{col_w}} {total_migrated:>8,}")
    print()
    print(f"  Overall Start    : {fmt_dt(overall_start_dt)}")
    print(f"  Overall End      : {fmt_dt(overall_end_dt)}")
    print(f"  Overall Duration : {fmt_dur(overall_dur)}")
    print(f"  Total Errors     : {total_errors}")
    print()
    status = "SUCCESS ✓" if total_errors == 0 else f"COMPLETED WITH {total_errors} ERROR(S)"
    print(f"  Status           : {status}")
    print("=" * 65)

    mongo_client.close()


if __name__ == "__main__":
    main()
