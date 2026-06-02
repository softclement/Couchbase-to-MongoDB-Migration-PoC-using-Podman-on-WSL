"""
cb_generate_data.py
Generates a synthetic travel-industry dataset and loads it into
Couchbase Community Edition using the Python SDK.

Collections (document types inside travel_bucket):
  airline  — 10,000 docs
  route    — 20,000 docs
  booking  — 80,000 docs
  Total    — 110,000 docs
"""

import random
from datetime import datetime, timedelta
from faker import Faker

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions
from couchbase.exceptions import CouchbaseException
from datetime import timedelta as td

fake = Faker()
random.seed(42)
Faker.seed(42)

CB_HOST   = "couchbase://localhost"
CB_USER   = "Administrator"
CB_PASS   = "Password123!"
CB_BUCKET = "travel_bucket"

AIRLINE_COUNT = 10_000
ROUTE_COUNT   = 20_000
BOOKING_COUNT = 80_000

AIRCRAFT_TYPES = ["Boeing 737", "Airbus A320", "Boeing 777",
                  "Airbus A380", "Boeing 787", "Embraer E190"]
BOOKING_STATUS = ["confirmed", "confirmed", "confirmed",
                  "pending", "cancelled"]
SEAT_CLASSES    = ["economy", "economy", "economy",
                   "business", "first"]

IATA_POOL = [fake.lexify("???").upper() for _ in range(500)]


def random_iata():
    return random.choice(IATA_POOL)


def generate_airlines(n: int) -> list[dict]:
    print(f"  Generating {n:,} airlines...")
    docs = []
    for i in range(1, n + 1):
        docs.append({
            "type":         "airline",
            "airline_code": f"AL{i:05d}",
            "name":         fake.company() + " Airways",
            "country":      fake.country(),
            "iata":         random_iata(),
            "founded_year": random.randint(1950, 2020),
            "fleet_size":   random.randint(5, 400),
            "active":       random.random() > 0.1,
            "created_at":   fake.date_time_between(start_date="-3y").isoformat(),
        })
    return docs


def generate_routes(n: int, airline_codes: list[str]) -> list[dict]:
    print(f"  Generating {n:,} routes...")
    docs = []
    for i in range(1, n + 1):
        origin = random_iata()
        dest   = random_iata()
        while dest == origin:
            dest = random_iata()
        docs.append({
            "type":          "route",
            "route_id":      f"RT{i:06d}",
            "airline_code":  random.choice(airline_codes),
            "origin":        origin,
            "destination":   dest,
            "distance_km":   random.randint(200, 15_000),
            "duration_min":  random.randint(45, 960),
            "aircraft":      random.choice(AIRCRAFT_TYPES),
            "price_usd":     round(random.uniform(50, 2_500), 2),
            "active":        random.random() > 0.05,
            "created_at":    fake.date_time_between(start_date="-2y").isoformat(),
        })
    return docs


def generate_bookings(n: int, route_ids: list[str]) -> list[dict]:
    print(f"  Generating {n:,} bookings...")
    docs = []
    base = datetime.now()
    for i in range(1, n + 1):
        dep = base + timedelta(days=random.randint(-365, 365))
        docs.append({
            "type":           "booking",
            "booking_ref":    f"BK{i:08d}",
            "route_id":       random.choice(route_ids),
            "passenger_name": fake.name(),
            "email":          fake.email(),
            "phone":          fake.phone_number(),
            "seat_class":     random.choice(SEAT_CLASSES),
            "seat_number":    f"{random.randint(1,50)}{random.choice('ABCDEF')}",
            "fare_usd":       round(random.uniform(50, 3_000), 2),
            "status":         random.choice(BOOKING_STATUS),
            "departure_at":   dep.isoformat(),
            "booked_at":      fake.date_time_between(start_date="-1y").isoformat(),
        })
    return docs


def upsert_batch(collection, docs: list[dict], id_field: str):
    """Upsert a list of docs; use id_field as the document key."""
    for doc in docs:
        key = doc[id_field]
        collection.upsert(key, doc)


def main():
    print("=" * 55)
    print("  Data Generation — Couchbase")
    print("=" * 55)

    print("\n  Connecting to Couchbase...")
    auth    = PasswordAuthenticator(CB_USER, CB_PASS)
    cluster = Cluster(CB_HOST, ClusterOptions(auth))
    cluster.wait_until_ready(timeout=td(seconds=15))
    bucket  = cluster.bucket(CB_BUCKET)
    col     = bucket.default_scope().collection("_default")
    print("  Connected.\n")

    # Airlines
    airlines = generate_airlines(AIRLINE_COUNT)
    print(f"  Inserting {AIRLINE_COUNT:,} airlines...")
    upsert_batch(col, airlines, "airline_code")
    airline_codes = [a["airline_code"] for a in airlines]

    # Routes
    routes = generate_routes(ROUTE_COUNT, airline_codes)
    print(f"  Inserting {ROUTE_COUNT:,} routes...")
    upsert_batch(col, routes, "route_id")
    route_ids = [r["route_id"] for r in routes]

    # Bookings — inserted in batches to stay memory-friendly
    BATCH = 10_000
    print(f"  Inserting {BOOKING_COUNT:,} bookings (batch {BATCH:,})...")
    for start in range(0, BOOKING_COUNT, BATCH):
        end   = min(start + BATCH, BOOKING_COUNT)
        batch = generate_bookings(end - start, route_ids)
        # Fix booking ref to be globally unique across batches
        for j, doc in enumerate(batch):
            doc["booking_ref"] = f"BK{start + j + 1:08d}"
        upsert_batch(col, batch, "booking_ref")
        print(f"    {end:,} / {BOOKING_COUNT:,}")

    print()
    print("Data Generation Complete")
    print("-" * 55)
    print(f"  Airlines  : {AIRLINE_COUNT:>8,}")
    print(f"  Routes    : {ROUTE_COUNT:>8,}")
    print(f"  Bookings  : {BOOKING_COUNT:>8,}")
    total = AIRLINE_COUNT + ROUTE_COUNT + BOOKING_COUNT
    print(f"  Total     : {total:>8,}")
    print("=" * 55)


if __name__ == "__main__":
    main()
