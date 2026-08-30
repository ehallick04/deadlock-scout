# Deploying the Deadlock scouting app (free)

## 1. Run it locally first

```powershell
uv pip install streamlit pandas
streamlit run app.py
```

It opens at http://localhost:8501. Confirm it works before deploying.

## 2. Put the project on GitHub

Free Streamlit hosting reads from a GitHub repo. The repo must be **public**
on the free tier.

```powershell
git init
git add api.py deadlock.py app.py main.py requirements.txt .gitignore
git commit -m "Deadlock scouting report"
```

Then create an empty repo on github.com and follow the two push commands it
shows you.

Do NOT commit `heroes.json`, `*.csv`, `.venv/`, or any list of player ids you
would rather keep private. The included `.gitignore` handles those.

## 3. Deploy

1. Go to https://share.streamlit.io and sign in with GitHub
2. "New app" -> pick your repo -> main file is `app.py`
3. Deploy

You get a public URL like `https://yourname-deadlock.streamlit.app`.
Send that link to your teammates. Nothing to install on their end.

## Things to know

- **Free tier sleeps.** After a period with no visitors the app suspends;
  the next visitor wakes it, which takes ~30 seconds. Harmless, just slow.
- **Public repo = public code.** Fine here, since there are no secrets.
  If you ever add an API key, put it in Streamlit's Secrets manager
  (app settings -> Secrets), never in the code.
- **Public URL = anyone with the link can use it.** There is no login. It
  only reads public match data, so this is low-stakes, but be aware the
  link is the only thing gating access.
- **Rate limits are shared.** Every visitor's queries come from the same
  deployed app, so a busy day counts against one pool. The 15-minute cache
  in `app.py` keeps this manageable.

## Alternative: no deploy at all

`streamlit run app.py` on your own machine, then share your local network
address (shown in the terminal as "Network URL") with teammates on the same
network. Nothing leaves your PC.
