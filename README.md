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
RSS kripto+makro ──┤
Truth Social       │   (pernyataan tokoh)
whitehouse.gov     │
Google News ───────┘
```

Pipeline berjalan berurutan dalam 20 langkah (lihat `src/main.py`). Hanya langkah pertama — pengambilan harga — yang fatal. Sumber lain boleh gagal; kegagalannya dicatat di `data_quality.failed_sources` dan pipeline tetap menghasilkan brief.

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
- **Pernyataan tokoh berpengaruh** — ucapan pejabat dan tokoh yang berpotensi menggerakkan pasar, lengkap dengan status keasliannya.
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

### Pelacakan pernyataan tokoh

Pernyataan seperti "The Fed harus memangkas suku bunga" atau kebijakan soal cadangan Bitcoin bisa menggerakkan pasar dalam hitungan menit. Sistem melacaknya dari tiga lapis sumber:

| Lapis | Sumber | Sifat |
|---|---|---|
| Primer | Truth Social (`realDonaldTrump`) | postingan langsung |
| Resmi | `whitehouse.gov` presidential actions | dokumen resmi |
| Media | Google News RSS (beberapa query terarah) | laporan atas pernyataan di platform mana pun |

**Kenapa bukan langsung dari Twitter/X:** API gratis X sudah tidak mengizinkan pembacaan timeline sejak kebijakan barunya, dan instance Nitter praktis mati semua. Membaca X secara langsung sekarang menuntut API berbayar. Karena itu pernyataan di X tetap tertangkap, tapi lewat laporan media — bukan sebagai kutipan mentah.

Konsekuensinya penting dan sengaja tidak disembunyikan: sebagian item adalah **laporan tentang** pernyataan, bukan pernyataan itu sendiri. Setiap item karena itu wajib punya `status`:

- `verbatim` — teks memuat ucapan atau postingan langsung
- `dilaporkan_media` — media melaporkan tokoh mengatakan sesuatu
- `rumor` — bersumber "orang dalam", belum dikonfirmasi

Di web ketiganya diberi warna berbeda; di Telegram item rumor ditandai "belum terkonfirmasi". Prompt melarang model menaikkan status hanya karena beritanya terdengar meyakinkan.

Tugas utama langkah LLM di sini adalah **membuang derau**: pencarian berita untuk nama tokoh mengembalikan banyak artikel yang cuma menyebut namanya tanpa memuat pernyataan apa pun. Item semacam itu diberi relevansi 0 dan dibuang.

Daftar akun dan query bisa diubah di `config.yaml` bagian `statements` — menambah tokoh lain (misalnya ketua bank sentral) cukup menambah query, tanpa mengubah kode.

---

## Tampilan Mobile

Halaman dirancang mobile-first dan diuji di lebar 360px, 390px, dan 430px:

- **Tanpa gulir horizontal** di semua lebar tersebut
- **Target sentuh minimal 44px** pada perangkat sentuh, sesuai pedoman iOS dan Android
- **Ukuran teks minimal 11px** di ponsel; ukuran yang lebih kecil hanya dipakai mulai breakpoint `sm`
- **Nav lompat** khusus ponsel di bawah header — halaman ini panjang, jadi ada baris pintasan yang bisa digulir ke samping menuju tiap bagian
- **Daftar panjang dipotong** di layar sempit (4 berita, 3 pernyataan) dengan tombol "Tampilkan semua"; di desktop semuanya langsung tampil
- Grafik, tabel indikator, dan grid makro menyusun ulang jadi satu kolom

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
2. Isi saldo secukupnya. Rantai analisa terdiri dari 9 langkah dengan keluaran naratif panjang, dan `max_cost_usd_per_run: 0.40` adalah **plafon**, bukan tarif tetap — dengan model bawaan, satu run biasanya hanya ~$0,12–0,18 (lihat tabel di langkah 4). Turunkan plafonnya kalau mau lebih hemat; langkah yang kena batas dilewati dan brief tetap terbit.

### 4. Model LLM di `config.yaml`

Kesembilan step sudah terisi model yang wajar sebagai titik awal, jadi bisa langsung jalan tanpa diubah:

| Step | Model utama | Cadangan | Alasan |
|---|---|---|---|
| `filter` | `deepseek/deepseek-v3.2` | `anthropic/claude-haiku-4.5` | murah, tugasnya cuma skor 0–100 |
| `classify` | `deepseek/deepseek-v3.2` | `anthropic/claude-haiku-4.5` | patuh JSON, keluaran pendek |
| `mechanism` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | butuh penalaran sebab-akibat |
| `statements` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | menyaring pernyataan dari derau |
| `technical` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menafsirkan indikator lintas timeframe |
| `whale` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | membaca divergensi posisi |
| `synthesis` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menulis analisa panjang |
| `outlook` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menggabungkan banyak sumber |
| `critic` | `openai/gpt-5.1` | `google/gemini-3.1-flash-lite-preview` | **beda keluarga** dari `synthesis` |

Dengan kombinasi ini satu run biasanya menghabiskan sekitar **$0,12–0,18**, jauh di bawah plafon `max_cost_usd_per_run: 0.40`. Dua run per hari berarti kira-kira **$7–11 per bulan**.

Entri kedua tiap baris adalah cadangan: OpenRouter otomatis memakainya kalau model pertama error, kena rate limit, atau kehabisan kapasitas.

**Aturan yang jangan dilanggar:** `critic` harus dari **keluarga model berbeda** dengan `synthesis`. Model cenderung tidak menemukan kesalahannya sendiri — kalau keduanya sekeluarga, fungsi pemeriksaan jadi percuma. Saat ini `synthesis` memakai Anthropic dan `critic` memakai OpenAI.

#### Memelihara daftar model

Katalog OpenRouter berubah cukup sering: model pensiun, slug berganti, harga turun. Ada skrip untuk itu:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."

python -m scripts.list_models --cek          # periksa slug di config.yaml
python -m scripts.list_models                # 40 model termurah
python -m scripts.list_models --cari claude  # saring per nama
python -m scripts.list_models --maks-harga 1 # <= $1 per juta token input
python -m scripts.list_models --gratis       # model berharga nol
python -m scripts.list_models --urut konteks # urut dari konteks terpanjang
```

