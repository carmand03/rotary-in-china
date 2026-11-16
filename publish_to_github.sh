#!/usr/bin/env bash
# Publish this folder as a repository on YOUR GitHub account, in one command:
#
#     bash publish_to_github.sh                 # creates a PRIVATE repository named rotary-in-china
#     bash publish_to_github.sh my-name public  # another name, and public from the start
#
# Needs Git and the GitHub command-line tool, signed in once:
#     macOS:  brew install gh      then:  gh auth login
# Nothing is sent anywhere until the last step; the script stops at the first error.
set -euo pipefail

NAME="${1:-rotary-in-china}"
VISIBILITY="${2:-private}"            # private (default) or public
DESCRIPTION="Transparent, repeatable workflow turning the archives of the Rotary clubs in China and Manchuria (1919-1952) into linked text and actor databases. Sample edition: Harbin."
TOPICS="digital-humanities,history,archives,ocr,named-entity-recognition,prosopography,rag,sqlite,china,rotary"

cd "$(dirname "$0")"
[ -f config.yaml ] && [ -d rctb ] && [ -d rcad ] || { echo "Run this from the project folder (the one with config.yaml)."; exit 1; }
command -v git >/dev/null || { echo "Git is not installed."; exit 1; }
command -v gh  >/dev/null || { echo "The GitHub command-line tool is not installed:  brew install gh"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Not signed in to GitHub. Run:  gh auth login"; exit 1; }
case "$VISIBILITY" in private|public) ;; *) echo "Second argument must be private or public."; exit 1;; esac

# 1. Safety checks: no secrets, no file over 50 MB, no scans or web harvest
if grep -rIlE "sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}" --exclude-dir=.git --exclude=publish_to_github.sh . ; then
  echo "A file above seems to contain an API key or token. Remove it and run again."; exit 1; fi
BIG=$(find . -path ./.git -prune -o -type f -size +50M -print)
[ -z "$BIG" ] || { echo "Files over 50 MB (GitHub refuses 100 MB):"; echo "$BIG"; exit 1; }

# 2. Local repository and first commit
[ -d .git ] || git init -q -b main
git add -A
if git diff --cached --quiet; then echo "Nothing new to commit."; else
  git -c user.name="$(git config user.name || gh api user -q .login)" \
      -c user.email="$(git config user.email || echo "$(gh api user -q .id)+$(gh api user -q .login)@users.noreply.github.com")" \
      commit -q -m "Rotary in China workflow: sample edition (Harbin, B37-F01)"
fi
echo "Committed: $(git ls-files | wc -l | tr -d ' ') files, $(du -sh .git | cut -f1) in Git."

# 3. Create the repository on GitHub and push (or just push if it already exists)
OWNER=$(gh api user -q .login)
if gh repo view "$OWNER/$NAME" >/dev/null 2>&1; then
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$NAME.git"
  git push -u origin main
else
  gh repo create "$NAME" "--$VISIBILITY" --source=. --remote=origin --description "$DESCRIPTION" --push
fi
gh repo edit "$OWNER/$NAME" --add-topic "$TOPICS" >/dev/null 2>&1 || true

echo
echo "Done: https://github.com/$OWNER/$NAME  ($VISIBILITY)"
echo "To update later:   git add -A && git commit -m \"what changed\" && git push"
[ "$VISIBILITY" = private ] && echo "To make it public: gh repo edit $OWNER/$NAME --visibility public --accept-visibility-change-consequences"
exit 0
