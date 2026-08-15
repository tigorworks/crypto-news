# state/

Berkas status yang dibawa antar run, di luar `docs/` supaya tidak ikut terbit
ke GitHub Pages.

- `subscribers.enc` — daftar pelanggan bot Telegram, **terenkripsi**.

Isinya adalah chat ID Telegram, yaitu identitas personal yang tetap. Repo ini
kemungkinan publik, jadi daftarnya tidak pernah ditulis sebagai teks biasa:
file dienkripsi dengan kunci turunan dari secret `TELEGRAM_SUBSCRIBER_KEY`.

Kalau secret itu belum diisi, fitur pelanggan dimatikan seluruhnya — bukan
diturunkan diam-diam jadi penyimpanan terbaca.

Jangan mengganti `TELEGRAM_SUBSCRIBER_KEY` setelah ada pelanggan terdaftar:
kunci lama tidak bisa dipulihkan dan daftarnya harus dibangun ulang.
