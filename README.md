# Ringkasan Pasar Bitcoin

Cronjob harian yang mengambil data pasar Bitcoin, menganalisanya, lalu menghasilkan dua keluaran:

1. **File JSON** yang dibaca halaman web statis di GitHub Pages
2. **Pesan Telegram** berisi ringkasan padat

Seluruh teks yang dilihat pengguna berbahasa Indonesia. Sifatnya informasional — sistem sengaja tidak mengeluarkan rekomendasi beli/jual maupun target harga.

---

## Cara Kerja

```
Binance/CoinGecko ─┐   (harga, klines)
Binance/Bybit      │   (funding, OI, posisi)
Deribit            │   (opsi: DVOL, skew, max pain)
Coin Metrics       │   (MVRV, NVT, alamat aktif)
Coinbase           │   (premium AS)
mempool.space      ├─→ pipeline Python ─→ docs/data/latest.json ─→ GitHub Pages
alternative.me     │        │
Farside (ETF)      │        └──────────→ Telegram
Yahoo Finance      │
RSS kripto+makro ──┤
Truth Social       │   (pernyataan tokoh)
whitehouse.gov     │
Google News ───────┘
```

Pipeline berjalan berurutan dalam 21 langkah (lihat `src/main.py`). Hanya langkah pertama — pengambilan harga — yang fatal. Sumber lain boleh gagal; kegagalannya dicatat di `data_quality.failed_sources` dan pipeline tetap menghasilkan brief.

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

### Bentuk analisa harian

Narasi AI mengikuti struktur laporan analis, bukan ringkasan bebas:

| Bagian | Isinya |
|---|---|
| Judul | Temuan utamanya, bukan "Update Harga BTC" |
| Posisi harga | Angka terkini, perubahan, jarak ke support/resistance |
| Penyebab | Rantai sebab-akibat lengkap dengan angka. Kalau penyebabnya teknikal, dikatakan teknikal |
| Data pendukung | 2–4 poin berangka |
| Peta level | Support & resistance konkret, plus arti kalau ditembus |
| Yang perlu diwaspadai | Argumen penyeimbang — wajib ada |
| Katalis berikutnya | Agenda dalam WIB |
| Kesimpulan | 2–3 kalimat; sering kali "belum ada yang perlu dilakukan" |

Prompt-nya menuntut hal yang sering dilewatkan: menelusuri rantai transmisi alih-alih berhenti di korelasi permukaan, membedakan yang **sudah tercermin di harga** dari **kejutan**, dan mengakui terus terang saat pergerakan bersifat teknikal — *"Tidak ada katalis berita spesifik dalam 24 jam terakhir; pergerakan ini konsisten dengan [mekanisme], bukan perubahan fundamental."*

Kata-kata hype dilarang, begitu pula prediksi harga sebagai kepastian dan rekomendasi beli/jual langsung.

### Kalau critic menemukan masalah

Critic memeriksa seluruh bagian naratif terhadap data mentah yang **persis sama** dengan yang dipakai penulisnya. Kalau ada temuan fatal:

1. **Satu putaran revisi** — narasi dikirim balik beserta daftar temuan untuk diperbaiki, lalu diperiksa ulang.
2. **Kalau masih gagal, hanya bagian bermasalah yang ditahan** — bukan seluruh analisa. Pembacaan teknikal, analisa whale, dan outlook tetap terkirim kalau tidak ikut ditandai.

Bagian yang ditahan disebutkan terus terang di web dan Telegram, lengkap dengan namanya.

### Data tingkat institusional

Sebagian besar dashboard kripto berhenti di harga, RSI, dan Fear & Greed — semuanya data retail. Yang berikut ini biasanya dijual berlangganan mahal, padahal tersedia gratis lewat API publik:

**Opsi Deribit** — Deribit menguasai mayoritas volume opsi BTC, jadi posisinya mencerminkan taruhan institusional.

| Metrik | Artinya |
|---|---|
| DVOL | Indeks volatilitas implied BTC — "VIX"-nya Bitcoin. Naik = pasar membayar mahal untuk proteksi |
| Put/call ratio | Berapa banyak proteksi turun dibanding taruhan naik |
| Skew put−call | Selisih IV put vs call di sekitar ATM. Positif = ketakutan berbayar |
| Max pain | Strike yang paling merugikan pemegang opsi saat expiry. Harga cenderung tertarik ke sana menjelang expiry besar |

**Valuasi on-chain (Coin Metrics)** — konteks jangka panjang yang tidak terlihat di grafik harga.

