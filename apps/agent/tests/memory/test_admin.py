from sage.memory.admin import MemoryAdminSettings, doctor


def test_admin_settings_do_not_require_provider_keys_or_expose_dsns() -> None:
    settings = MemoryAdminSettings.from_env(
        {
            "SAGE_MEMORY_ENABLED": "false",
            "SAGE_MEMORY_DATABASE_URL": "postgresql://secret@example/db",
        }
    )

    assert "secret" not in repr(settings)


def test_doctor_checks_local_capabilities_without_database_or_model() -> None:
    result = doctor(MemoryAdminSettings())

    assert result["postgres"] == "not_configured"
    assert result["fts5"] == "ok"
    assert result["tree_sitter"]["python"] == "parsed"
