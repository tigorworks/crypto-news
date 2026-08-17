"""Kelola daftar pelanggan bot Telegram.

Cara kerjanya: tiap run memanggil getUpdates, membaca perintah /start dan
/stop, lalu memperbarui daftar. Telegram menyimpan update yang belum diambil
selama 24 jam, jadi selama brief jalan minimal sekali sehari tidak ada
pendaftaran yang terlewat. Offset update terakhir ikut disimpan supaya
pesan yang sama tidak diproses dua kali.

SOAL PRIVASI — alasan file ini dienkripsi:

Chat ID Telegram adalah identitas personal yang tetap. Repo ini kemungkinan
publik, jadi menuliskan daftar chat ID apa adanya berarti mempublikasikan
siapa saja yang berlangganan. Karena itu daftarnya disimpan terenkripsi
dengan kunci dari secret TELEGRAM_SUBSCRIBER_KEY.

Kalau secret itu kosong, fitur pelanggan DIMATIKAN, bukan diturunkan jadi
teks biasa. Menyimpan diam-diam dalam bentuk terbaca adalah kegagalan yang
tidak terlihat sampai terlambat.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils.http import HttpError, get_json
from ..utils.timezone import iso_utc, now_utc

log = logging.getLogger(__name__)

# Salt tetap: kuncinya sendiri sudah rahasia, dan file ini hanya punya satu
# tujuan, jadi salt acak per file tidak menambah keamanan berarti tapi
# menyulitkan pemulihan kalau file rusak.
SALT = b"btc-market-brief-subscribers-v1"
ITERASI = 390_000

PESAN_SELAMAT_DATANG = (
    "👋 <b>Kamu berlangganan Nawala</b>\n\n"
    "<i>Ringkasan Pasar Kripto</i>\n\n"
    "Brief dikirim otomatis setiap pagi berisi harga, teknikal, posisi "
    "derivatif, data opsi, valuasi on-chain, dan analisa AI.\n\n"
    "Kirim /stop kapan saja untuk berhenti.\n\n"
    "<i>Informasi, bukan saran investasi.</i>"
)

PESAN_BERHENTI = (
    "Kamu berhenti berlangganan. Kirim /start kalau ingin bergabung lagi."
)


def _fernet(passphrase: str):
    """Bangun Fernet dari passphrase. Impor di dalam supaya modul tetap
    bisa dipakai walau `cryptography` belum terpasang."""
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=SALT, iterations=ITERASI
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8"))))


def state_kosong() -> Dict[str, Any]:
    return {"offset": 0, "chats": {}}


def muat(path: Path, passphrase: Optional[str]) -> Dict[str, Any]:
    """Baca state pelanggan. State kosong kalau belum ada atau tak terbaca."""
    if not passphrase or not path.exists():
        return state_kosong()
    try:
        isi = _fernet(passphrase).decrypt(path.read_bytes())
        state = json.loads(isi.decode("utf-8"))
        state.setdefault("offset", 0)
        state.setdefault("chats", {})
        return state
    except Exception as exc:
        # Kunci salah atau file rusak. Mengembalikan state kosong lebih aman
        # daripada menimpanya — file lamanya tidak ditulis ulang di sini.
        log.error(
            "Daftar pelanggan tidak bisa dibaca (%s). "
            "Pastikan TELEGRAM_SUBSCRIBER_KEY sama dengan saat file dibuat.",
            exc,
        )
        return state_kosong()


def simpan(path: Path, passphrase: Optional[str], state: Dict[str, Any]) -> bool:
    if not passphrase:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        isi = json.dumps(state, ensure_ascii=False).encode("utf-8")
        path.write_bytes(_fernet(passphrase).encrypt(isi))
        return True
    except Exception as exc:
        log.error("Gagal menyimpan daftar pelanggan: %s", exc)
        return False


def _perintah(teks: str) -> Optional[str]:
    """Kenali /start dan /stop, termasuk bentuk /start@NamaBot."""
    kata = (teks or "").strip().split()
    if not kata:
        return None
    awal = kata[0].lower().split("@")[0]
    return awal if awal in ("/start", "/stop") else None


def sinkronkan(token: str, state: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Ambil update baru dan perbarui daftar pelanggan.

    Return: (state_baru, chat_id_baru_mendaftar, chat_id_berhenti)
    """
    baru: List[str] = []
    keluar: List[str] = []

    try:
        hasil = get_json(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={
                "offset": state.get("offset", 0),
                "limit": 100,
                "timeout": 0,
                "allowed_updates": json.dumps(["message"]),
            },
            timeout=30,
        )
    except HttpError as exc:
        log.warning("Gagal mengambil update Telegram: %s", exc)
        return state, baru, keluar

    if not hasil.get("ok"):
        log.warning("Telegram menolak getUpdates: %s", hasil)
        return state, baru, keluar

    updates = hasil.get("result") or []
    offset_maks = state.get("offset", 0)

    for upd in updates:
        # Offset berikutnya selalu update_id tertinggi + 1, dipakai walau
        # pesannya diabaikan, supaya tidak diambil ulang selamanya.
        offset_maks = max(offset_maks, int(upd.get("update_id", 0)) + 1)

        pesan = upd.get("message") or {}
        chat = pesan.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue

        perintah = _perintah(pesan.get("text", ""))
        if perintah is None:
            continue

        kunci = str(chat_id)
        if perintah == "/start":
            if kunci not in state["chats"]:
                nama = " ".join(
                    filter(None, [chat.get("first_name"), chat.get("last_name")])
                ) or chat.get("title") or chat.get("username") or "tanpa nama"
                state["chats"][kunci] = {
                    "nama": nama[:80],
                    "tipe": chat.get("type"),
                    "sejak": iso_utc(now_utc()),
                }
                baru.append(kunci)
        else:  # /stop
            if state["chats"].pop(kunci, None) is not None:
                keluar.append(kunci)

    state["offset"] = offset_maks
    if updates:
        log.info(
            "Update Telegram diproses: %d, pelanggan baru %d, berhenti %d, total %d",
            len(updates), len(baru), len(keluar), len(state["chats"]),
        )
    return state, baru, keluar


def daftar_chat(state: Dict[str, Any]) -> List[str]:
    return list(state.get("chats", {}).keys())


def buang(state: Dict[str, Any], chat_id: str) -> None:
    """Keluarkan chat yang sudah memblokir bot atau tidak lagi valid."""
    if state.get("chats", {}).pop(str(chat_id), None) is not None:
        log.info("Pelanggan %s dikeluarkan dari daftar", chat_id)
