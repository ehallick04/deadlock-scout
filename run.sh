#!/bin/bash
# Launch the app through the project's own virtualenv.
#
# Bare `streamlit run app.py` picks up whatever `streamlit` is first on PATH,
# which on this machine is Anaconda's base environment -- and Anaconda ships a
# pyarrow built against a different numpy than the one sitting beside it.
# Streamlit renders every DataFrame through pyarrow, so the app starts fine and
# then dies on the first table with an ImportError from inside pyarrow.lib,
# which reads as a bug in this code rather than in the interpreter it was
# launched from. It has cost three separate debugging sessions.
#
#   ./run.sh

set -eu
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  cat >&2 <<'MISSING'
No .venv here. Create one:

  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt

Do not use `conda activate` or the base environment -- see the comment at the
top of this script for why.
MISSING
  exit 1
fi

exec .venv/bin/python -m streamlit run app.py "$@"
