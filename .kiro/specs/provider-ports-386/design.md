# Design

> See requirements.md for context.

The provider ports/adapters pattern ensures `get_provider()` actually routes calls through the correct adapter at runtime, verified by spy/fake tests that confirm switching providers changes the execution path.
