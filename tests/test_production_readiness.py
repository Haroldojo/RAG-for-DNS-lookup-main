from pathlib import Path

import pytest

from app.core.config import Settings


def test_production_configuration_fails_without_required_service_urls() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings(app_env="production", debug=False).validate_production()


def test_production_configuration_rejects_debug_mode() -> None:
    with pytest.raises(RuntimeError, match="DEBUG"):
        Settings(
            app_env="production",
            debug=True,
            database_url="postgresql://example",
            redis_url="redis://example",
            qdrant_url="http://example",
        ).validate_production()


def test_deployment_assets_keep_internal_services_private() -> None:
    root = Path(__file__).parents[1]
    compose = (root / "docker-compose.yml").read_text()

    assert "postgres:" in compose and "redis:" in compose and "qdrant:" in compose
    assert "6333:6333" not in compose and "5432:5432" not in compose and "6379:6379" not in compose
    assert (root / ".github" / "workflows" / "ci.yml").exists()
    assert (root / "docs" / "deployment.md").exists()