`--cek` membandingkan tiap slug di `config.yaml` dengan katalog yang sedang aktif dan menandai yang sudah tidak ada. Keluar dengan kode 1 kalau ada yang perlu diperbaiki, jadi bisa dipakai di CI.

Kalau sebuah slug ternyata sudah pensiun, sistem tidak akan mogok: model cadangan dipakai, dan kalau dua-duanya gagal step itu dilewati sementara brief tetap terbit — kegagalannya tercatat di `data_quality.catatan`.

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
├── main.py                 # orkestrator 20 langkah
├── config.py               # baca env + config.yaml
├── collectors/
│   ├── binance.py          # harga, klines, funding, OI (+ fallback CoinGecko)
│   ├── market.py           # fear & greed, on-chain, arus ETF
│   ├── macro.py            # yfinance: DXY, yield, minyak, indeks (+ FRED opsional)
│   ├── news.py             # RSS + dedup + skor prioritas
│   ├── whale.py            # posisi top trader vs ritel, aliran taker
│   ├── statements.py       # pernyataan tokoh berpengaruh
│   └── calendar.py         # agenda ekonomi 7 hari
├── analysis/
│   ├── technical.py        # indikator + deteksi sinyal palsu — murni kode
│   ├── llm.py              # klien OpenRouter + budget + logging biaya
│   └── news_analysis.py    # rangkaian 9 panggilan LLM
├── output/
│   ├── builder.py          # susun brief.json, diff, arsip
│   └── telegram.py         # render + kirim
└── utils/
    ├── http.py             # retry + backoff + timeout
    ├── format.py           # angka gaya Indonesia
    └── timezone.py         # helper WIB + format tanggal Indonesia

scripts/
└── list_models.py          # bantu memelihara daftar model OpenRouter

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
| Bagian pernyataan kosong | Wajar kalau memang tidak ada pernyataan relevan dalam 48 jam. Kalau selalu kosong, cek `sumber_gagal` di log — Truth Social memang sering memblokir IP data center. |
| Bagian whale kosong | Binance Futures memblokir IP runner. Tidak fatal — kartu posisi whale disembunyikan dan `failed_sources` memuat `whale`. |
| Brief terbit tanpa bagian AI | `OPENROUTER_API_KEY` kosong, nama model masih placeholder, atau budget per run tercapai. Cek `data_quality.catatan`. |
| Telegram tidak masuk | Pastikan sudah mengirim pesan pertama ke bot, dan `TELEGRAM_CHAT_ID` benar (ID grup diawali minus). |
| Halaman Pages kosong | GitHub Pages belum diarahkan ke folder `/docs`, atau `latest.json` belum pernah dibuat. |
| Workflow berhenti jalan sendiri | Repo tidak aktif 60 hari. Aktifkan kembali di tab Actions. |

---

## Disclaimer

Konten yang dihasilkan bersifat informasional dan **bukan saran investasi**. Bagian yang ditandai `✦ AI` dihasilkan model bahasa dan dapat mengandung kesalahan.
