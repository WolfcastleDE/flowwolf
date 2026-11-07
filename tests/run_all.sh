#!/bin/sh
# Runs every end-to-end suite against fresh containers. Needs Docker, curl and Python 3.
# Usage: tests/run_all.sh   (builds flowwolf:latest and flowwolf:python first)
cd "$(dirname "$0")"
set -u
PY=${PY:-python}
docker build -q -t flowwolf:latest .. >/dev/null && docker build -q --build-arg FEATURES=python -t flowwolf:python .. >/dev/null || exit 1
clean() { docker rm -f fw-e2e fw-e2e-b fw-main fw-w1 fw-w2 fw-pg fw-pgs fw-redis fw-maria fw-gitea fw-mail fw-mq fw-db fw-qdrant fw-pgv fw-s3 fw-mongo fw-ftp fw-sftp >/dev/null 2>&1; docker volume rm fw-e2e-data fw-q >/dev/null 2>&1; }
clean
fresh() { docker rm -f fw-e2e >/dev/null 2>&1; docker run -d --rm --name fw-e2e -p 5679:5678 "$@" >/dev/null; sleep 2; }

# services used by the suites
docker run -d --rm --name fw-pg -e "POSTGRES_PASSWORD=pw with 'quote" -p 5433:5432 postgres:16 >/dev/null
MSYS_NO_PATHCONV=1 docker run -d --rm --name fw-pgs -e "POSTGRES_PASSWORD=pw with 'quote" -p 5434:5432 --entrypoint bash postgres:16 -c \
  "openssl req -new -x509 -days 2 -nodes -subj /CN=localhost -keyout /tmp/k.pem -out /tmp/c.pem 2>/dev/null && chown postgres /tmp/k.pem /tmp/c.pem && chmod 600 /tmp/k.pem && exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/c.pem -c ssl_key_file=/tmp/k.pem" >/dev/null
MSYS_NO_PATHCONV=1 docker run -d --rm --name fw-mq -p 1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf >/dev/null
docker run -d --rm --name fw-pgv -e POSTGRES_PASSWORD=pw -p 5436:5432 pgvector/pgvector:pg16 >/dev/null
docker run -d --rm --name fw-qdrant -p 6333:6333 qdrant/qdrant:v1.12.4 >/dev/null
docker run -d --rm --name fw-redis -p 6380:6379 redis:7-alpine >/dev/null
MSYS_NO_PATHCONV=1 docker run -d --rm --name fw-s3 -p 8333:8333 -v "$(pwd -W 2>/dev/null || pwd)/s3.json:/etc/s3.json:ro" chrislusf/seaweedfs server -s3 -s3.config=/etc/s3.json >/dev/null
docker run -d --rm --name fw-mongo -p 27018:27017 -e MONGO_INITDB_ROOT_USERNAME=root -e "MONGO_INITDB_ROOT_PASSWORD=pw ä&?" mongo:7 >/dev/null
docker run -d --rm --name fw-ftp -p 2121:21 -p 21000-21010:21000-21010 -e "USERS=fw|pw|/ftp/fw" -e MIN_PORT=21000 -e MAX_PORT=21010 delfer/alpine-ftp-server >/dev/null
docker run -d --rm --name fw-sftp -p 2222:22 atmoz/sftp fw:pw:::upload >/dev/null
docker run -d --rm --name fw-maria -e "MARIADB_ROOT_PASSWORD=pw with 'quote" -e MARIADB_DATABASE=shop -p 33077:3306 mariadb:11 >/dev/null
docker run -d --rm --name fw-mail -p 3025:3025 -p 3993:3993 -e "GREENMAIL_OPTS=-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.auth.disabled" greenmail/standalone:2.1.2 >/dev/null
docker run -d --rm --name fw-gitea -p 3300:3000 -p 3322:22 -e GITEA__security__INSTALL_LOCK=true -e GITEA__server__ROOT_URL=http://localhost:3300/ -e GITEA__database__DB_TYPE=sqlite3 gitea/gitea:1.22 >/dev/null
for i in $(seq 90); do [ "$(docker logs fw-maria 2>&1 | grep -c 'ready for connections')" -ge 2 ] && [ "$(docker logs fw-pgv 2>&1 | grep -c 'ready to accept')" -ge 2 ] && curl -sf localhost:3300/api/v1/version >/dev/null && break; sleep 1; done
docker exec -u git fw-gitea gitea admin user create --username flow --password 'flowpass123' --email flow@example.com --admin --must-change-password=false >/dev/null 2>&1
T=$(curl -s -u flow:flowpass123 -X POST localhost:3300/api/v1/users/flow/tokens -H 'content-type: application/json' -d '{"name":"t","scopes":["write:repository","write:user"]}' | $PY -c "import sys,json;print(json.load(sys.stdin)['sha1'])")
curl -s -o /dev/null -H "Authorization: token $T" -H 'content-type: application/json' -X POST localhost:3300/api/v1/user/repos -d '{"name":"automations","auto_init":true,"default_branch":"main"}'

