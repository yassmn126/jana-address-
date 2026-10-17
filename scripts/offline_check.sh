#!/usr/bin/env bash
# Offline jury simulation (make offline-check): the API image runs with NO network at all (--network none) and is
# driven through docker exec on its own loopback. Checks start time, /version, runtime deps, exact jury schema,
# the jury metric on the hand-labeled sets, parity with the local code, 1000-record timing and memory.
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)}"
IMG="${IMG:-jana-address-api:latest}"

NAME=jana-offline-check
docker rm -f $NAME >/dev/null 2>&1
echo "== start (network none)"
t0=$(python3 -c 'import time;print(time.time())')
docker run -d --name $NAME --network none $IMG >/dev/null
for i in $(seq 1 360); do
  if docker exec $NAME python -c "import urllib.request,sys; sys.exit(0 if b'ok' in urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read() else 1)" >/dev/null 2>&1; then break; fi
  sleep 0.5
done
t1=$(python3 -c 'import time;print(time.time())')
python3 -c "print(f'healthy after {${t1}-${t0}:.1f}s (brief: container start ≤ 180 s)')"
docker exec $NAME sh -c 'cat /proc/net/route | wc -l; ls /sys/class/net' | tr '\n' ' '; echo " ← interfaces (only lo expected)"

echo "== /version"
docker exec $NAME python -c "
import json,urllib.request
v=json.load(urllib.request.urlopen('http://127.0.0.1:8000/version'))
c=v['components']; print({k:c[k] for k in ('rules','model','mode','gazetteer')}); print('gazetteer', c['gazetteer_stats'])"

echo "== runtime deps (torch / sklearn / lightgbm must be absent)"
docker exec $NAME python -c "
import importlib.util as u, sys
print('python', sys.version.split()[0]); print({m: bool(u.find_spec(m)) for m in ('torch','sklearn','lightgbm','onnxruntime','numpy')})"

post() {  # $1 = input json path on host, $2 = output path on host, $3 = query string
  docker cp "$1" $NAME:/tmp/in.json
  docker exec $NAME python -c "
import json,time,urllib.request
d=open('/tmp/in.json','rb').read()
r=urllib.request.Request('http://127.0.0.1:8000/api/v1/parse/batch$3',data=d,headers={'Content-Type':'application/json'})
t=time.perf_counter(); out=urllib.request.urlopen(r,timeout=600).read(); dt=time.perf_counter()-t
open('/tmp/out.json','wb').write(out); n=len(json.loads(out)); print(f'{n} records in {dt:.2f}s ({1000*dt/max(1,n):.2f} ms/record)')"
  docker cp $NAME:/tmp/out.json "$2"
}
mkdir -p runs/docker
for s in dev_handmade test_handmade conventions_examples; do
  echo "== $s"
  python3 -c "
import json; d=json.load(open('data/$s.json')); d=d['examples'] if isinstance(d,dict) else d
json.dump([{'id':x.get('id',str(i)),'raw_address':x['raw_address']} for i,x in enumerate(d)], open('runs/docker/$s.in.json','w'), ensure_ascii=False)"
  post runs/docker/$s.in.json runs/docker/$s.out.json ""
  python3 -c "
import json
JURY={'id','raw_address','province','city','district','living_area','street_type','street_name','house','apartment','entrance','floor','landmark','confidence'}
o=json.load(open('runs/docker/$s.out.json')); bad=[x['id'] for x in o if set(x)!=JURY]
print('exact jury schema:', 'OK' if not bad else f'EXTRA/MISSING KEYS in {bad[:5]}')"
done
echo "== jury metric on container output"
$PY -m eval.metric --pred runs/docker/dev_handmade.out.json --gold data/dev_handmade.json | head -3
$PY -m eval.metric --pred runs/docker/test_handmade.out.json --gold data/test_handmade.json | head -3
echo "== same answers as the local venv?"
$PY -W ignore - <<'EOF'
import json, sys
sys.path.insert(0, 'api')
from jana_address.service import parse_one
o = json.load(open('runs/docker/test_handmade.out.json')) + json.load(open('runs/docker/dev_handmade.out.json'))
diff = [x['id'] for x in o if {k: v for k, v in parse_one(x['id'], x['raw_address']).items() if k != 'confidence'} != {k: v for k, v in x.items() if k != 'confidence'}]
print('records that differ from local:', len(diff), diff[:5])
EOF
echo "== 1000 records"
python3 -c "
import json; d=json.load(open('data/dev_handmade.json'))+json.load(open('data/test_handmade.json'))
json.dump([{'id':f't{i}','raw_address':d[i % len(d)]['raw_address']} for i in range(1000)], open('runs/docker/speed.in.json','w'), ensure_ascii=False)"
post runs/docker/speed.in.json runs/docker/speed.out.json ""
echo "== explain mode on one record"
echo '[{"id":"e1","raw_address":"Алматы, мкр Аксай-4, 98, кв 21"}]' > runs/docker/one.json
post runs/docker/one.json runs/docker/one.out.json "?explain=1"
python3 -c "
import json; x=json.load(open('runs/docker/one.out.json'))[0]
print({k:x[k] for k in ('street_name','house','apartment','district','confidence')}, x['provenance']['district'], x['tokens'][3].get('model_label'))"
echo "== memory"
docker stats --no-stream --format '{{.Name}} mem={{.MemUsage}} cpu={{.CPUPerc}}' $NAME
echo "== logs (errors only)"
docker logs $NAME 2>&1 | grep -iE "error|traceback|exception" | head -5 || true
docker rm -f $NAME >/dev/null
echo "== OFFLINE TEST DONE"
