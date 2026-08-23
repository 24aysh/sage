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
