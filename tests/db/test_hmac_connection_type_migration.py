"""Schema v44: cms_hmac 连接重命名为平台内置 hmac_token 类型。"""

from __future__ import annotations

import json

from server.app.db.migrations import migrate_hmac_connection_type
from server.app.db.transaction import write_transaction
from server.app.services.connection_adapters import get_adapter
from tests.postgres_support import TEST_DATABASE_URL

_CONFIG = {
    "app_id": "app",
    "nonce": "nonce",
    "token_url": "http://cms/token",
    "secret": {"secret_ref": "conn:cms-internal:secret"},
}


def _insert_connection(conn, key: str, type_name: str) -> None:
    conn.execute(
        "insert into external_connections(key, type, display_name, config_json)"
        " values (%s, %s, '', %s) on conflict(key) do nothing",
        (key, type_name, json.dumps(_CONFIG, ensure_ascii=False)),
    )


def _type_of(conn, key: str) -> str:
    row = conn.execute("select type from external_connections where key=%s", (key,)).fetchone()
    return str(row["type"])


def test_cms_hmac_connections_retype_to_platform_builtin() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_connection(conn, "hmac-mig-cms", "cms_hmac")
        _insert_connection(conn, "hmac-mig-static", "static_bearer")

        migrate_hmac_connection_type(conn)

        assert _type_of(conn, "hmac-mig-cms") == "hmac_token"
        # Other types and the stored config/secret refs stay untouched.
        assert _type_of(conn, "hmac-mig-static") == "static_bearer"
        row = conn.execute(
            "select config_json from external_connections where key='hmac-mig-cms'"
        ).fetchone()
        assert json.loads(row["config_json"]) == _CONFIG
    assert get_adapter("hmac_token").secret_keys == ("secret",)


def test_migration_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_connection(conn, "hmac-mig-idem", "cms_hmac")
        migrate_hmac_connection_type(conn)
        migrate_hmac_connection_type(conn)
        assert _type_of(conn, "hmac-mig-idem") == "hmac_token"
