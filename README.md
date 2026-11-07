# netpipe — Capstone Companion Code

The finished code, and a checkpoint for every chapter, for the
[Nautomation Prime capstone project](https://www.nautomationprime.io/foundation/tutorials/capstone/):
a small intent-driven pipeline that validates, renders, pre-flights, deploys,
verifies and rolls back a configuration change on Cisco IOS-XE access switches.

```
intent (YAML) → validate → plan → preflight → deploy → verify → rollback + audit
```

## Build it first, then compare

The capstone is written to be built, not read. You'll learn far more by typing
each chapter's code than by cloning this repo. Use the repo for three things:

- **Checking your work.** Finished a chapter and something doesn't run? Diff
  your project against that chapter's checkpoint.
- **Skipping ahead.** Want to start at chapter 5 without building 1–4? Check out
  the `chapter-04` tag and carry on from there.
- **A known-good reference.** Every checkpoint's tests pass, and CI runs them
  on every push.

## Checkpoints

Each tag is the project as it stands **at the end of** that chapter.

| Tag | Chapter | What runs at this point |
|---|---|---|
| `chapter-01` | [1 — Intent and Repository Layout](https://www.nautomationprime.io/foundation/tutorials/capstone/01-intent-and-repository-layout/) | `python -m netpipe show` |
| `chapter-02` | [2 — Validating Intent](https://www.nautomationprime.io/foundation/tutorials/capstone/02-validating-intent/) | `validate` |
| `chapter-03` | [3 — Rendering Configuration](https://www.nautomationprime.io/foundation/tutorials/capstone/03-rendering-configuration/) | `validate`, `plan` |
| `chapter-04` | [4 — Credentials and Pre-Flight](https://www.nautomationprime.io/foundation/tutorials/capstone/04-credentials-and-preflight/) | … `preflight` |
| `chapter-05` | [5 — Deploying with Nornir](https://www.nautomationprime.io/foundation/tutorials/capstone/05-deploying-with-nornir/) | … `deploy` |
| `chapter-06` | [6 — Proving the Change with PyATS](https://www.nautomationprime.io/foundation/tutorials/capstone/06-proving-the-change-with-pyats/) | … `snapshot`, `verify` |
| `chapter-07` | [7 — Rollback and the Audit Record](https://www.nautomationprime.io/foundation/tutorials/capstone/07-rollback-and-audit/) | … `run` (the finished pipeline) |

`main` is the finished project, the same as `chapter-07`.

```bash
git clone https://github.com/Nautomation-Prime/netpipe-capstone.git
cd netpipe-capstone

# See what one chapter adds
git diff chapter-03 chapter-04

# Start your own work from a checkpoint
git switch -c my-netpipe chapter-04

# Compare your own project with a checkpoint
git switch --detach chapter-03
diff -ru ~/path/to/your/netpipe/netpipe netpipe
```

**"Use this template" on GitHub** gives you your own copy of the finished
project, which is the right starting point for adapting it to your network.
For the chapter checkpoints, clone this repo.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt pytest

pytest                             # no devices needed
python -m netpipe validate         # no devices needed
python -m netpipe plan --offline   # no devices needed
```

Everything from `preflight` onwards talks to devices. Copy `.env.example` to
`.env`, fill in lab credentials, and point `intent/man1.yaml` at your own lab
switches.

> **Use a lab.** This project writes configuration. CML, EVE-NG, GNS3 or a pair
> of spare switches on a bench. Never point a tutorial at production.

### Windows

Chapters 1–5 run natively. PyATS has no Windows build, so from chapter 6
onwards `pip install -r requirements.txt` fails on Windows. Use
[WSL](https://learn.microsoft.com/windows/wsl/install) for chapters 6 and 7.

## Where this repo differs from the pages

- **`requirements.txt`** holds known-good version ranges rather than the output
  of `pip freeze`. Your own frozen file will pin exact versions, which is fine.
- **`artefacts/` and `inventory/` don't exist in the repo.** They're
  gitignored build output, created the first time you run `plan` and `deploy`.

## Licence

Apache 2.0. See [LICENSE](LICENSE). Use it, adapt it, and run it at work.
