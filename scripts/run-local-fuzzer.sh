#!/bin/bash

ROOT_DIR="$(realpath $(dirname ${BASH_SOURCE[0]:-$0})/..)"
cd "${ROOT_DIR}"

export PYTHONPATH="${ROOT_DIR}/orchestrator/src"
python3.8 orchestrator/shim.py fuzz "$@"