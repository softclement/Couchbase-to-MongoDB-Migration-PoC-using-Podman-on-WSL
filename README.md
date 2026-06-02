# Couchbase to MongoDB Migration PoC

> **Platform:** WSL Ubuntu + Podman &nbsp;|&nbsp; **Language:** Python 3.12 &nbsp;|&nbsp; **Status:** Learning PoC

A hands-on proof of concept demonstrating a NoSQL-to-NoSQL migration from **Couchbase 7.6 Community Edition** to **MongoDB 8** using Python SDKs running in Podman containers on WSL.

Simulates a travel-industry dataset (airlines, routes, bookings) and covers data generation, migration with **per-collection elapsed-time tracking**, validation, and mongosh verification end-to-end.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  WSL Ubuntu  ·  Podman (nosql-net bridge)                           │
│                                                                     │
│  ┌──────────────────────┐          ┌───────────────────────────┐   │
│  │  Couchbase 7.6 CE    │          │  Python 3.12              │   │
│  │  port 8091 (UI/REST) │          │                           │   │
│  │  port 8093 (N1QL)    │─────────►│  cb_generate_data.py      │   │
│  │  port 11210 (SDK/KV) │          │  couchbase_to_mongo.py    │   │
│  │                      │◄─────────│    ⏱ start/end/duration   │   │
│  │  travel_bucket       │          │  cb_validation.py         │   │
│  │    type: airline     │          └──────────┬────────────────┘   │
│  │    type: route       │                     │ insert_many        │
│  │    type: booking     │                     ▼                    │
│  └──────────────────────┘          ┌───────────────────────────┐   │
│                                    │  MongoDB 8                │   │
│                                    │  port 27017               │   │
│                                    │                           │   │
│                                    │  demo database            │   │
│                                    │    airlines  collection   │   │
│                                    │    routes    collection   │   │
│                                    │    bookings  collection   │   │
│                                    └───────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Dataset

| Collection | Documents  |
|------------|----------:|
| airlines   |    10,000 |
| routes     |    20,000 |
| bookings   |    80,000 |
| **Total**  |**110,000**|

---

## Repository Structure

```
couchbase-mongodb-migration-poc/
├── README.md
├── cb_requirements.txt
├── cb_generate_data.py        # seed Couchbase with Faker data
├── couchbase_to_mongo.py      # migrate + capture timing
├── cb_validation.py           # count reconciliation
├── cb_cleanup.sh              # stop & remove containers
├── reports/
│   └── migration_summary.txt
└── screenshots/
```

---

## Environment

| Component                   | Version  |
|-----------------------------|----------|
| WSL Ubuntu                  | 22.04+   |
| Podman                      | Latest   |
| Couchbase Community Edition | 7.6.x    |
| MongoDB                     | 8.x      |
| Python                      | 3.12     |
| couchbase SDK               | Latest   |
| pymongo                     | Latest   |
| faker                       | Latest   |

> **Note:** Credentials (`Password123!`) are for local PoC use only. Never use in production.

---

## Prerequisites

- WSL 2 with Ubuntu 22.04 or later
- Podman installed (`sudo apt install podman`)
- Python 3.12 (`sudo apt install python3.12 python3.12-venv`)
- At least 2 GB free RAM

---

# Step 1 — Create Project Directory

```bash
mkdir -p ~/cb-to-mongo-poc
cd ~/cb-to-mongo-poc
```

---

# Step 2 — Create Podman Network

```bash
podman network create nosql-net
```

Verify:

```bash
podman network ls
```

Expected:

```
NETWORK ID    NAME        DRIVER
xxxxxxxxxxxx  nosql-net   bridge
```

---

# Step 3 — Start Couchbase Container

```bash
podman run -d \
  --name couchbase \
  --network nosql-net \
  -p 8091-8096:8091-8096 \
  -p 11210:11210 \
  docker.io/couchbase:community
```

Verify:

```bash
podman ps
```

---

# Step 4 — Configure Couchbase

Choose **one** of the two options below. Both produce identical results.

---

## Option A — Web UI (recommended for first-time setup)

Open the Couchbase Web UI:

```
http://localhost:8091
```

Select **Setup New Cluster** and enter:

```
Cluster Name : couchbase-poc
Username     : Administrator
Password     : Password123!
```

Accept the default service settings and finish the wizard.

**Create Bucket:**

