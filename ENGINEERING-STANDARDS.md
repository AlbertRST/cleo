# ENGINEERING STANDARDS — How every Cleo project is built

Set by the partner on 2026-09-03. These are **Cleo's job to enforce**, on every project, at
every session, without being reminded. The partner should never have to say any of this twice.

## Language

- **Default: Python 3.** Every project starts in Python unless the domain says otherwise.
- **Totally flexible when needed.** Any language or framework is allowed when it is the right
  tool (a Power BI project speaks DAX and M; a web front end speaks TypeScript). The choice is
  a decision: record it in that project's `DECISIONS.md` with the reason.
- Cleo's own engine stays standard library + PyYAML, tests in pytest — so it installs anywhere.

## Every project is born with

Done by `install-cleo.sh` the moment Cleo enters a project, and re-checked at every `/sleep`:

| Item | Rule |
|---|---|
| `git` | initialised if absent, with an initial commit. Never a remote without the partner's say. |
| `venv/` | a virtualenv named exactly `venv` at the project root, with `pyyaml` + `pytest` so the engine and tests run from it. Never committed. |
| `.env` | present from day one (created empty with a header) — secrets live here, nowhere else. Never committed. |
| `.gitignore` | present, and covering the things every project commonly ignores: `venv/`, `.env*` (except `.env.example`), databases (`*.db`, `*.sqlite*`), `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.log`, `.DS_Store`, editor junk, `.claude/settings.local.json`, `private/`, documents and media (`*.pdf`, `*.docx`, `*.xlsx`, images outside build inputs), and any tool-specific caches the project introduces. |
| `README.md` | says what the project is, how to set it up, how to run it. Kept true. |

## Git holds code and text only (Alberto, 2026-08-24; carried into doctrine 2026-09-07)

- **Never in a repo:** documents, scans, PDFs, office files, spreadsheets, databases, archives,
  media. They bloat history permanently, they are undiffable, and company documents carry
  personal data (ID scans, tax certificates, insurance policies) — once committed, in every
  clone forever. The control is the `.gitignore`, not discipline. Source documents stay on
  disk where the work needs them.
- **The one deliberate exception:** build inputs a site or app cannot be produced without
  (photography, fonts, brand SVGs under `src/assets/**`), masters capped at 2560px on the long
  edge. Raster intermediates stay disk-only.
- **Precedent:** a repo's history once carried 43 MB of exactly this and had to be rewritten
  with `git filter-repo` (44 MB → 0.4 MB). Rewriting history is the expensive fix; the ignore
  rule is the cheap one.

## Quality bar — industry practice, efficiency, scalability from day one

- **Clean and minimal.** No dead code, no commented-out blocks, no "just in case" files, no
  duplicate helpers. If it is not used, it is deleted (git remembers it).
- **Organised.** A predictable folder structure: source in a package (`src/<pkg>/` or
  `<pkg>/`), tests in `tests/`, scripts in `scripts/`, docs in `docs/`, data and outputs out
  of the repo or ignored. Nothing loose at the root that has a proper home.
- **Scalable by construction.** Configuration via environment, not constants; small modules
  with one job; no hidden global state; I/O at the edges. Choose the boring, proven library
  over the clever one.
- **Efficient.** Measure before optimising, but never write the obviously wasteful version.
- **Tested where it matters.** Behaviour that would hurt if it broke has a test. Tests run
  with one command from the venv.
- **Documented at the right altitude.** README for humans, docstrings for the why, no
  narrative comments restating the code.
- **Reproducible.** Pinned dependencies (`requirements.txt` or `pyproject.toml`), one command
  to set up, one to run, one to test.

## The standing hygiene pass (Cleo, every `/sleep`)

1. Root directory: anything that does not belong in the structure above gets filed or removed.
2. `git status`: no secrets, databases, caches, or `venv/` staged; `.gitignore` extended if a
   new kind of junk appeared.
3. Dead code and stale files introduced this session: removed, not "left for later".
4. README still true after today's changes.
5. Deviations from these standards that are deliberate: written into `DECISIONS.md`.

**Why this is doctrine and not a checklist for the partner:** projects rot one small
exception at a time. The only way a project stays manageable and scalable over years is if
the entity that outlives every session treats tidiness as part of the work, every time.
