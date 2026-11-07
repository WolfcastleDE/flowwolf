#!/bin/sh
# Multi-host setup on one PC: PostgreSQL + main + two workers, no shared volume (each container
# only sees the database), then the queue suite and the core suite against PostgreSQL.
# Usage: tests/pg_cluster.sh   (expects flowwolf:latest to be built)
cd "$(dirname "$0")"
PY=${PY:-python}
docker rm -f fw-db fw-main fw-w1 fw-w2 fw-e2e >/dev/null 2>&1
docker run -d --rm --name fw-db -e POSTGRES_PASSWORD=dbpw -e POSTGRES_DB=flow -p 5435:5432 postgres:16 >/dev/null
for i in $(seq 60); do [ "$(docker logs fw-db 2>&1 | grep -c 'ready to accept')" -ge 2 ] && break; sleep 1; done
K=$($PY -c "import secrets;print(secrets.token_hex(24))")
PG="-e DB_TYPE=postgresdb -e DB_POSTGRESDB_HOST=host.docker.internal -e DB_POSTGRESDB_PORT=5435 -e DB_POSTGRESDB_DATABASE=flow
    -e DB_POSTGRESDB_USER=postgres -e DB_POSTGRESDB_PASSWORD=dbpw -e FLOWWOLF_ENCRYPTION_KEY=$K"
docker run -d --rm --name fw-main -p 5679:5678 $PG -e EXECUTIONS_MODE=queue flowwolf:latest >/dev/null; sleep 3
for w in w1 w2; do docker run -d --rm --name fw-$w --hostname $w $PG -e EXECUTIONS_MODE=queue -e FLOWWOLF_WORKER_CONCURRENCY=3 --no-healthcheck flowwolf:latest worker >/dev/null; done; sleep 3
$PY -X utf8 e2e_queue_workers.py http://localhost:5679 | tail -1 | sed 's/$/ (PostgreSQL, separate hosts)/'
docker rm -f fw-main fw-w1 fw-w2 >/dev/null
# Core suite on a fresh schema of the same server.
docker run -d --rm --name fw-e2e -p 5679:5678 $PG -e DB_POSTGRESDB_SCHEMA=core flowwolf:latest >/dev/null; sleep 3
$PY -X utf8 e2e.py http://localhost:5679 | tail -1 | sed 's/$/ (PostgreSQL)/'
docker rm -f fw-e2e fw-db >/dev/null