fresh flowwolf:latest; $PY -X utf8 e2e.py http://localhost:5679 | tail -1
fresh flowwolf:latest; $PY -X utf8 e2e_users.py http://localhost:5679 | tail -1
fresh flowwolf:latest; $PY -X utf8 e2e_catalogue.py http://localhost:5679 | tail -1
fresh -e FLOWWOLF_API_BASE_OVERRIDES="$($PY e2e_operations.py --overrides)" flowwolf:latest; $PY -X utf8 e2e_operations.py http://localhost:5679 | tail -1
fresh flowwolf:latest; $PY -X utf8 e2e_integrations.py http://localhost:5679 host.docker.internal | tail -1
fresh flowwolf:latest; $PY -X utf8 e2e_ai_share.py http://localhost:5679 | tail -1
fresh flowwolf:latest; $PY -X utf8 e2e_mysql_utils.py http://localhost:5679 host.docker.internal | tail -1
fresh flowwolf:latest; $PY -X utf8 e2e_mail_core.py http://localhost:5679 host.docker.internal | tail -1
fresh flowwolf:latest; $PY -X utf8 e2e_triggers.py http://localhost:5679 host.docker.internal | tail -1
fresh -e FLOWWOLF_API_BASE_OVERRIDES="$($PY e2e_pollers.py --overrides)" flowwolf:latest; $PY -X utf8 e2e_pollers.py http://localhost:5679 | tail -1
fresh -e FLOWWOLF_API_BASE_OVERRIDES="$($PY e2e_hooktriggers.py --overrides)" flowwolf:latest; $PY -X utf8 e2e_hooktriggers.py http://localhost:5679 | tail -1
fresh -e FLOWWOLF_API_BASE_OVERRIDES="$($PY e2e_contracts.py --overrides)" flowwolf:latest; $PY -X utf8 e2e_contracts.py http://localhost:5679 | tail -1
fresh -e FLOWWOLF_API_BASE_OVERRIDES="$($PY e2e_ai_cluster.py --overrides)" flowwolf:latest; $PY -X utf8 e2e_ai_cluster.py http://localhost:5679 host.docker.internal | tail -1
fresh flowwolf:latest; $PY -X utf8 e2e_storage.py http://localhost:5679 host.docker.internal | tail -1
fresh -e N8N_CONCURRENCY_PRODUCTION_LIMIT=1 flowwolf:python; $PY -X utf8 e2e_rag_python.py http://localhost:5679 | tail -1
fresh flowwolf:latest; docker run -d --rm --name fw-e2e-b -p 5680:5678 flowwolf:latest >/dev/null; sleep 2
$PY -X utf8 e2e_git_roles.py http://localhost:5679 http://localhost:5680 http://host.docker.internal:3300/api/v1 http://localhost:3300/api/v1 "$T" | tail -1
docker rm -f fw-e2e fw-e2e-b >/dev/null
fresh flowwolf:latest; docker run -d --rm --name fw-e2e-b -p 5680:5678 flowwolf:latest >/dev/null; sleep 2
$PY -X utf8 e2e_git_ssh.py http://localhost:5679 http://localhost:5680 ssh://git@host.docker.internal:3322/flow/automations.git http://localhost:3300/api/v1 "$T" | tail -1
docker rm -f fw-e2e fw-e2e-b >/dev/null
docker run -d --name fw-e2e -p 5679:5678 -v fw-e2e-data:/data flowwolf:latest >/dev/null; sleep 2
$PY -X utf8 e2e_persist.py http://localhost:5679 fw-e2e | tail -1
docker rm -f fw-e2e >/dev/null
docker run -d --name fw-main -p 5679:5678 -v fw-q:/data -e EXECUTIONS_MODE=queue flowwolf:latest >/dev/null; sleep 2
for w in w1 w2; do docker run -d --name fw-$w --hostname $w -v fw-q:/data -e EXECUTIONS_MODE=queue -e FLOWWOLF_WORKER_CONCURRENCY=3 --no-healthcheck flowwolf:latest worker >/dev/null; done; sleep 2
$PY -X utf8 e2e_queue_workers.py http://localhost:5679 | tail -1
docker rm -f fw-main fw-w1 fw-w2 >/dev/null
sh pg_cluster.sh
clean
