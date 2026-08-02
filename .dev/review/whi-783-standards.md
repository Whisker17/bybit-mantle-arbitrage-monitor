# Standards review — WHI-783

Repo: /Users/whisker/Work/src/personal/bybit-mantle-arbitrage-monitor-whi-783
Fixed point: origin/dev
Diff command: `git diff origin/dev...HEAD`
Commits: `git log origin/dev..HEAD --oneline`
  e320dcc feat(web): split premium vs-Und into CEX/DEX groups (WHI-783)

Standards sources to read:
- AGENTS.md (architecture, TUI frozen for new features, Web is surface for new metrics)
- docs/DESIGN.md §4.2 module layout if relevant
- docs/GIT_WORKFLOW.md only if commit hygiene matters

Smell baseline (always apply; judgement calls only; repo docs override):
- Mysterious Name — rename if name doesn't reveal purpose
- Duplicated Code — extract shared shape
- Feature Envy — move method onto data it envies
- Data Clumps — bundle travelling fields
- Primitive Obsession — domain concept needs a type
- Repeated Switches — replace with polymorphism/map
- Shotgun Surgery — gather what changes together
- Divergent Change — split multi-reason modules
- Speculative Generality — delete unused abstraction
- Message Chains — hide long navigation
- Middle Man — cut pure delegates
- Refused Bequest — drop unused inheritance

Brief: Report — per file/hunk where relevant — (a) every place the diff violates a documented standard: cite the standard (file + the rule); and (b) any baseline smell you spot: name it and quote the hunk. Distinguish hard violations from judgement calls — documented-standard breaches can be hard, but baseline smells are always judgement calls, and a documented repo standard overrides the baseline. Skip anything tooling enforces. Under 400 words.

Run the diff yourself in this worktree. Output only the Standards report.
