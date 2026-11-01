"""Where the device token lives.

The token is a bearer credential: whoever holds it can read this client's reminder queue. It is
therefore kept in the **OS-protected store** — Windows Credential Manager, which protects the secret
with the owner's logon and Data Protection API — and never in the configuration file, the journal, a
log line or a command-line argument.

On a platform without that store the companion refuses to hold a token at all rather than writing one
to a plain file. Tests use an in-memory store; nothing else may.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Protocol

from .errors import CredentialUnavailable

#: Credential Manager constants (wincred.h).
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168

MAX_TOKEN_BYTES = 4096


def credential_target(host: str, port: int) -> str:
    """The Credential Manager target name for one server.

    One entry per server, so pairing with a second server cannot overwrite the first token and
    ``revoke``/``forget`` remove exactly one credential.
    """
    return f"JARVIS/companion/{host}:{port}"


class CredentialStore(Protocol):
    """Minimal secret store: get, set, delete. No listing, no export."""

    name: str

    def available(self) -> tuple[bool, str]: ...

    def get(self, target: str) -> str | None: ...

    def set(self, target: str, secret: str) -> None: ...

    def delete(self, target: str) -> bool: ...


class _Credential(ctypes.Structure):
    """CREDENTIALW. Only present to satisfy wincred.h; never instantiated off Windows."""

    _fields_ = [
        ("Flags", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", ctypes.c_byte * 8),
        ("CredentialBlobSize", ctypes.c_uint32),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", ctypes.c_uint32),
        ("AttributeCount", ctypes.c_uint32),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


class WindowsCredentialStore:
    """Windows Credential Manager (advapi32 CredWriteW/CredReadW/CredDeleteW).

    The secret is written as UTF-16LE into a generic credential with ``CRED_PERSIST_LOCAL_MACHINE``,
    so it survives reboots for this Windows account and is readable by no other account.
    """

    name = "windows-credential-manager"

    def available(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, f"Windows Credential Manager is not available on {sys.platform}"
        return True, "Windows Credential Manager (advapi32)"

    # ------------------------------------------------------------------ internals
    def _advapi32(self):
        ok, reason = self.available()
        if not ok:
            raise CredentialUnavailable(reason, detail={"platform": sys.platform})
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        advapi32.CredWriteW.argtypes = [ctypes.POINTER(_Credential), ctypes.c_uint32]
        advapi32.CredWriteW.restype = ctypes.c_bool
        advapi32.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                       ctypes.POINTER(ctypes.POINTER(_Credential))]
        advapi32.CredReadW.restype = ctypes.c_bool
        advapi32.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        advapi32.CredDeleteW.restype = ctypes.c_bool
        advapi32.CredFree.argtypes = [ctypes.c_void_p]
        advapi32.CredFree.restype = None
        kernel32.GetLastError.restype = ctypes.c_uint32
        return advapi32, kernel32

    # ------------------------------------------------------------------ protocol
    def get(self, target: str) -> str | None:
        advapi32, kernel32 = self._advapi32()
        pointer = ctypes.POINTER(_Credential)()
        ok = advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer))
        if not ok:
            error = kernel32.GetLastError()
            if error == ERROR_NOT_FOUND:
                return None
            raise CredentialUnavailable(
                f"Credential Manager could not read {target!r} (WinError {error})",
                detail={"winerror": int(error)},
            )
        try:
            blob = pointer.contents
            size = int(blob.CredentialBlobSize)
            if size <= 0:
                return None
            raw = ctypes.string_at(blob.CredentialBlob, size)
            return raw.decode("utf-16-le").rstrip("\x00")
        finally:
            advapi32.CredFree(pointer)

    def set(self, target: str, secret: str) -> None:
        advapi32, kernel32 = self._advapi32()
        encoded = secret.encode("utf-16-le")
        if len(encoded) > MAX_TOKEN_BYTES:
            raise CredentialUnavailable("the token is larger than the store accepts")
        buffer = ctypes.create_string_buffer(encoded, len(encoded))
        credential = _Credential()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "J.A.R.V.I.S. companion device token"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "jarvis-companion"
        if not advapi32.CredWriteW(ctypes.byref(credential), 0):
            error = kernel32.GetLastError()
            raise CredentialUnavailable(
                f"Credential Manager could not store {target!r} (WinError {error})",
                detail={"winerror": int(error)},
            )

    def delete(self, target: str) -> bool:
        advapi32, kernel32 = self._advapi32()
        if advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
            return True
        error = kernel32.GetLastError()
        if error == ERROR_NOT_FOUND:
            return False
        raise CredentialUnavailable(
            f"Credential Manager could not delete {target!r} (WinError {error})",
            detail={"winerror": int(error)},
        )


class InMemoryCredentialStore:
    """Test double. Never used by the shipped command line: it protects nothing."""

    name = "in-memory (test only)"

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def available(self) -> tuple[bool, str]:
        return True, "in-memory store (tests only; no OS protection)"

    def get(self, target: str) -> str | None:
        return self._secrets.get(target)

    def set(self, target: str, secret: str) -> None:
        self._secrets[target] = secret

    def delete(self, target: str) -> bool:
        return self._secrets.pop(target, None) is not None


def default_credential_store() -> CredentialStore:
    """The store the shipped client uses: the OS store, or a refusal.

    Deliberately no plaintext fallback: a file-backed store would be a different security claim, and
    the owner would not have been told.
    """
    if sys.platform == "win32":
        return WindowsCredentialStore()
    raise CredentialUnavailable(
        "this is the Windows companion and no OS-protected credential store exists on "
        f"{sys.platform}; there is no plaintext fallback by design",
        detail={"platform": sys.platform},
    )


__all__ = [
    "CredentialStore",
    "InMemoryCredentialStore",
    "MAX_TOKEN_BYTES",
    "WindowsCredentialStore",
    "credential_target",
    "default_credential_store",
]
