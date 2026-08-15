# Ringkasan Pasar Bitcoin

Cronjob harian yang mengambil data pasar Bitcoin, menganalisanya, lalu menghasilkan dua keluaran:

1. **File JSON** yang dibaca halaman web statis di GitHub Pages
2. **Pesan Telegram** berisi ringkasan padat

Seluruh teks yang dilihat pengguna berbahasa Indonesia. Sifatnya informasional — sistem sengaja tidak mengeluarkan rekomendasi beli/jual maupun target harga.

---

## Cara Kerja

```
Binance/CoinGecko ─┐   (harga, klines, funding, OI)
Binance Futures    │   (posisi whale vs ritel)
mempool.space      ├─→ pipeline Python ─→ docs/data/latest.json ─→ GitHub Pages
alternative.me     │        │
Farside (ETF)      │        └──────────→ Telegram
Yahoo Finance      │
RSS kripto+makro ──┘
```

Pipeline berjalan berurutan dalam 18 langkah (lihat `src/main.py`). Hanya langkah pertama — pengambilan harga — yang fatal. Sumber lain boleh gagal; kegagalannya dicatat di `data_quality.failed_sources` dan pipeline tetap menghasilkan brief.

**Pemisahan tanggung jawab yang dipegang ketat:**

| Dikerjakan kode | Dikerjakan LLM |
|---|---|
| Menghitung seluruh indikator teknikal | **Menafsirkan** indikator itu |
| Mendeteksi pola sapuan likuiditas, absorpsi, breakout lemah | Menjelaskan arti pola tersebut |
| Menghitung rasio posisi whale vs ritel | Membaca apa arti divergensinya |
| Skor sentimen agregat | Menilai relevansi & mengklasifikasi berita |
| Reaksi harga vs berita | Menjelaskan mekanisme transmisi ke harga |
| Semua persentase dan perbandingan | Menulis narasi, outlook, dan critic |

Tidak ada satu pun angka di output yang dihitung oleh LLM. Prinsipnya tegas: **kode menghitung, LLM menafsirkan.** Model bahasa tidak bisa diandalkan untuk aritmatika 250 candle, jadi semua angka dihitung lebih dulu lalu dikirim jadi ke model.

### Yang dianalisa

- **Kenapa harga bergerak** — narasi 6–9 paragraf yang mengurai sebab pergerakan, plus daftar `penyebab_pergerakan` terurut lengkap dengan tingkat keyakinan dan dasar datanya. Kalau penyebabnya tidak jelas, model diwajibkan mengatakan begitu.
- **Pembacaan teknikal** — kondisi tiap timeframe, di mana 1D/4H/1H saling menguatkan, di mana saling bertentangan, dan apa yang membatalkan pembacaannya.
- **Sinyal palsu & pemain besar** — divergensi posisi top trader versus ritel, ditambah pola candle yang sering menandai pergerakan tidak tulus.
- **Pandangan ke depan** — skenario menguat/melemah beserta pemicunya, faktor geopolitik, keputusan besar yang dipantau, dan risiko utama.

### Deteksi sinyal palsu

Kode mendeteksi pola berikut dari geometri candle dan volume, lalu LLM menafsirkannya:

| Pola | Artinya |
|---|---|
| Sapuan likuiditas | Harga menembus swing lalu ditutup kembali — level dipicu tanpa diikuti |
| Penolakan atas/bawah | Wick jauh lebih panjang dari badan candle — ada penyerapan di area itu |
| Absorpsi volume | Volume besar tapi harga hampir tidak bergerak |
| Breakout volume lemah | Tertinggi baru dengan volume lebih kecil dari puncak sebelumnya |
| Posisi derivatif padat | Funding ekstrem berbarengan dengan open interest naik |

Ditambah divergensi posisi: Binance memisahkan statistik *top trader* (proksi pemain besar) dari *seluruh akun* (didominasi ritel). Ketika whale net short sementara ritel net long, itu pola distribusi klasik — dan sebaliknya untuk akumulasi.

Semua ini disajikan sebagai **petunjuk probabilistik, bukan bukti**. Prompt secara eksplisit melarang model mengarang cerita manipulasi dari sinyal yang tipis, dan setiap temuan wajib menyertakan tingkat keyakinan.

---

## Setup

### 1. Fork / clone repo

```bash
git clone https://github.com/tigorworks/crypto-news.git
cd crypto-news
pip install -r requirements.txt
```

### 2. Buat bot Telegram dan ambil chat ID

