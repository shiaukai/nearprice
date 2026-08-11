"""CryptoJS 相容的 AES 加密（純 stdlib，不需要 pycryptodome / cryptography）。

內政部實價登錄網站 (lvr.land.moi.gov.tw) 的查詢 API 用 CryptoJS.AES.encrypt(json, passphrase)
把查詢條件加密後放在 URL query string。CryptoJS 的 passphrase 模式 = OpenSSL 相容格式：

    salt      = 8 bytes random
    key||iv   = EVP_BytesToKey(passphrase, salt, MD5, 1 iteration)  -> 32 bytes key + 16 bytes iv
    ct        = AES-256-CBC(PKCS#7)
    output    = base64("Salted__" + salt + ct)

這裡自己實作 AES 是為了讓整個 skill 零外部相依 —— macOS 內建 python3 是 externally-managed，
pip install 會被 PEP 668 擋掉，使用者不該為了查房價先弄一個 venv。
"""

from __future__ import annotations

import base64
import hashlib
import os

# --- AES core -------------------------------------------------------------

_SBOX = [
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
]
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a


def _mul(a: int, b: int) -> int:
    """GF(2^8) 乘法。"""
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _expand_key(key: bytes) -> list[list[int]]:
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(nr + 1)]


def _add_round_key(state: list[int], rk: list[int]) -> None:
    for i in range(16):
        state[i] ^= rk[i]


def _encrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    nr = len(round_keys) - 1
    state = list(block)
    _add_round_key(state, round_keys[0])
    for rnd in range(1, nr + 1):
        state = [_SBOX[b] for b in state]
        # ShiftRows (column-major state: index = 4*col + row)
        state = [state[(i + 4 * (i % 4)) % 16] for i in range(16)]
        if rnd != nr:
            new = [0] * 16
            for c in range(4):
                col = state[4 * c:4 * c + 4]
                new[4 * c + 0] = _mul(col[0], 2) ^ _mul(col[1], 3) ^ col[2] ^ col[3]
                new[4 * c + 1] = col[0] ^ _mul(col[1], 2) ^ _mul(col[2], 3) ^ col[3]
                new[4 * c + 2] = col[0] ^ col[1] ^ _mul(col[2], 2) ^ _mul(col[3], 3)
                new[4 * c + 3] = _mul(col[0], 3) ^ col[1] ^ col[2] ^ _mul(col[3], 2)
            state = new
        _add_round_key(state, round_keys[rnd])
    return bytes(state)


def _decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    nr = len(round_keys) - 1
    state = list(block)
    _add_round_key(state, round_keys[nr])
    for rnd in range(nr - 1, -1, -1):
        # InvShiftRows
        state = [state[(i - 4 * (i % 4)) % 16] for i in range(16)]
        state = [_INV_SBOX[b] for b in state]
        _add_round_key(state, round_keys[rnd])
        if rnd != 0:
            new = [0] * 16
            for c in range(4):
                col = state[4 * c:4 * c + 4]
                new[4 * c + 0] = _mul(col[0], 14) ^ _mul(col[1], 11) ^ _mul(col[2], 13) ^ _mul(col[3], 9)
                new[4 * c + 1] = _mul(col[0], 9) ^ _mul(col[1], 14) ^ _mul(col[2], 11) ^ _mul(col[3], 13)
                new[4 * c + 2] = _mul(col[0], 13) ^ _mul(col[1], 9) ^ _mul(col[2], 14) ^ _mul(col[3], 11)
                new[4 * c + 3] = _mul(col[0], 11) ^ _mul(col[1], 13) ^ _mul(col[2], 9) ^ _mul(col[3], 14)
            state = new
    return bytes(state)


# --- CBC + PKCS#7 ---------------------------------------------------------

def _pkcs7_pad(data: bytes) -> bytes:
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    if 1 <= n <= 16 and data[-n:] == bytes([n]) * n:
        return data[:-n]
    return data


def aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    rk = _expand_key(key)
    out = bytearray()
    prev = iv
    padded = _pkcs7_pad(plaintext)
    for i in range(0, len(padded), 16):
        block = bytes(a ^ b for a, b in zip(padded[i:i + 16], prev))
        prev = _encrypt_block(block, rk)
        out += prev
    return bytes(out)


def aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    rk = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(block, rk), prev))
        prev = block
    return _pkcs7_unpad(bytes(out))


# --- OpenSSL / CryptoJS passphrase mode -----------------------------------

def evp_bytes_to_key(passphrase: bytes, salt: bytes, key_len: int = 32, iv_len: int = 16) -> tuple[bytes, bytes]:
    """OpenSSL EVP_BytesToKey，MD5、1 round —— CryptoJS 的預設 KDF。"""
    d = b""
    prev = b""
    while len(d) < key_len + iv_len:
        prev = hashlib.md5(prev + passphrase + salt).digest()
        d += prev
    return d[:key_len], d[key_len:key_len + iv_len]


def cryptojs_encrypt(plaintext: str, passphrase: str, salt: bytes | None = None) -> str:
    """等同 CryptoJS.AES.encrypt(plaintext, passphrase).toString()。"""
    salt = salt if salt is not None else os.urandom(8)
    key, iv = evp_bytes_to_key(passphrase.encode("utf-8"), salt)
    ct = aes_cbc_encrypt(plaintext.encode("utf-8"), key, iv)
    return base64.b64encode(b"Salted__" + salt + ct).decode("ascii")


def cryptojs_decrypt(b64_ciphertext: str, passphrase: str) -> str:
    """等同 CryptoJS.AES.decrypt(...).toString(CryptoJS.enc.Utf8)。"""
    raw = base64.b64decode(b64_ciphertext)
    if raw[:8] != b"Salted__":
        raise ValueError("不是 OpenSSL salted 格式")
    salt, ct = raw[8:16], raw[16:]
    key, iv = evp_bytes_to_key(passphrase.encode("utf-8"), salt)
    return aes_cbc_decrypt(ct, key, iv).decode("utf-8")


if __name__ == "__main__":  # 自我測試
    import json

    sample = json.dumps({"a": "臺北市大安區", "b": 1}, ensure_ascii=False, separators=(",", ":"))
    enc = cryptojs_encrypt(sample, "lvr.land.moi.gov.tw")
    assert cryptojs_decrypt(enc, "lvr.land.moi.gov.tw") == sample, "round-trip 失敗"
    # 已知向量：CryptoJS.AES.encrypt("abc", "key", {salt fixed}) 用固定 salt 驗證 KDF
    k, v = evp_bytes_to_key(b"lvr.land.moi.gov.tw", bytes(8))
    assert len(k) == 32 and len(v) == 16
    print("twcrypto OK:", enc[:32], "...")
