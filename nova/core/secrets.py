from __future__ import annotations


class SecretStoreUnavailable(RuntimeError):
    pass


class SecretStore:
    """Small adapter over the operating-system credential store."""

    service = "N.O.V.A."

    def _keyring(self):
        try:
            import keyring
        except ImportError as exc:
            raise SecretStoreUnavailable(
                "Secure credential storage is unavailable; configure the provider key through its environment variable."
            ) from exc
        return keyring

    def get(self, key: str) -> str:
        try:
            return self._keyring().get_password(self.service, key) or ""
        except Exception as exc:  # keyring backends expose several exception types
            raise SecretStoreUnavailable("The operating-system credential store is unavailable.") from exc

    def set(self, key: str, value: str) -> None:
        if not value:
            return
        try:
            self._keyring().set_password(self.service, key, value)
        except Exception as exc:
            raise SecretStoreUnavailable("The operating-system credential store is unavailable.") from exc

    def delete(self, key: str) -> None:
        try:
            self._keyring().delete_password(self.service, key)
        except Exception:  # noqa: BLE001 - keyring backends expose unrelated exception types
            return
