# CLAUDE.md

This file provides mandatory guidance for Claude Code agents working in this repository. Follow these instructions exactly to ensure consistent development workflow.

## CRITICAL: Workflow Requirements
- Follow the mandatory step-by-step workflow below - no exceptions
- Use UV for ALL Python operations (`uv run [command]`) 
- All dependencies are managed in pyproject.toml - use `uv pip install -e ".[dev]"` to install them
- Work in git worktrees under `trees/` directory for all development
- **CONTINUE WORKING** after completing setup - do not stop to ask permission

## MANDATORY PRE-WORK CHECKLIST

Before starting ANY development work, you MUST complete this checklist:

### ✅ 1. Environment Setup (One-time)
- [ ] Run: `./scripts/setup_env.sh` to set up development environment with UV
- [ ] This installs ALL dependencies from pyproject.toml (including pytest, black, ruff, mypy)
- [ ] Verify setup: `uv run pytest tests/unit/test_components.py -v`
- [ ] **DO NOT install additional packages** - everything needed is already configured

### ✅ 2. Task Preparation
- [ ] Create git worktree: `git worktree add trees/[branch-name] -b task/[task-name]`
- [ ] Switch to worktree: `cd trees/[branch-name]`
- [ ] Move task file IN WORKTREE: `git mv tasks/backlog/[task-file].md tasks/active/[task-file].md`
- [ ] Update task metadata: Status to "active", add Started date, add progress entry
- [ ] Commit task move: `git commit -m "Start task: [task-name]"`

### ✅ 3. UV Environment Verification
- [ ] Verify UV detects environment: `uv run python --version`
- [ ] Test dependency access: `uv run pytest --version`

## DEVELOPMENT WORKFLOW (MANDATORY STEPS)

### Step 1: Start Development Work
- [ ] Work in your worktree directory: `trees/[branch-name]/`
- [ ] Make code changes following project conventions
- [ ] Use ONLY `uv run [command]` for all Python operations
- [ ] Update task file with progress notes

### Step 2: Code Quality Enforcement (MANDATORY)
After making ANY code changes, you MUST run these commands in order:

```bash
# In your worktree directory
uv run black .                    # Format code - MANDATORY
uv run ruff check .               # Lint code - MANDATORY  
uv run mypy src                   # Type check - MANDATORY
```

**These commands MUST pass before proceeding. Fix all issues before continuing.**

### Step 3: Testing Requirements (MANDATORY)
- [ ] Run relevant tests: `uv run pytest tests/unit/test_[component].py -v`
- [ ] If integration tests exist: `uv run pytest -m integration`
- [ ] All tests MUST pass before proceeding

### Step 4: Commit Changes
- [ ] Add changes: `git add .`
- [ ] Commit with descriptive message: `git commit -m "Implement [feature]: [description]"`
- [ ] Messages MUST start with present-tense verb, NO AI attribution

## TASK COMPLETION WORKFLOW (MANDATORY)

### Step 1: Final Quality Check
- [ ] Run complete test suite: `uv run pytest`
- [ ] Run slow/container tests locally: `uv run pytest -m slow -v` (REQUIRED - these skip in CI)
- [ ] Run final quality check: `uv run black . && uv run ruff check . && uv run mypy src`
- [ ] All commands MUST pass

### Step 2: Push and Create PR
- [ ] Push branch: `git push -u origin task/[task-name]`
- [ ] Create PR using gh CLI: `gh pr create --title "[Task Name]" --body "[Description]"`
- [ ] Update task file with PR URL and metadata

### Step 3: Task File Management
- [ ] Update task status to "completed"
- [ ] Add completion date and PR information
- [ ] Move task IN WORKTREE: `git mv tasks/active/[task-file].md tasks/completed/[task-file].md`
- [ ] Commit task completion: `git commit -m "Complete task: [task-name]"`

### Step 4: Cleanup (After PR Merge)
- [ ] Delete worktree: `git worktree remove trees/[branch-name]`
- [ ] Delete branch: `git branch -d task/[task-name]`

## GIT WORKTREE WORKFLOW

This project uses git worktrees in the `trees/` directory for parallel development:

### Creating a Worktree
```bash
# Create worktree with new branch
git worktree add trees/[branch-name] -b task/[task-name]

# Switch to worktree
cd trees/[branch-name]
```

### Working in Worktrees
- Each worktree is a complete working directory
- UV environment works the same in worktrees
- Main branch remains accessible in project root
- Multiple worktrees can exist simultaneously

### Worktree Cleanup
```bash
# Remove worktree when done
git worktree remove trees/[branch-name]

# Remove branch after PR merge
git branch -d task/[task-name]
```

## TOOLS & COMMANDS REFERENCE

### UV Commands (Use ONLY these)
```bash
uv run pytest                     # Run tests
uv run pytest -m unit           # Run unit tests
uv run pytest -m integration    # Run integration tests
uv run black .                  # Format code
uv run ruff check .             # Lint code
uv run mypy src                 # Type check
```

### GitHub Operations (Prefer gh CLI)
```bash
gh pr create --title "Title" --body "Description"  # Preferred
gh pr view                      # View current PR
gh pr merge                     # Merge PR  
gh repo view --web              # Open repo in browser

# MCP tools available as alternative if needed:
# mcp__github__create_pull_request, mcp__github__merge_pull_request, etc.
```

### Testing Categories
- Unit tests: `uv run pytest -m unit`
- Integration tests: `uv run pytest -m integration`  
- E2E tests: `uv run pytest -m e2e`
- Container tests: `uv run pytest -m container`
- Specific file: `uv run pytest tests/unit/test_bundle.py -v`

**📋 Complete Testing Strategy**: See [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) for comprehensive documentation of our testing approach, CI integration, and local testing commands.

## ENFORCEMENT RULES

### ❌ NEVER DO:
- Install additional packages (all dependencies are in pyproject.toml)
- Run Python commands without `uv run`
- Skip code quality checks (black, ruff, mypy)
- Work directly on main branch
- Create branches without worktrees
- Include AI attribution in commit messages
- Move task files with `mv` (use `git mv`)

### ✅ ALWAYS DO:
- Use `uv run` for all Python commands (dependencies are pre-managed)
- Run quality checks after every code change
- Work in worktrees under `trees/` directory  
- Use `git mv` for task file movements
- Update task progress regularly
- Test changes before committing
- Use descriptive commit messages
- Continue working after setup - don't stop to ask permission
- Prefer `gh` CLI for GitHub operations (MCP tools allowed as alternative)

## PROJECT STRUCTURE
```
├── trees/                     # Git worktrees (DO NOT commit)
│   ├── feature-branch-1/      # Worktree for task/feature-1
│   └── feature-branch-2/      # Worktree for task/feature-2  
├── tasks/
│   ├── backlog/              # Tasks ready to start
│   ├── active/               # Tasks currently being worked on
│   └── completed/            # Finished tasks
├── src/                      # Source code
└── tests/                    # Test files
```

## TROUBLESHOOTING

### UV Environment Issues
```bash
# Reset environment
./scripts/setup_env.sh --force-recreate

# Verify installation  
uv run python -c "import mcp_server_troubleshoot"
```

### Worktree Issues
```bash
# List worktrees
git worktree list

# Remove broken worktree
git worktree remove trees/[branch-name] --force
```