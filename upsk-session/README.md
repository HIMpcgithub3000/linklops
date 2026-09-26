# upsk-session

Session context for the upsk.to bootcamp, split so a chat session loads ~400 tokens
instead of ~3,500.

```
STATE.md          always paste this. Position, rules, environment, and an index.
ARCH.md           file map + the two-plane index argument + RLS predicate
PROFILE.md        teaching calibration, tracked gaps, style signals
log/module-01.md  M1 decision, bug, five open carry-forwards
log/module-02.md  M2 decision, BREAK diagnosis, fix, two open threads
FULL-BRIEF.md     everything unsplit — only for a cold handoff to another tool
```

## Why not `cto init` here

`claude-token-optimizer` writes its own `CLAUDE.md`. `../CLAUDE.md` is
`<!-- upsk:managed -->` and is what points the instructor session at
`~/.upsk/api.upsk.to/SKILL.md` — overwriting it breaks the instructor. Running it inside
`../upsk-system-design-workspace` would drop a `.claude/` folder into the graded
artifact. The pattern is applied by hand here instead: one small always-loaded file, the
rest on demand.

## Maintenance

Last thing at every module boundary:

1. Update the **Position** and **Blocked on** lines in `STATE.md`.
2. Write `log/module-NN.md` for the module that just closed — decision, bug, fix,
   carry-forwards. Anything unresolved goes under an "open threads" heading.
3. Move anything the next module needs into `ARCH.md` if it's structural, `PROFILE.md`
   if it's about how you're taught.

`FULL-BRIEF.md` goes stale. Regenerate it from the split files only when handing off to
a tool that can't read a directory.
