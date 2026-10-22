#!/bin/sh
# Скачивает свежие выгрузки OpenStreetMap по всем федеральным округам России и собирает базу.
set -e
cd "$(dirname "$0")/.."
mkdir -p data/raw
for d in central-fed-district northwestern-fed-district volga-fed-district ural-fed-district \
         siberian-fed-district south-fed-district north-caucasus-fed-district \
         far-eastern-fed-district crimean-fed-district kaliningrad; do
  echo "Скачиваю $d..."
  curl -fsSL --retry 3 -C - -o "data/raw/$d.osm.pbf" "https://download.geofabrik.de/russia/$d-latest.osm.pbf"
done
ls -la data/raw
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
"$PY" scripts/build_osm_db.py data/raw/*.osm.pbf
