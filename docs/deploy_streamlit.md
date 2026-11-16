# Putting the viewers online with Streamlit Community Cloud

Streamlit Community Cloud is a free hosting service for Streamlit apps. It reads the code and the
sample databases from the GitHub repository and gives each viewer a web address.

## Before you start

- The repository must be on GitHub (`bash publish_to_github.sh`).
- **Anyone with the link sees the content**: the 46 archive page images and the Harbin data. Check
  the archive's terms first.
- One app deployed from a *private* repository is allowed per account. To put both viewers online,
  the repository has to be public; while it is private, deploy only one.
- Apps are hosted in the United States and go to sleep after about twelve hours without a visit
  (the next visitor wakes them with one click and a short wait).

## Steps (once per viewer)

1. Go to <https://share.streamlit.io> and sign in with your GitHub account. Allow access to the
   repository when asked.
2. Click **Create app**, then **Deploy a public app from GitHub**.
3. Fill in:

   | Field | Text viewer | Actor viewer |
   |---|---|---|
   | Repository | `<your account>/rotary-in-china` | the same |
   | Branch | `main` | `main` |
   | Main file path | `app/app.py` | `app/actors.py` |
   | App URL (optional) | for example `rotary-china-texts` | for example `rotary-china-actors` |

4. Open **Advanced settings** and choose Python 3.12. Leave the secrets box empty (see below).
5. Click **Deploy**. The first start takes a few minutes while the packages install.

The address of each app is then `https://<the name you chose>.streamlit.app`.

## Updating

Every `git push` to the repository updates the apps within a minute or so. To publish new results,
rebuild locally (`python -m rctb build`, `python -m rctb embed`, `python -m rcad all`), commit the
changed files under `data/out/` and `actors/out/`, and push.

## The "Ask" tab

Reading, searching and filtering work without any model. The Ask tab of the text viewer writes
answers with a language model and is off by default (`llm.backend: none` in `config.yaml`): online,
it shows the passages it found and says that no model is configured.

To switch it on with the Anthropic API: set `llm.backend: anthropic` in `config.yaml`, push, and in
the app's **Settings → Secrets** add one line:

    ANTHROPIC_API_KEY = "your key"

Each question asked by a visitor is then billed to that key, so only do this for an app whose link
you share with people you know. Never write the key in a file of the repository.

## If something goes wrong

- **"ModuleNotFoundError"**: a package is missing from `requirements.txt` at the root of the repository.
- **"No database found"**: `data/out/rctb.sqlite` or `actors/out/rcad.sqlite` was not committed.
- **Page images missing**: `data/out/images/` was not committed.
- The app's log (bottom right, **Manage app**) shows the full message; send it along when asking for help.
