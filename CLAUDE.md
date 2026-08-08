# CLAUDE.md — Fab Ops Intelligence

## Git and GitHub Operating Rules

Claude Code is authorized to manage the repository's normal Git workflow.

### Commit and Push Automation

After completing a requested implementation task:

1. Run the relevant tests and validation checks.
2. Review the changed files and `git diff`.
3. If the implementation is correct and the validation passes, create a focused Git commit.
4. Push the commit to the configured `origin/main`.
5. Leave the working tree clean.
6. Report the commit hash, commit message, tests executed, and push result.

Do not wait for a separate user instruction to commit and push after a completed implementation task unless the task is explicitly described as design-only, review-only, investigation-only, or otherwise instructed not to modify Git.

### Git Author Identity — Mandatory

All commits must use the repository owner's configured Git identity.

The configured Git author is:

* Name: `Raar1999`
* Email: `91361865+Raar1999@users.noreply.github.com`

Never change the configured Git identity.

### NO AI CO-AUTHORSHIP — ABSOLUTE RULE

NEVER add:

`Co-Authored-By:`

to any commit message.

NEVER add:

`Co-Authored-By: Claude`
`Co-Authored-By: Claude Code`
`Co-Authored-By: Anthropic`
or any equivalent AI/assistant attribution.

Claude Code must not identify itself as a co-author, contributor, author, or collaborator in Git commit metadata.

The commit author and committer must remain the configured repository owner.

Before every commit, inspect the final commit message and ensure that it contains no `Co-Authored-By` trailer.

### Commit Message Rules

Use concise, conventional commit messages.

Examples:

* `feat: add answer-blind scenario engine`
* `feat: add chamber fault scenario`
* `test: add FabSim leakage checks`
* `docs: finalize Phase 1 scenario specification`
* `refactor: isolate observable data plane`

Do not mention Claude, Claude Code, Anthropic, AI assistance, or co-authorship in commit messages.

Keep commits focused on the completed task.

### History Safety

Do NOT perform:

* `git rebase`
* `git reset --hard`
* force pushes
* history rewriting
* deletion of branches
* destructive Git operations

unless explicitly instructed by the user.

Normal commit and push operations are permitted after successful implementation and validation.

Never use:

`git push --force`

for normal project work.

### Pre-Push Verification

Before pushing a completed implementation:

1. Run the relevant test suite.
2. Check `git status`.
3. Inspect `git diff`.
4. Confirm no unintended files are included.
5. Confirm no secrets or credentials are being committed.
6. Confirm the commit message contains no `Co-Authored-By` trailer.
7. Push to `origin/main`.

If validation fails, do NOT commit or push the broken implementation.

### Design-Only Tasks

If the user explicitly says:

* design only
* architecture review
* do not implement
* do not modify source
* do not commit

then these instructions are overridden for that task.

In such cases, do not modify source code or create commits.

### GitHub Repository

The canonical remote is:

`origin`

The primary branch is:

`main`

Push completed validated work to:

`origin/main`

### Final Report After Automatic Commit/Push

After each automatically committed and pushed implementation task, report:

* What was implemented
* Tests/validation performed
* Commit hash
* Commit message
* Push result
* Current working-tree status

Do not claim a push succeeded unless Git actually reports success.
