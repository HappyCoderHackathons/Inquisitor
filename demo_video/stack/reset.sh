#!/usr/bin/env bash
# Wipe the throwaway demo databases and model files so takes can be re-recorded from scratch.
set -euo pipefail
RUN="$(cd "$(dirname "$0")" && pwd)/run"
mongosh --quiet --port 27027 --eval 'db.getSiblingDB("inquisitor_demo").dropDatabase(); db.getSiblingDB("inquisitor_demo_ml").dropDatabase()'
rm -rf "$RUN/ml"/* "$RUN/artifacts"
rm -f "$(dirname "$0")/../takes/"{state.json,agent.json,pairing-code.txt}
