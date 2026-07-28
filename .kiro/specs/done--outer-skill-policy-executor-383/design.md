# Design

> See requirements.md for context.

The policy executor provides a restricted runtime that:
1. Loads policy schemas per skill family (source / analysis / report / evaluation / improvement)
2. Compiles policies through a validated compiler
3. Executes actions within strict boundaries (no trust weight / PIT / evidence / security / cost / deploy mutations)
4. Fails closed on forbidden keys, unknown actions, and code injection attempts
