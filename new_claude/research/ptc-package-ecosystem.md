# PTC Package Ecosystem Research

**Date:** 2026-02-28
**Status:** Complete

## Key Finding: Agents Genuinely Use Packages When Available

The "LLM-in-Sandbox" paper (arXiv:2601.16206, Jan 2026) is the strongest evidence:
- Math: models used code execution 43.4% of the time for numerical verification (numpy, scipy)
- Chemistry: models spontaneously installed Java runtime + OPSIN library (18.4% external resource usage)
- Long context: 8x token reduction by using grep/sed instead of processing full text
- Strong models (Claude Sonnet, GPT-5, DeepSeek): +0.5% to +24.2% gains with sandbox
- Token efficiency: 0.5x-0.8x consumption vs vanilla mode

**Critical insight:** "Strong LLMs, without additional training, exhibit generalization capabilities to leverage the code sandbox for non-code tasks."

## Who's Doing This

| Platform | Approach | Packages |
|----------|----------|----------|
| Anthropic Code Execution | Linux container, Python 3.11, 5GiB RAM, no internet | pandas, numpy, scipy, scikit-learn, statsmodels, matplotlib, seaborn, pillow, sympy, mpmath + file processing |
| OpenAI Code Interpreter | Sandbox, 400+ packages, no internet | pandas, numpy, scipy, scikit-learn, torch, matplotlib, seaborn, plotly, opencv, nltk, spacy |
| E2B | Firecracker microVMs, custom templates | pandas, numpy, scikit-learn, scipy, matplotlib, seaborn, plotly, requests, nltk, spacy |
| ipybox (Gradion AI) | Docker + IPython kernels, MCP integration | Minimal base + runtime pip install |
| freeact (Gradion AI) | Code actions as evolving tools | Skills pre-installed on ipybox |
| Docker Sandboxes | Official templates for Claude Code | Git, Docker CLI, Node.js, Python, Go + user extensions |
| Modal | Dynamic image definition via Python SDK | User-specified |
| HuggingFace smolagents | CodeAgent with authorized imports | Allowlisted modules |

## Package Tiers

### Tier 1: Universal (every platform includes these)
pandas, numpy, matplotlib, scipy, scikit-learn, sympy, pillow

### Tier 2: Common (most platforms include)
seaborn, statsmodels, pyarrow, openpyxl, pypdf, requests/httpx, beautifulsoup4, tqdm, python-dateutil

### Tier 3: Domain-Specific (SE / code analysis)
tree-sitter, ast (stdlib), networkx, radon, vulture, pygments, black, ruff, pytest, coverage, hypothesis

### Tier 3: Domain-Specific (robotics-cv)
opencv-python-headless, scikit-image, torch, torchvision, scipy, pyyaml

## Design Patterns

### 1. Base + Domain Layers (dominant pattern)
- Base: OS + Python + system tools + data science essentials
- Domain: Specialized packages via Dockerfile extension
- Runtime: Agent can pip install more

### 2. Capability Description via Skills
- Anthropic: SKILL.md with progressive disclosure (name → instructions → bundled scripts)
- Published as open standard at agentskills.io
- OpenAI adopted for Codex CLI

### 3. Code Actions as Evolving Tools (freeact)
- Successful code actions saved with typed interfaces
- Become reusable tools for later steps
- Agent's tool library grows over time

## Sources
- Anthropic Code Execution: https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool
- Anthropic PTC: https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling
- Anthropic Agent Skills: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- LLM-in-Sandbox paper: https://arxiv.org/abs/2601.16206
- E2B: https://e2b.dev/docs
- ipybox: https://github.com/gradion-ai/ipybox
- freeact: https://github.com/gradion-ai/freeact
- Code Actions as Tools: https://gradion-ai.github.io/agents-nanny/2025/12/16/code-actions-as-tools-evolving-tool-libraries-for-agents/
- Docker Sandboxes: https://docs.docker.com/ai/sandboxes/templates/
- smolagents: https://github.com/huggingface/smolagents
- Aider tree-sitter: https://aider.chat/2024/05/22/linting.html