| Metrik | Artinya |
|---|---|
| MVRV | Kapitalisasi dibagi realized cap. Historisnya > 3,5 zona euforia, < 1 harga di bawah biaya perolehan |
| Realized cap | Nilai seluruh koin dihargai saat terakhir berpindah — "biaya perolehan" agregat jaringan |
| NVT | Kapitalisasi dibagi nilai transaksi. Analog rasio P/E |
| Alamat aktif | Proksi permintaan nyata, bukan spekulasi derivatif |
| Pasokan diam >1thn | Porsi pasokan yang tidak bergerak setahun. Naik = akumulasi pemegang jangka panjang |

**Aliran dana**

| Metrik | Artinya |
|---|---|
| Premium Coinbase | Selisih harga Coinbase terhadap pasar global. Positif = permintaan AS lebih agresif, sering mendahului arus institusional |
| Pasokan stablecoin | Total USDT + USDC — "amunisi" yang menunggu dibelanjakan. Naik = likuiditas masuk ekosistem |

Semua angka ini dihitung kode dari data mentah — max pain misalnya dihitung dengan menjumlahkan pembayaran penulis opsi di tiap strike, bukan diambil dari ringkasan pihak lain. Semuanya masuk ke konteks LLM, jadi narasi dan outlook menganalisanya, bukan sekadar menampilkannya.

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

### Tautan tambahan

