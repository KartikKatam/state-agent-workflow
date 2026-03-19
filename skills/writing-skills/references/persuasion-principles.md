# Persuasion Principles for Skill Design

Based on Cialdini (2021) and Meincke et al. (2025, N=28,000 AI conversations). Persuasion techniques more than doubled compliance rates (33% → 72%, p < .001).

## The Seven Principles

### 1. Authority
Deference to expertise and non-negotiable framing.
- "YOU MUST", "Never", "Always", "No exceptions"
- Eliminates decision fatigue and rationalization
- **Use for:** Discipline-enforcing skills (TDD, testing requirements, safety-critical practices)

### 2. Commitment
Consistency with prior actions and public declarations.
- Require announcements: "I'm using [Skill Name]"
- Force explicit choices: "Choose A, B, or C"
- Use checklists for tracking
- **Use for:** Multi-step processes, accountability mechanisms

### 3. Scarcity
Urgency from time limits or sequential dependencies.
- "Before proceeding", "Immediately after X"
- Prevents "I'll do it later" rationalization
- **Use for:** Verification requirements, time-sensitive workflows

### 4. Social Proof
Conformity to established norms.
- "Every time", "Always", "X without Y = failure"
- Establishes universal standards
- **Use for:** Warning about common failures, reinforcing standards

### 5. Unity
Shared identity and collaborative language.
- "our codebase", "we're colleagues", shared goals
- **Use for:** Collaborative workflows, non-hierarchical practices
- **Don't use for:** Discipline enforcement (too soft)

### 6. Reciprocity
Obligation to return benefits. **Rarely needed** — other principles more effective.

### 7. Liking
**DO NOT USE for compliance.** Conflicts with honest feedback. Creates sycophancy.

## Combinations by Skill Type

| Skill Type | Use | Avoid |
|------------|-----|-------|
| Discipline-enforcing | Authority + Commitment + Social Proof | Liking, Reciprocity |
| Guidance/technique | Moderate Authority + Unity | Heavy authority |
| Collaborative | Unity + Commitment | Authority, Liking |
| Reference | Clarity only | All persuasion |

## Why This Works

**Bright-line rules reduce rationalization:** "YOU MUST" removes decision fatigue. Absolute language eliminates "is this an exception?" questions.

**Implementation intentions create automatic behavior:** Clear triggers + required actions = automatic execution. "When X, do Y" is more effective than "generally do Y."

**LLMs are parahuman:** Trained on human text containing these patterns. Authority language precedes compliance in training data. Commitment sequences (statement → action) are frequently modeled.

## The Ethics Test

Would this technique serve the user's genuine interests if they fully understood it?
- **Legitimate:** Ensuring critical practices are followed, preventing predictable failures
- **Illegitimate:** Manipulating for personal gain, false urgency, guilt-based compliance