```
Bucket Name : travel_bucket
RAM Quota   : 200 MB
```

**Create Primary Index** — open the Query Workbench:

```
http://localhost:8091/ui/index.html#/query/workbench
```

Execute:

```sql
CREATE PRIMARY INDEX idx_travel ON travel_bucket;
```

Wait for the index status to show **online** before proceeding to Step 5.

---

## Option B — CLI Only (no browser required)

Run all commands below **in order** from the WSL terminal.

> **Key rule:** Services, quotas, and storage mode must be set **before** credentials.
> Once `settings/web` is called, the node is locked and pre-auth steps are no longer accepted.
>
> **WSL + Podman note:** Port 8093 (N1QL) is unreliable via WSL port mapping.
> All N1QL commands are run **inside the container** using `podman exec` to avoid this issue.

**4.0 — Wait for Couchbase REST API to be ready**

```bash
echo "Waiting for Couchbase..."
until curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:8091/ui/index.html | grep -q "200"; do
  sleep 3
done
echo "Couchbase ready."
```

**4.1 — Enable services** (kv + n1ql + index — no auth yet)

```bash
curl -s -o /dev/null -w "services:    %{http_code}\n" \
  -X POST http://localhost:8091/node/controller/setupServices \
  -d services=kv,n1ql,index
```

Expected: `200`

**4.2 — Set memory quotas** (no auth yet)

```bash
curl -s -o /dev/null -w "quotas:      %{http_code}\n" \
  -X POST http://localhost:8091/pools/default \
  -d memoryQuota=256 \
  -d indexMemoryQuota=256
```

Expected: `200`

**4.3 — Set indexer storage mode** (no auth yet)

```bash
curl -s -o /dev/null -w "index mode:  %{http_code}\n" \
  -X POST http://localhost:8091/settings/indexes \
  -d storageMode=forestdb
```

Expected: `200`

> Required for Community Edition. Without it `CREATE PRIMARY INDEX` fails with
> `"Please Set Indexer Storage Mode Before Create Index"`.

**4.4 — Create Administrator credentials** (auth required for all steps after this)

```bash
curl -s -o /dev/null -w "credentials: %{http_code}\n" \
  -X POST http://localhost:8091/settings/web \
  -d username=Administrator \
  -d password=Password123! \
  -d port=8091
```

Expected: `200`

**4.5 — Create the bucket**

```bash
curl -s -o /dev/null -w "bucket:      %{http_code}\n" \
  -u Administrator:Password123! \
  -X POST http://localhost:8091/pools/default/buckets \
  -d name=travel_bucket \
  -d bucketType=couchbase \
  -d ramQuota=200 \
  -d replicaNumber=0
```

Expected: `202`

> `ramQuota=200` must be less than `memoryQuota=256` set in step 4.2.

**4.6 — Wait for bucket to be ready**

```bash
echo "Waiting for bucket..."
until curl -s -u Administrator:Password123! \
  http://localhost:8091/pools/default/buckets/travel_bucket \
  | grep -q '"name":"travel_bucket"'; do
  sleep 2
done
echo "Bucket ready."
```

**4.7 — Wait for N1QL to be ready inside the container**

> Port 8093 is polled **inside the container** via `podman exec` to avoid WSL port-mapping issues.

```bash
echo "Waiting for N1QL service..."
until podman exec couchbase curl -s -o /dev/null -w "%{http_code}" \
  http://127.0.0.1:8093/query/service | grep -qE "200|405"; do
  sleep 3
done
echo "N1QL ready."
```

> N1QL takes 60–90 seconds to initialise. This is normal — do not Ctrl-C.

**4.8 — Confirm all three services are active**

```bash
curl -s -u Administrator:Password123! \
  http://localhost:8091/pools/default \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print('  services:', n['services']) for n in d['nodes']]"
```

Expected:

```
  services: ['index', 'kv', 'n1ql']
```

**4.9 — Create the primary index** (run inside container)

```bash
podman exec couchbase curl -s \
  -u Administrator:Password123! \
  -X POST http://127.0.0.1:8093/query/service \
  -d 'statement=CREATE PRIMARY INDEX idx_travel ON travel_bucket'
```

Expected response contains:

```json
"status": "success"
```

**4.10 — Verify the index is online** (run inside container)

```bash
podman exec couchbase curl -s \
  -u Administrator:Password123! \
  -X POST http://127.0.0.1:8093/query/service \
  -d 'statement=SELECT state, name FROM system:indexes WHERE keyspace_id = "travel_bucket"'
```