1. Buka [@BotFather](https://t.me/BotFather) di Telegram, kirim `/newbot`, ikuti instruksinya.
2. Simpan token yang diberikan (bentuknya `123456789:AAxx...`).
3. Kirim satu pesan apa pun ke bot barumu (bot tidak bisa memulai chat duluan).
4. Buka URL ini di browser, ganti `<TOKEN>` dengan tokenmu:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
5. Cari `"chat":{"id":123456789` — angka itu `TELEGRAM_CHAT_ID`.

Untuk mengirim ke grup: tambahkan bot ke grup, kirim satu pesan di grup, lalu ulangi langkah 4. ID grup diawali tanda minus.

### 3. Ambil API key OpenRouter

1. Daftar di [openrouter.ai](https://openrouter.ai), buka **Keys**, buat key baru.
2. Isi saldo secukupnya. Rantai analisa kini 8 langkah dengan keluaran naratif panjang, jadi batas bawaannya `max_cost_usd_per_run: 0.40`. Dengan dua run per hari, itu berarti **maksimal sekitar $24/bulan** — biasanya jauh di bawah itu karena batas ini adalah plafon, bukan tarif tetap. Turunkan angkanya kalau mau lebih hemat; langkah yang kena batas akan dilewati dan brief tetap terbit.

### 4. Isi nama model di `config.yaml`

Bagian `llm` sengaja diisi placeholder (`ISI-MODEL-...`) karena katalog OpenRouter berubah terus. Buka [openrouter.ai/models](https://openrouter.ai/models), lalu isi tiap step:

| Step | Pilih model yang | Alasan |
|---|---|---|
| `filter` | paling murah | tugasnya hanya memberi skor 0–100 |
| `classify` | kecil tapi patuh JSON | keluarannya terstruktur, bukan naratif |
| `mechanism` | menengah | butuh penalaran sebab-akibat |
| `technical` | menengah–kuat | menafsirkan indikator lintas timeframe |
| `whale` | menengah–kuat | membaca divergensi posisi dan pola manipulasi |
| `synthesis` | kuat | menulis analisa panjang berbahasa Indonesia |
| `outlook` | kuat | menggabungkan teknikal, makro, dan geopolitik |
| `critic` | kuat, **keluarga berbeda** dari `synthesis` | model yang sama cenderung tidak menemukan kesalahannya sendiri |

Tiap step berupa array — model kedua dipakai otomatis oleh OpenRouter kalau yang pertama error atau kena rate limit.

```yaml
llm:
  filter:    ["penyedia/model-murah", "penyedia/model-cadangan"]
  synthesis: ["penyedia/model-kuat", "penyedia/model-cadangan"]
  critic:    ["penyedia-lain/model-kuat", "penyedia-lain/model-cadangan"]
```

Kalau placeholder dibiarkan, langkah LLM otomatis dilewati dan brief tetap terbit — hanya tanpa bagian analisa AI.

### 5. Isi GitHub Secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Nama | Wajib |
|---|---|
| `OPENROUTER_API_KEY` | Ya |
| `TELEGRAM_TOKEN` | Ya |
| `TELEGRAM_CHAT_ID` | Ya |
| `FRED_API_KEY` | Tidak — dilewati kalau kosong |

### 6. Aktifkan GitHub Pages

**Settings → Pages → Source: Deploy from a branch**, pilih branch `main` dan folder **`/docs`**.

Setelah itu perbarui `site_url` dan `repo_url` di `config.yaml` supaya link di Telegram dan footer web menunjuk ke alamat yang benar.

### 7. Jalankan sekali untuk mengetes

**Actions → btc-brief → Run workflow**, atau jalankan lokal:

```bash
python -m src.main --dry-run   # tanpa kirim Telegram, tanpa tulis arsip
python -m src.main             # jalan penuh
```

Lihat hasilnya:

```bash
python -m http.server 8000 --directory docs
# buka http://localhost:8000
```

---

## Jadwal

Workflow berjalan dua kali sehari:

| Cron (UTC) | Waktu WIB |
|---|---|
| `0 0 * * *` | 07:00 |
| `0 13 * * *` | 20:00 |

Catatan penting:

- **Cron GitHub Actions memakai UTC.** WIB = UTC+7, jadi kurangi 7 jam dari waktu WIB yang diinginkan.
- **Jadwal bisa telat 5–30 menit** saat GitHub sedang sibuk. Ini normal dan di luar kendali repo.
- **Workflow otomatis nonaktif setelah 60 hari** repo tanpa aktivitas. GitHub mengirim email peringatan sebelum itu; cukup buka Actions dan aktifkan lagi.

---

## Struktur

```
src/
├── main.py                 # orkestrator 18 langkah
├── config.py               # baca env + config.yaml
├── collectors/
│   ├── binance.py          # harga, klines, funding, OI (+ fallback CoinGecko)
│   ├── market.py           # fear & greed, on-chain, arus ETF
│   ├── macro.py            # yfinance: DXY, yield, minyak, indeks (+ FRED opsional)
│   ├── news.py             # RSS + dedup + skor prioritas
│   ├── whale.py            # posisi top trader vs ritel, aliran taker
│   └── calendar.py         # agenda ekonomi 7 hari
├── analysis/
│   ├── technical.py        # indikator + deteksi sinyal palsu — murni kode
│   ├── llm.py              # klien OpenRouter + budget + logging biaya
│   └── news_analysis.py    # rangkaian 8 panggilan LLM
├── output/
│   ├── builder.py          # susun brief.json, diff, arsip
│   └── telegram.py         # render + kirim
└── utils/
    ├── http.py             # retry + backoff + timeout
    ├── format.py           # angka gaya Indonesia
    └── timezone.py         # helper WIB + format tanggal Indonesia

docs/                       # GitHub Pages
├── index.html
├── app.js
└── data/
    ├── latest.json         # brief terbaru
    ├── index.json          # daftar arsip
    └── archive/            # brief lama (retensi 90 hari)
```

---

## Guardrail

- **Budget LLM per run** dibatasi `max_cost_usd_per_run`. Begitu terlampaui, step LLM sisanya dihentikan dan brief tetap terbit dengan data seadanya.
- **Timeout 60 detik** per panggilan HTTP, retry maksimal 2× dengan exponential backoff.
- **Kredensial hanya lewat environment variable.** Tidak ada key di kode maupun di JSON keluaran.
- **JSON keluaran tidak memuat prompt atau API key** — repo kemungkinan publik.
- **Telegram dikirim sebelum operasi file/commit**, supaya kegagalan git tidak membatalkan notifikasi.
- **Critic memeriksa SELURUH bagian naratif** (narasi, teknikal, whale, outlook) terhadap data mentah. Kalau menemukan angka karangan, saran investasi, target harga, atau klaim dari pengetahuan luar, seluruh bagian AI ditahan.
- **Skenario ditulis kondisional**, merujuk level yang dihitung kode ("selama bertahan di atas X, kondisi Y cenderung berlanjut"). Target harga dan ajakan transaksi tetap dilarang, dan critic secara khusus diminta membedakan keduanya.
- **Setiap panggilan LLM dicatat** (model, token, biaya, durasi) ke stdout agar terlihat di log Actions.

## Pelabelan AI

Pengguna harus bisa membedakan sekilas mana angka faktual dan mana interpretasi mesin:

- Seluruh keluaran LLM dikurung dalam objek `ai` di `latest.json`.
- Di web, bagian AI diberi border indigo, latar berbeda, badge `✦ AI`, dan keterangan bahwa isinya dapat keliru.
- Chip hasil AI di kartu berita (sentimen, kekuatan, kategori, mekanisme) diberi ring indigo.
- Di Telegram, blok AI dipisah garis dan ditandai `✦ ANALISA AI`.
- Kalau critic menolak narasi, web menampilkan banner peringatan dan Telegram menampilkan `⚠️ Analisa AI ditahan karena tidak lolos verifikasi.`

---

## Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| Log berhenti di `BERHENTI: data harga tidak tersedia` | Binance dan CoinGecko sama-sama tidak bisa diakses. Biasanya sementara; cek lagi run berikutnya. |
| `failed_sources` memuat `etf_flow` | Struktur tabel Farside berubah. Tidak fatal — kolom ETF akan tampil "tidak tersedia". |
| Bagian whale kosong | Binance Futures memblokir IP runner. Tidak fatal — kartu posisi whale disembunyikan dan `failed_sources` memuat `whale`. |
| Brief terbit tanpa bagian AI | `OPENROUTER_API_KEY` kosong, nama model masih placeholder, atau budget per run tercapai. Cek `data_quality.catatan`. |
| Telegram tidak masuk | Pastikan sudah mengirim pesan pertama ke bot, dan `TELEGRAM_CHAT_ID` benar (ID grup diawali minus). |
| Halaman Pages kosong | GitHub Pages belum diarahkan ke folder `/docs`, atau `latest.json` belum pernah dibuat. |
| Workflow berhenti jalan sendiri | Repo tidak aktif 60 hari. Aktifkan kembali di tab Actions. |

---

## Disclaimer

Konten yang dihasilkan bersifat informasional dan **bukan saran investasi**. Bagian yang ditandai `✦ AI` dihasilkan model bahasa dan dapat mengandung kesalahan.
