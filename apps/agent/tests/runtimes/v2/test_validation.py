import pytest

from sage.errors import RepositoryError
from sage.runtimes.v2.validation import normalize_patch


def test_normalize_patch_accepts_unified_diff_code_fence() -> None:
    patch = normalize_patch(
        """```diff
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
```"""
    )

    assert patch.startswith("--- a/app.py\n+++ b/app.py")
    assert patch.endswith("\n")


def test_normalize_patch_rejects_apply_patch_markers_with_repair_guidance() -> None:
    with pytest.raises(RepositoryError, match="apply-patch markers"):
        normalize_patch(
            """*** Begin Patch
*** Update File: app.py
@@
-old
+new
*** End Patch"""
        )


def test_normalize_patch_canonicalizes_bare_dev_null_header() -> None:
    patch = normalize_patch(
        """diff --git a/new.py b/new.py
new file mode 100644
--- dev/null
+++ b/new.py
@@ -0,0 +1 @@
+value = 1
"""
    )

    assert "--- /dev/null\n" in patch