Expected:

```json
"results": [ { "name": "idx_travel", "state": "online" } ]
```

Proceed to Step 5 once `state` shows `online`.

---

# Step 5 — Start MongoDB Container

```bash
podman run -d \
  --name mongodb \
  --network nosql-net \
  -p 27017:27017 \
  docker.io/library/mongo:8
```

Verify both containers are running:

```bash
podman ps
```

Expected:

```
CONTAINER ID  IMAGE                     NAMES
xxxxxxxxxxxx  couchbase:community       couchbase
xxxxxxxxxxxx  mongo:8                   mongodb
```

---

# Step 6 — Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r cb_requirements.txt
```

Or install individually:

```bash
pip install couchbase pymongo faker
```

---

# Step 7 — Generate Sample Data in Couchbase

```bash
python cb_generate_data.py
```

Expected output:

```
=======================================================
  Data Generation — Couchbase
=======================================================
  Generating 10,000 airlines...
  Inserting 10,000 airlines...
  Generating 20,000 routes...
  Inserting 20,000 routes...
  Generating 80,000 bookings (batch 10,000)...
    10,000 / 80,000
    20,000 / 80,000
    30,000 / 80,000
    40,000 / 80,000
    50,000 / 80,000
    60,000 / 80,000
    70,000 / 80,000
    80,000 / 80,000

Data Generation Complete
-------------------------------------------------------
  Airlines  :   10,000
  Routes    :   20,000
  Bookings  :   80,000
  Total     :  110,000
=======================================================
```

**Verify in Couchbase:**

```bash
curl -s \
  -u Administrator:Password123! \
  -X POST http://localhost:8093/query/service \
  -d 'statement=SELECT type, COUNT(*) cnt FROM travel_bucket GROUP BY type ORDER BY type'
```

Expected:

```json
"results": [
  { "cnt": 10000, "type": "airline" },
  { "cnt": 80000, "type": "booking" },
  { "cnt": 20000, "type": "route"   }
]
```

---

# Step 8 — Run Migration

```bash
python couchbase_to_mongo.py
```

The script records **start time**, **end time**, and **duration** per collection and overall.

Expected output:

```
=================================================================
  Couchbase → MongoDB Migration
=================================================================

  Overall Start : 2026-06-02 02:28:04 UTC

  Connecting to Couchbase...
  Couchbase connection OK

  Connecting to MongoDB...
  MongoDB connection OK

  Starting migration...
-----------------------------------------------------------------

  [Airlines] Starting   : 2026-06-02 02:28:04 UTC
  [Airlines] Completed  : 2026-06-02 02:28:06 UTC
  [Airlines] Duration   : 1.94s
  [Airlines] Migrated   : 10,000 / 10,000  (errors: 0)

  [Routes] Starting     : 2026-06-02 02:28:06 UTC
  [Routes] Completed    : 2026-06-02 02:28:08 UTC
  [Routes] Duration     : 2.21s
  [Routes] Migrated     : 20,000 / 20,000  (errors: 0)

  [Bookings] Starting   : 2026-06-02 02:28:08 UTC
  [Bookings] Completed  : 2026-06-02 02:28:12 UTC
  [Bookings] Duration   : 3.84s
  [Bookings] Migrated   : 80,000 / 80,000  (errors: 0)

  Creating MongoDB indexes...
  Indexes created.

=================================================================
  Migration Summary
=================================================================
  Collection     Docs    Start                    End                      Dur
  ------------ ------    ------------------------ ------------------------ ------
  Airlines     10,000    2026-06-02 02:28:04 UTC  2026-06-02 02:28:06 UTC  1.94s
  Routes       20,000    2026-06-02 02:28:06 UTC  2026-06-02 02:28:08 UTC  2.21s
  Bookings     80,000    2026-06-02 02:28:08 UTC  2026-06-02 02:28:12 UTC  3.84s
  ------------ ------
  TOTAL       110,000

  Overall Start    : 2026-06-02 02:28:04 UTC
  Overall End      : 2026-06-02 02:28:12 UTC
  Overall Duration : 8.45s
  Total Errors     : 0

  Status           : SUCCESS ✓
