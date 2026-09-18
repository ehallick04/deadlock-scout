"""
gate.py — a password gate for the deployed app.

Streamlit Community Cloud gives a public URL to a public repo, so anyone with
the link can open the app. This puts a password in front of it.

**What this does and does not protect.** It protects the *app*, not the data.
`rosters.json` is committed to a public repo and readable on GitHub without
touching the app at all, and the Deadlock API is public. So on its own the gate
is modest: it stops casual use of your tooling.

It becomes important the moment the deployed app is pointed at the scouting
server. The app's secrets then hold server credentials, which means anyone who
can load the page is effectively logged in as you — free rein over the store and
every account grant. The gate is what stops the URL being a session.

Passwords are stored as PBKDF2 hashes, never plaintext, so a secrets file that
leaks into a screenshot or a log does not hand over the password itself. Generate
one with:

    python gate.py

Then paste the line it prints into `.streamlit/secrets.toml` locally, or into
the app's Secrets box on Streamlit Cloud:

    APP_PASSWORD = "pbkdf2$200000$....$...."

Or give each team its own, so one can be revoked without changing everyone's:

    [APP_USERS]
    ethan = "pbkdf2$200000$....$...."
    melee-creeps = "pbkdf2$200000$....$...."

With neither key set there is no gate at all, which keeps local development
frictionless — the same opt-in shape as the server routing.
"""

import base64
import getpass
import hashlib
import hmac
import os
import secrets
import sys
import time

ITERATIONS = 200_000
ALGORITHM = "pbkdf2"


# --------------------------------------------------------------- hashing


def make_hash(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 iterations)
    return "$".join([ALGORITHM, str(iterations),
                     base64.b64encode(salt).decode(),
                     base64.b64encode(digest).decode()])


def check_hash(password: str, stored: str) -> bool:
    """
    Constant-time comparison against a stored hash.

    A plain string stored instead of a hash is accepted too, because telling
    someone their password is wrong when the real problem is the format of a
    secret is a miserable thing to debug. It is compared in constant time all
    the same.
    """
    stored = (stored or "").strip()
    if not stored:
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != ALGORITHM:
        return hmac.compare_digest(password, stored)
    try:
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2])
        expected = base64.b64decode(parts[3])
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 iterations)
    return hmac.compare_digest(digest, expected)


# --------------------------------------------------------------- config


def _secret(name, default=None):
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default


def accounts() -> dict:
    """
    {name: stored_hash}. Empty when no gate is configured.

    A single APP_PASSWORD is recorded under a blank name, which is what lets the
    sign-in form ask only for a password in that case.
    """
    users = _secret("APP_USERS") or {}
    try:
        out = {str(k): str(v) for k, v in dict(users).items()}
    except (TypeError, ValueError):
        out = {}
    shared = _secret("APP_PASSWORD")
    if shared:
        out.setdefault("", str(shared))
    return out


def configured() -> bool:
    return bool(accounts())


# --------------------------------------------------------------- the gate


def require_access() -> None:
    """
    Stop the script until the visitor signs in. No-op when no gate is set.

    Call it before anything renders. The signed-in flag lives in Streamlit's
    session state, which is held server-side per connection rather than in a
    cookie the browser could forge — but it is also per connection, so a
    refresh means signing in again. That is the honest trade for not inventing
    a token scheme, and it is the behaviour you want on a shared machine.
    """
    import streamlit as st

    known = accounts()
    if not known:
        return
    if st.session_state.get("gate_user") is not None:
        return

    multi = [name for name in known if name]
    st.title("Deadlock Scout")
    st.caption("This app is private. Sign in to continue.")

    with st.form("gate"):
        who = ""
        if multi:
            # Typed, not a dropdown. A list of names would tell anyone who
            # opens the URL which teams use this, which is not theirs to know.
            who = st.text_input("Name you were given")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        name = who.strip().lower() if multi else ""
        lookup = {k.lower(): v for k, v in known.items()}
        stored = lookup.get(name, "")
        # a failed attempt costs a moment: not real rate limiting, since a new
        # browser session resets it, but enough to make guessing tedious
        time.sleep(0.5)
        # One message for a wrong name and a wrong password alike, so the form
        # cannot be used to find out which names exist.
        if stored and check_hash(password, stored):
            st.session_state["gate_user"] = name or "signed in"
            st.rerun()
        st.error("That did not work.")

    st.stop()


def current_user():
    import streamlit as st
    return st.session_state.get("gate_user")


def sign_out_button() -> None:
    """A way out, for shared machines."""
    import streamlit as st

    if not configured() or current_user() is None:
        return
    who = current_user()
    label = f"Sign out ({who})" if who != "signed in" else "Sign out"
    if st.sidebar.button(label):
        del st.session_state["gate_user"]
        st.rerun()


# --------------------------------------------------------------- CLI


def main() -> int:
    if len(sys.argv) > 1:
        # Avoid this: a password typed as an argument is kept in shell history.
        password = sys.argv[1]
        print("warning: that password is now in your shell history",
              file=sys.stderr)
    else:
        password = getpass.getpass("Password to hash: ")
        if password != getpass.getpass("Again: "):
            print("those did not match", file=sys.stderr)
            return 1
    if len(password) < 8:
        print("use at least 8 characters", file=sys.stderr)
        return 1
    print()
    print("Paste into .streamlit/secrets.toml, or the Secrets box on")
    print("Streamlit Community Cloud:")
    print()
    print(f'APP_PASSWORD = "{make_hash(password)}"')
    print()
    print("For one password per team instead:")
    print()
    print("[APP_USERS]")
    print(f'melee-creeps = "{make_hash(password)}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
