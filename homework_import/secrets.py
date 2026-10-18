"""Windows DPAPI helpers for encrypting the local AI API key at rest."""
from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


class SecretError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _configure():
    if os.name != "nt":
        raise SecretError("当前系统不支持 Windows DPAPI，请使用 AI_API_KEY 环境变量")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
                                         ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
                                           ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def protect(text: str) -> str:
    """Encrypt a secret for the current Windows user."""
    if not text:
        return ""
    crypt32, kernel32 = _configure()
    data = text.encode("utf-8")
    in_blob, keep_in = _blob(data)
    out_blob = _DataBlob()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), "cpp-homework-judge",
                                    None, None, None, 0x1, ctypes.byref(out_blob)):
        raise SecretError("Windows DPAPI 加密失败（错误码 %s）" % ctypes.get_last_error())
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def unprotect(token: str) -> str:
    """Decrypt a token created for the current Windows user."""
    if not token:
        return ""
    if not token.startswith("dpapi:"):
        return token  # old plaintext setting, migrated by the caller
    crypt32, kernel32 = _configure()
    try:
        encrypted = base64.b64decode(token.split(":", 1)[1], validate=True)
    except ValueError as exc:
        raise SecretError("保存的密钥格式损坏，请重新输入") from exc
    in_blob, keep_in = _blob(encrypted)
    out_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None,
                                      0x1, ctypes.byref(out_blob)):
        raise SecretError("当前 Windows 用户无法解密保存的密钥，请重新输入")
    try:
        data = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
    return data.decode("utf-8")