Tombol di kartu harga diatur lewat `tautan_luar` di `config.yaml` — label, URL, dan nama ikon [Lucide](https://lucide.dev/icons):

```yaml
tautan_luar:
  - label: Arena Pertempuran BTC
    url: https://tigorworks.github.io/crypto-battlefield/
    ikon: swords
```

Kosongkan daftarnya kalau tidak ingin ada tombol.

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
2. Isi saldo secukupnya. `max_cost_usd_per_run: 0.60` adalah **plafon**, bukan tarif tetap — satu run terukur ~$0,26 (lihat tabel di langkah 4). Langkah yang kena batas dilewati dan brief tetap terbit.

### 4. Model LLM di `config.yaml`

Kesembilan step sudah terisi model yang wajar sebagai titik awal, jadi bisa langsung jalan tanpa diubah:

| Step | Model utama | Cadangan | Alasan |
|---|---|---|---|
| `filter` | `deepseek/deepseek-v3.2` | `anthropic/claude-haiku-4.5` | murah, tugasnya cuma skor 0–100 |
| `classify` | `deepseek/deepseek-v3.2` | `anthropic/claude-haiku-4.5` | patuh JSON, keluaran pendek |
| `format` | `deepseek/deepseek-v3.2` | `anthropic/claude-haiku-4.5` | menata tampilan pesan Telegram |
| `mechanism` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | butuh penalaran sebab-akibat |
| `statements` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | menyaring pernyataan dari derau |
| `technical` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menafsirkan indikator lintas timeframe |
| `whale` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | membaca divergensi posisi |
| `synthesis` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menulis analisa panjang |
| `outlook` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menggabungkan banyak sumber |
| `critic` | `openai/gpt-5.1` | `google/gemini-3.1-flash-lite-preview` | **beda keluarga** dari `synthesis` |

Satu run terukur di produksi sekitar **$0,26**, di bawah plafon `max_cost_usd_per_run: 0.60`. Satu run per hari berarti sekitar **$8 per bulan**.

Plafonnya diberi ruang lebih karena kalau critic menemukan masalah, sistem menjalankan satu putaran revisi. Tanpa ruang itu revisi akan terpotong budget dan analisanya hilang sama sekali.

**Aturan keluarga model dijaga saat runtime.** Config boleh mendaftarkan cadangan yang bertumpang tindih — misalnya `synthesis` jatuh ke `openai/gpt-5.1` sementara `critic` juga OpenAI. Sebelum critic dijalankan, kode memeriksa model mana yang BENAR-BENAR melayani synthesis, lalu menyaring pilihan critic agar tetap beda keluarga.

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
| `TELEGRAM_SUBSCRIBER_KEY` | Tidak — hanya kalau ingin fitur pelanggan `/start` |

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

## Tampilan Pesan Telegram

Pesan dirakit kode, lalu ditata ulang oleh LLM murah supaya enak dibaca — harga dan analisa AI ditonjolkan, emoji ditambahkan, alurnya dirapikan. Dimatikan lewat `telegram.rapikan_dengan_llm: false`.

**Perapi tidak dipercaya begitu saja.** Memberi LLM kebebasan menulis ulang pesan berarti membuka jalan bagi angka karangan lewat pintu belakang — persis hal yang dijaga ketat oleh critic. Karena itu hasilnya diperiksa kode sebelum dikirim:

| Pemeriksaan | Kalau gagal |
|---|---|
| Setiap angka di hasil harus sudah ada di pesan asli | tolak |
| Hanya tag HTML yang didukung Telegram (`<b> <i> <u> <s> <code> <a>`) | tolak |
| Penanda `ANALISA AI` dan disclaimer masih ada | tolak |
| Panjang di bawah 4096 karakter | tolak |

Ditolak berarti pesan asli yang dikirim. Tampilan yang kurang cantik jauh lebih baik daripada angka yang salah.

Perapi boleh memformat ulang gaya penulisan angka (`63.042` ↔ `63,042`) karena angkanya dinormalkan dulu sebelum dibandingkan — yang dilarang adalah memunculkan angka yang tidak ada.

Pesan dasarnya dirender dengan batas 3400 karakter saat perapi aktif, menyisakan ruang untuk emoji dan jeda baris. Tanpa ruang itu hasil rapinya selalu melewati batas dan selalu ditolak.

---

## Mengirim ke Banyak Penerima

Ada dua cara, bisa dipakai bersamaan.

### Cara 1: daftar tetap

`TELEGRAM_CHAT_ID` menerima beberapa ID sekaligus, dipisah koma:

```
123311673,-1001234567890,987654321
```

Cocok untuk beberapa penerima yang jarang berubah, termasuk grup (ID grup diawali tanda minus).

### Cara 2: pelanggan lewat /start

Siapa pun bisa mengirim `/start` ke bot untuk berlangganan, dan `/stop` untuk berhenti. Tiap run membaca perintah baru, mengirim sapaan ke pendaftar, lalu memasukkan mereka ke daftar kirim.

Butuh satu secret tambahan: **`TELEGRAM_SUBSCRIBER_KEY`** — isi bebas, misalnya hasil `openssl rand -base64 32`.

**Kenapa ada kunci itu:** chat ID Telegram adalah identitas personal yang tetap. Repo ini kemungkinan publik, jadi daftar pelanggan disimpan **terenkripsi** di `state/subscribers.enc`. Kalau secret tidak diisi, fitur pelanggan **dimatikan** — bukan diturunkan diam-diam jadi teks biasa, karena kebocoran semacam itu baru ketahuan setelah terlambat.

Jangan mengganti kunci setelah ada pelanggan terdaftar; kunci lama tidak bisa dipulihkan dan daftarnya harus dibangun ulang.

Beberapa hal yang sudah ditangani:

- Telegram menyimpan update yang belum diambil selama **24 jam**, jadi selama brief jalan minimal sekali sehari tidak ada pendaftaran yang terlewat.
- Penerima yang **memblokir bot** otomatis dikeluarkan dari daftar. Error sementara seperti Bad Gateway **tidak** mengeluarkan siapa pun.
- Ada jeda antar pesan supaya tidak menabrak batas ~30 pesan/detik Telegram.

### Alternatif paling sederhana: channel

Kalau tujuannya menyiarkan ke banyak orang, **channel Telegram** sering lebih praktis: buat channel, jadikan bot sebagai admin, lalu isi `TELEGRAM_CHAT_ID` dengan ID channel. Telegram sendiri yang mengurus daftar anggota — tidak ada chat ID yang perlu disimpan, tidak ada batas jumlah, dan tidak ada state sama sekali.

---

## Jadwal

Workflow berjalan sekali sehari:

| Cron (UTC) | Waktu WIB |
|---|---|
| `0 23 * * *` | 06:00 |

**Kenapa 06:00 WIB dan bukan 07:00?** Candle harian Binance berganti tepat pukul 00:00 UTC — yang persis sama dengan 07:00 WIB. Menjalankan brief di jam itu membuat candle 1D yang sedang berjalan baru berumur nol menit: volumenya nyaris nol, VWAP harian tidak bermakna, dan rasio "volume vs rata-rata" jadi menyesatkan. Pada 23:00 UTC candle sudah terisi 23 dari 24 jam, sesi saham AS sudah tutup dan tercerna, dan arus ETF harian sudah terbit.

Kalau tetap ingin 07:00 WIB, ganti ke `0 0 * * *` — sadari saja angka volume harian pada run itu tidak bisa dipercaya.

Catatan penting:

- **Cron GitHub Actions memakai UTC.** WIB = UTC+7, jadi kurangi 7 jam dari waktu WIB yang diinginkan.
- **Jadwal bisa telat 5–30 menit** saat GitHub sedang sibuk. Ini normal dan di luar kendali repo.
- **Workflow otomatis nonaktif setelah 60 hari** repo tanpa aktivitas. GitHub mengirim email peringatan sebelum itu; cukup buka Actions dan aktifkan lagi.

---

## Struktur

```
src/
├── main.py                 # orkestrator 21 langkah
├── config.py               # baca env + config.yaml
├── collectors/
│   ├── binance.py          # harga, klines, funding, OI (+ fallback CoinGecko)
│   ├── market.py           # fear & greed, on-chain, arus ETF
│   ├── macro.py            # yfinance: DXY, yield, minyak, indeks (+ FRED opsional)
│   ├── news.py             # RSS + dedup + skor prioritas
│   ├── whale.py            # posisi top trader vs ritel, aliran taker
│   ├── bybit.py            # cadangan derivatif saat Binance memblokir
│   ├── options.py          # opsi Deribit: DVOL, put/call, skew, max pain
│   ├── onchain.py          # valuasi on-chain: MVRV, NVT, alamat aktif
│   ├── flows.py            # premium Coinbase, pasokan stablecoin
│   ├── statements.py       # pernyataan tokoh berpengaruh
│   └── calendar.py         # agenda ekonomi 7 hari
├── analysis/
│   ├── technical.py        # indikator + deteksi sinyal palsu — murni kode
│   ├── llm.py              # klien OpenRouter + budget + logging biaya
│   └── news_analysis.py    # rangkaian 9 panggilan LLM
├── output/
│   ├── builder.py          # susun brief.json, diff, arsip
│   └── telegram.py         # render + kirim
├── output/
│   └── subscribers.py      # daftar pelanggan bot (terenkripsi)
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
| Funding/OI kosong | Binance (451) dan Bybit (CloudFront) sama-sama memblokir IP runner AS. Deribit dipakai sebagai lapis ketiga. |
| Analisa bertanda "belum terverifikasi" | Critic gagal dijalankan pada run itu. Analisanya tetap dikirim supaya tidak hilang, tapi statusnya ditandai terus terang di web dan Telegram. |
| Riwayat OI kosong | Tidak ada bursa yang menyediakannya dari IP runner. Perubahan OI diturunkan dari brief sebelumnya sebagai gantinya. |
| Sebagian metrik on-chain hilang | Tier gratis Coin Metrics tidak menyediakan semua metrik. Metrik yang ditolak dibuang otomatis lalu permintaan diulang — sisanya tetap didapat. |
| Kartu opsi/valuasi/aliran kosong | Deribit, Coin Metrics, atau Coinbase sedang tidak terjangkau. Semuanya opsional — brief tetap terbit, dan sumber yang gagal tercatat di `failed_sources`. |
| Bagian pernyataan kosong | Wajar kalau memang tidak ada pernyataan relevan dalam 48 jam. Kalau selalu kosong, cek `sumber_gagal` di log — Truth Social memang sering memblokir IP data center. |
| Log penuh "HTTP 451 restricted location" | Normal di GitHub Actions. Binance menolak IP runner yang berbasis AS — pembatasan wilayah permanen. Harga otomatis pindah ke CoinGecko, funding/OI ke Bybit. |
| Divergensi whale kosong | Pemisahan "top trader" vs "seluruh akun" hanya ada di Binance. Saat Binance terblokir, hanya sisi ritel yang pulih lewat Bybit, jadi divergensi memang tidak bisa dihitung. |
| Step AI gagal dengan "terpotong di batas max_tokens" | Naikkan `max_tokens` step tersebut di `src/analysis/news_analysis.py`. Balasan yang terpotong ditolak sengaja, karena JSON separuh jadi lebih berbahaya daripada tidak ada hasil. |
| Brief terbit tanpa bagian AI | `OPENROUTER_API_KEY` kosong, nama model masih placeholder, atau budget per run tercapai. Cek `data_quality.catatan`. |
| Telegram tidak masuk | Pastikan sudah mengirim pesan pertama ke bot, dan `TELEGRAM_CHAT_ID` benar (ID grup diawali minus). |
| Halaman Pages kosong | GitHub Pages belum diarahkan ke folder `/docs`, atau `latest.json` belum pernah dibuat. |
| Workflow berhenti jalan sendiri | Repo tidak aktif 60 hari. Aktifkan kembali di tab Actions. |

---

## Disclaimer

Konten yang dihasilkan bersifat informasional dan **bukan saran investasi**. Bagian yang ditandai `✦ AI` dihasilkan model bahasa dan dapat mengandung kesalahan.
