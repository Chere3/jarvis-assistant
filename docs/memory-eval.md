# Memory evaluation (scripts/memory_eval.py)

A synthetic set in a temporary directory (it never touches the personal memory): one preference stated by the user, one
imported document with three extracted facts (with quote and locator), and one question that has no answer in memory.

Real result (2026-09-04, claude-opus-5, total cost ~$0.30):

| Question | Retrieval | Attribution (cited) | Content | Unsupported claim |
|---|---|---|---|---|
| What preferences of mine do you know about? | 1.0 | 1.0 | ✓ | no |
| When is the Faro kickoff meeting and where did you get that from? | 1.0 | 1.0 | ✓ (quotes document and line) | no |
| What is Faro's budget? | 1.0 | 1.0 | ✓ | no |
| Who is the sensor supplier contact? | 1.0 | 1.0 | ✓ | no |
| What is my favorite color? (no data) | — | — | answers "I don't know, I have nothing stored" | no |
| What do you know about the Faro project? | 1.0 | 1.0 | ✗ (only because of the literal check for the word "Faro") | no |

Summary: mean retrieval 1.0, mean attribution 1.0, 0 unsupported claims across 6 cases.
Run it again: `python scripts/memory_eval.py` (uses the real Claude) or `--demo`.
