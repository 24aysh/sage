# Fix the calculator addition bug

The `add` function in `calculator.py` subtracts its second argument instead of
adding it. Correct the implementation so it returns the arithmetic sum.

Acceptance criteria:

- `add(7, 5)` returns `12`.
- `add(-2, 5)` returns `3`.
- The existing public function signature and type hints remain unchanged.
- Only `calculator.py` is changed.
- `python3 calculator_checks.py` passes.

Do not add dependencies, generated files, or unrelated cleanup.
