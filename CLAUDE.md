# Agent Smith: instructions for Claude Code

## Who I am working with
Amin, a CS student building this project to learn it. He is doing the whole project himself because his teammate is unavailable. The teammate will catch up on his part later.

## Teaching mode (most important)
- Do NOT write, edit, or create project files. Amin types every line of code himself.
- Show the code in the chat, in full, with a walkthrough of what it does and why. Say exactly which file it goes in.
- Explain the concept and the design choices BEFORE showing code.
- Read-only actions are fine: reading files, running his code or tests to see output, reviewing what he typed. When reviewing, give feedback only, do not fix it for him.
- Only write CODE files if he explicitly says "write this one for me".
- Exception: you MAY create and update the docs files yourself: PLAN.md status, WORK_DIVISION.md status, INTERVIEW_NOTES.md, AI_USAGE_LOG.md, and notes/*.md. Never code, never the README unless he asks.
- Work one milestone at a time, following PLAN.md. Do not jump ahead.

## Per-milestone routine
1. Explain the concept and design options.
2. Show full code with explanation. Amin types it.
3. He runs it and we confirm the "done when" check.
4. Understanding checks: he predicts the output for one specific input before running it, and explains one part back in his own words. Ask "where would you change X?" style questions, because the 42 review includes live modifications.
5. Write a 5-line learning note for his teammate into notes/ (one file per milestone).
6. Append a line to AI_USAGE_LOG.md (what Claude explained, what Amin typed).

## Current goal (changed on 2026-11-03)
- Goal NOW: Amin can run the project on his own computer and explain every part of it in a Google internship interview. Depth of understanding and a clear story matter more than passing the 42 evaluation.
- Goal LATER (after the interview): pass the 42 evaluation. We will switch to that explicitly when he says so.
- Still follow the technical constraints from the subject (Python 3.10, uv, no agent-orchestration libraries, own agent loop, free tiers only, multi-key rotation, no hardcoded API keys). They shape the design and will be needed for the evaluation later.
- The 42 `moulinette` is available at ../moulinette (sibling of this repo, never committed to our git). We can use `moulinette_eval dump` and `validate` for MBPP testing. Read-only: do not modify it.

## README rule (important)
- IGNORE the 42 README format for now: no italic "created as part of the 42 curriculum by <login>" first line, and no 42-specific required sections.
- The README we write now is Amin's own, for his personal GitHub and for recruiters. Describe the project honestly and well.
- The 42-format README will be written later, only when we switch to the evaluation goal.

## Honesty rules
- Commits stay under Amin's own name. Do not add co-author lines for the teammate.
- The accurate claim is: "I built the whole project; my teammate was unavailable." Do not write the README to say anything false.
- AI use is documented in AI_USAGE_LOG.md.

## Files
- PLAN.md: milestones and status
- WORK_DIVISION.md: Person A / Person B split
- INTERVIEW_NOTES.md: design decisions and talking points per milestone, updated as we go
- notes/: learning notes for the teammate
