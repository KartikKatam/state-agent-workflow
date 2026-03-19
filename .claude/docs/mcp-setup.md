# MCP Setup Guide

This guide covers installation and configuration of the Model Context Protocol (MCP) servers used in the multi-agent TDD workflow.

---

## Required MCPs

| MCP | Used By | Purpose |
|-----|---------|---------|
| **Context7** | Researcher | Up-to-date library documentation |
| **GitHub** | Codebase Explorer | Git history analysis, PR/issue lookup |
| **Sequential Thinking** | Orchestrator, Plan-Architect | Structured reasoning at decision points |

---

## Installation

### Prerequisites

- Node.js 18+ (for npx)
- Claude Code CLI installed
- GitHub personal access token (for GitHub MCP)

### Context7

Provides version-specific library documentation to prevent AI hallucination of APIs.

```bash
# Basic installation (rate-limited)
claude mcp add context7 -- npx -y @upstash/context7-mcp

# With API key (higher rate limits)
claude mcp add context7 -e CONTEXT7_API_KEY=your_key -- npx -y @upstash/context7-mcp
```

**Get API Key**: https://context7.io (optional, free tier works for moderate usage)

**Tools Provided**:
- `resolve-library-id` - Find Context7 ID for a library name (call first)
- `query-docs` - Fetch documentation and code examples for a library ID

### GitHub

Provides repository access for git history analysis, PR lookup, and issue tracking.

```bash
# Personal account
claude mcp add github-personal -e GITHUB_TOKEN=your_token -- npx -y @modelcontextprotocol/server-github
```

**Get Token**: https://github.com/settings/tokens
- Required scopes: `repo`, `read:org` (for private repos)
- For public repos only: `public_repo` is sufficient

**Tools Provided** (subset used by this workflow):
- `list_commits` - Get commit history
- `get_commit` - Detailed commit info with diff
- `compare_branches` - See divergence between branches
- `search_code` - Find code patterns
- `get_pull_request` - PR details and discussions
- `search_issues` - Find related issues

### Sequential Thinking

Provides structured reasoning capabilities for complex decision-making.

```bash
claude mcp add sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking
```

**No API key required.**

**Tools Provided**:
- `sequentialthinking` - Dynamic reasoning through numbered thought steps. Supports:
  - `thoughtNumber` / `totalThoughts` - Track progress
  - `nextThoughtNeeded` - Continue or conclude
  - `isRevision` / `revisesThought` - Revise previous thinking
  - `branchFromThought` / `branchId` - Explore alternative paths

---

## Verification

After installation, verify MCPs are configured:

```bash
# List all configured MCPs
claude mcp list

# Expected output:
# context7: npx -y @upstash/context7-mcp
# github-personal: npx -y @modelcontextprotocol/server-github
# sequential-thinking: npx -y @modelcontextprotocol/server-sequential-thinking
```

Test each MCP:

```bash
# Test Context7 (in Claude Code session)
# Ask: "Use context7 to get pytest fixture documentation"

# Test GitHub (in Claude Code session)
# Ask: "Use github mcp to list recent commits on this repo"

# Test Sequential Thinking (in Claude Code session)
# Ask: "Use sequential thinking to analyze whether we should use async here"
```

---

## Environment Variables

Create a `.env` file or export these variables:

```bash
# Optional - for higher Context7 rate limits
export CONTEXT7_API_KEY=your_context7_key

# Required - for GitHub MCP
export GITHUB_TOKEN=your_github_token
```

For persistent configuration, add to your shell profile (`~/.bashrc`, `~/.zshrc`):

```bash
# MCP Configuration
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
export CONTEXT7_API_KEY=ctx7_xxxxxxxxxxxx  # Optional
```

---

## Troubleshooting

### "MCP not found" errors

```bash
# Remove and re-add the MCP
claude mcp remove context7
claude mcp add context7 -- npx -y @upstash/context7-mcp

# Clear npx cache if needed
npx clear-npx-cache
```

### GitHub authentication failures

```bash
# Verify token is set
echo $GITHUB_TOKEN

# Test token directly
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user

# If expired, generate new token at:
# https://github.com/settings/tokens
```

### Context7 rate limiting

If you see rate limit errors:
1. Wait a few minutes (free tier resets quickly)
2. Add API key for higher limits
3. Check existing research in `.claude/research/` first

### Sequential Thinking not responding

```bash
# Check if server starts
npx -y @modelcontextprotocol/server-sequential-thinking --help

# Remove and re-add
claude mcp remove sequential-thinking
claude mcp add sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking
```

### General debugging

```bash
# Check MCP server logs (if available)
claude mcp logs context7

# Verify Node.js version
node --version  # Should be 18+

# Check npx is working
npx --version
```

---

## Agent-MCP Mapping

This workflow restricts which agents can use which MCP tools:

| Agent | MCP | Allowed Tools |
|-------|-----|---------------|
| Researcher | Context7 | `resolve-library-id`, `query-docs` |
| Codebase Explorer | GitHub | `list_commits`, `search_code`, `compare_branches`, `get_pull_request`, `get_pull_request_files`, `get_pull_request_comments`, `get_pull_request_reviews`, `list_pull_requests`, `search_issues`, `get_issue`, `list_issues` |
| Orchestrator | Sequential Thinking | `sequentialthinking` |
| Plan-Architect | Sequential Thinking | `sequentialthinking` |
| Scribe | None | Uses `gh` CLI directly |
| Chunk-Coder | None | Requests research via orchestrator |

Tool filtering is handled at agent spawn time - agents only see their allowed tools.

---

## Rate Limits & Costs

| MCP | Free Tier | Paid/Token |
|-----|-----------|------------|
| Context7 | ~100 requests/day | Higher with API key |
| GitHub | 60 req/hour (no token) | 5000 req/hour (with token) |
| Sequential Thinking | Unlimited | N/A |

**Cost-saving tips**:
1. Check `.claude/research/` before querying Context7
2. Use git-history skill only when explicitly needed
3. Cache research results for reuse across sessions

---

## Security Notes

1. **Never commit tokens** - Use environment variables or `.env` files
2. **Least privilege** - GitHub token should only have required scopes
3. **Review MCP sources** - Only use official or verified MCP servers
4. **Prompt injection risk** - MCP responses could contain malicious content; agents should validate
