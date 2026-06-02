"""
cb_validation.py
Compares document counts between Couchbase (source) and MongoDB (target).
Exits with code 0 on PASS, code 1 on FAIL.
"""

import sys
import time
from datetime import timedelta

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions
from couchbase.exceptions import CouchbaseException

from pymongo import MongoClient

CB_HOST   = "couchbase://localhost"
CB_USER   = "Administrator"
CB_PASS   = "Password123!"
CB_BUCKET = "travel_bucket"

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB  = "demo"

N1QL_RETRIES    = 3
N1QL_RETRY_WAIT = 3   # seconds


def cb_count(cluster, bucket: str, doc_type: str) -> int:
    query = f"SELECT RAW COUNT(*) FROM `{bucket}` WHERE type = '{doc_type}'"
    for attempt in range(1, N1QL_RETRIES + 1):
        try:
            rows = list(cluster.query(query))
            return rows[0] if rows else 0
        except CouchbaseException as exc:
            if attempt < N1QL_RETRIES:
                print(f"    [RETRY {attempt}] N1QL error ({exc}). Retrying in {N1QL_RETRY_WAIT}s...")
                time.sleep(N1QL_RETRY_WAIT)
            else:
                raise


def main():
    print()
    print("=" * 50)
    print("  Couchbase → MongoDB Validation")
    print("=" * 50)

    # ── Couchbase counts ────────────────────────────────────────────
    print("\n  Connecting to Couchbase...")
    auth    = PasswordAuthenticator(CB_USER, CB_PASS)
    cluster = Cluster(CB_HOST, ClusterOptions(auth))
    cluster.wait_until_ready(timeout=timedelta(seconds=15))

    cb_counts = {
        "airline": cb_count(cluster, CB_BUCKET, "airline"),
        "route":   cb_count(cluster, CB_BUCKET, "route"),
        "booking": cb_count(cluster, CB_BUCKET, "booking"),
    }
    print("  Couchbase counts retrieved.")

    # ── MongoDB counts ──────────────────────────────────────────────
    print("\n  Connecting to MongoDB...")
    mongo_client = MongoClient(MONGO_URI)
    db           = mongo_client[MONGO_DB]

    mongo_counts = {
        "airline": db["airlines"].count_documents({}),
        "route":   db["routes"].count_documents({}),
        "booking": db["bookings"].count_documents({}),
    }
    mongo_client.close()
    print("  MongoDB counts retrieved.")

    # ── Compare ─────────────────────────────────────────────────────
    print()
    print("-" * 50)
    print(f"  {'Type':<12} {'Couchbase':>12} {'MongoDB':>10} {'Match':>6}")
    print(f"  {'-'*12} {'-'*12} {'-'*10} {'-'*6}")

    all_pass = True
    for doc_type, cb_label, mongo_label in [
        ("airline", "Airlines", "airlines"),
        ("route",   "Routes",   "routes"),
        ("booking", "Bookings", "bookings"),
    ]:
        c = cb_counts[doc_type]
        m = mongo_counts[doc_type]
        ok = "✓" if c == m else "✗"
        if c != m:
            all_pass = False
        print(f"  {cb_label:<12} {c:>12,} {m:>10,} {ok:>6}")

    cb_total    = sum(cb_counts.values())
    mongo_total = sum(mongo_counts.values())
    print(f"  {'-'*12} {'-'*12} {'-'*10}")
    print(f"  {'TOTAL':<12} {cb_total:>12,} {mongo_total:>10,}")
    print()

    if all_pass:
        print("  VALIDATION PASSED ✓")
        print("=" * 50)
        sys.exit(0)
    else:
        print("  VALIDATION FAILED ✗")
        print("  One or more counts do not match.")
        print("=" * 50)
        sys.exit(1)


if __name__ == "__main__":
    main()