=================================================================
```

Save the report:

```bash
mkdir -p reports
python couchbase_to_mongo.py | tee reports/migration_summary.txt
```

---

# Step 9 — Validate Migration

```bash
python cb_validation.py
```

Expected output:

```
==================================================
  Couchbase → MongoDB Validation
==================================================

  Connecting to Couchbase...
  Couchbase counts retrieved.

  Connecting to MongoDB...
  MongoDB counts retrieved.

  Type            Couchbase    MongoDB   Match
  ------------ ------------ ----------  ------
  Airlines           10,000     10,000       ✓
  Routes             20,000     20,000       ✓
  Bookings           80,000     80,000       ✓
  ------------ ------------ ----------
  TOTAL             110,000    110,000

  VALIDATION PASSED ✓
==================================================
```

---

# Step 10 — Verify Data in MongoDB

Connect to mongosh:

```bash
podman exec -it mongodb mongosh
```

> **Important:** Always run `use demo` first. The default database is `test` and
> collections there will be empty.

```javascript
use demo

db.airlines.countDocuments()
// Expected: 10000

db.routes.countDocuments()
// Expected: 20000

db.bookings.countDocuments()
// Expected: 80000
```

View sample documents:

```javascript
db.airlines.findOne()

db.routes.findOne()

db.bookings.findOne()
```

Verify indexes:

```javascript
db.airlines.getIndexes()

db.routes.getIndexes()

db.bookings.getIndexes()
```

Exit:

```javascript
exit
```

**Verify from WSL CLI without entering mongosh:**

```bash
podman exec mongodb mongosh --quiet --eval "
  use('demo');
  print('airlines : ' + db.airlines.countDocuments());
  print('routes   : ' + db.routes.countDocuments());
  print('bookings : ' + db.bookings.countDocuments());
"
```

Expected:

```
airlines : 10000
routes   : 20000
bookings : 80000
```

---

# Migration Validation Summary

| Document Type | Couchbase | MongoDB   | Status |
|---------------|----------:|----------:|--------|
| airline       |    10,000 |    10,000 | ✓      |
| route         |    20,000 |    20,000 | ✓      |
| booking       |    80,000 |    80,000 | ✓      |
| **Total**     |**110,000**|**110,000**| **PASSED** |

---

# Results

```
Source Database  : Couchbase 7.6 Community Edition
Target Database  : MongoDB 8
Collections      : 3
Airlines         : 10,000
Routes           : 20,000
Bookings         : 80,000
Total Documents  : 110,000
Migration Status : SUCCESS
Validation       : PASSED
Timing           : Per-collection start / end / duration captured
Overall Duration : 8.45s
Platform         : WSL + Podman
Automation       : Python 3.12
```

---

# Cleanup

```bash
chmod +x cb_cleanup.sh
./cb_cleanup.sh
```

Expected output:

```
Stopping containers...
Removing containers...
Removing Podman network...
Cleanup complete.
```

Remove project directory:

```bash
cd ~
rm -rf ~/cb-to-mongo-poc
```

---

## Lessons Learned — Couchbase CLI Setup

When configuring Couchbase via REST API, the order of operations is critical:

| Order | Step | Endpoint | Auth needed |
|-------|------|----------|-------------|
| 1 | Enable services (kv, n1ql, index) | `/node/controller/setupServices` | No |
| 2 | Set memory quotas | `/pools/default` | No |
| 3 | Set indexer storage mode | `/settings/indexes` | No |
| 4 | Create credentials | `/settings/web` | No |
| 5 | Create bucket | `/pools/default/buckets` | Yes |
| 6 | Create index | port 8093 N1QL | Yes |

Key points:
- `setupServices` must be called **before** `settings/web` — once credentials are set, the node considers itself initialised and service changes are locked
- `storageMode=forestdb` is mandatory for Community Edition before any index can be created
- N1QL port 8093 takes 60–90 seconds to open after services are enabled on WSL + Podman — this is normal
- Bucket `ramQuota` must always be less than `memoryQuota`
- In mongosh, always run `use demo` before querying — default database is `test`

---

## Learning Outcomes

- Couchbase cluster setup via REST API and Web UI
- Couchbase N1QL / SQL++ querying via Python SDK and curl
- MongoDB collection design and index creation via pymongo
- Podman container networking on WSL
- Python-based NoSQL migration with batch insert
- Migration effort tracking — start time, end time, duration per collection
- Cross-database count reconciliation and validation
- End-to-end reverse NoSQL migration workflow (Couchbase → MongoDB)
