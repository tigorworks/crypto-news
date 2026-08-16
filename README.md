# Ringkasan Pasar Bitcoin

Cronjob harian yang mengambil data pasar Bitcoin, menganalisanya, lalu menghasilkan dua keluaran:

1. **File JSON** yang dibaca halaman web statis di GitHub Pages
2. **Pesan Telegram** berisi ringkasan padat

Seluruh teks yang dilihat pengguna berbahasa Indonesia. Sifatnya informasional — sistem sengaja tidak mengeluarkan rekomendasi beli/jual maupun target harga.

---

## Cara Kerja

```
Binance/CoinGecko ─┐   (harga, klines)
Binance/Bybit/OKX  │   (funding, OI, posisi whale vs ritel)
Deribit            │   (opsi: DVOL, skew, max pain)
Coin Metrics       │   (MVRV, NVT, alamat aktif)
Coinbase           │   (premium AS)
mempool.space      ├─→ pipeline Python ─→ docs/data/latest.json ─→ GitHub Pages
alternative.me     │        │
Farside (ETF)      │        └──────────→ Telegram
Yahoo Finance      │
RSS kripto+makro ──┤   (36 feed: media + regulator + bank sentral)
Fed/SEC/CFTC       │   (sumber primer regulasi AS)
US Treasury        │
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
- **Pembacaan teknikal** — kondisi candle harian: struktur & tren, momentum & volume, di mana indikator saling menguatkan, di mana saling bertentangan, dan apa yang membatalkan pembacaannya.
- **Sinyal palsu & pemain besar** — divergensi posisi top trader versus ritel, ditambah pola candle yang sering menandai pergerakan tidak tulus.
- **Pernyataan tokoh berpengaruh** — ucapan pejabat dan tokoh yang berpotensi menggerakkan pasar, lengkap dengan status keasliannya.
- **Makro & geopolitik** — DXY, yield UST 10Y, minyak, emas, VIX, dan **USD/JPY** ditelusuri rantai transmisinya ke BTC (dolar menguat menekan aset berisiko; yen menguat tajam mengindikasikan pelepasan carry trade dolar-yen). Ini bukan bagian terpisah — prompt sintesis dan outlook diwajibkan menimbang data makro ini, bukan cuma berita dan harga.
- **Pandangan ke depan** — skenario menguat/melemah beserta pemicunya, keputusan besar yang dipantau (FOMC, rilis data ekonomi dari `agenda_mendatang`), dan risiko utama.
- **Geopolitik & regulasi** — ditempatkan **paling atas** di bagian analisa AI, ditulis sebagai **paragraf naratif** (`ai.outlook.narasi_geopolitik`), bukan daftar potongan. Prompt mewajibkan rantai transmisinya ditelusuri sampai ke harga BTC: bukan "ada pertemuan Gedung Putih soal kripto", tapi "pertemuan itu berpotensi menurunkan premi risiko regulasi yang ditanggung institusi AS, jalur yang sama yang menggerakkan arus ETF". Daftar `faktor_geopolitik` tetap ada sebagai butir penopang, bukan pengulangan narasinya. Di bawahnya, web mencantumkan **tautan sumber** yang dipilih kode dari berita berkategori regulasi/geopolitik/makro dan diurutkan berdasarkan tier kredibilitas — jadi pembaca bisa memverifikasi sendiri, bukan sekadar percaya pada AI. Atribusinya berasal dari data, bukan dari model.

**Urutan tampilan** — web: **geopolitik & regulasi** → narasi utama → penyebab pergerakan → pandangan ke depan → pembacaan teknikal → whale. Telegram: narasi → penyebab → pandangan ke depan (termasuk geopolitik) → teknikal → whale. Agenda ditempatkan sebelum bagian berita & pernyataan di kedua tempat: apa yang akan terjadi lebih menentukan posisi pembaca daripada apa yang sudah diberitakan.

**Tanpa nama field yang bocor.** Konteks yang dilihat LLM berbentuk JSON, jadi model kerap menyalin nama field dan nilai enum apa adanya ke dalam narasi — pembaca disuguhi `"pola short_covering"`, `"invalidasi_turun di $64.314"`, `"buy_sell_ratio taker 1,785"`. Prompt melarangnya, tapi larangan saja tidak cukup: `src/utils/istilah.py` menggantinya lagi lewat **kode** setelah LLM selesai menulis (`short_covering` → "penutupan posisi short", `invalidasi_turun` → "batas pembatalan skenario turun"), plus aturan generik yang mengubah garis bawah jadi spasi untuk field yang belum masuk kamus. Deterministik, tidak bergantung kepatuhan model, dan tidak pernah menyentuh angka.

**Satu peristiwa dibahas sekali.** Tiap bagian punya tugas berbeda — `penyebab` menjelaskan apa yang menggerakkan harga, `data_pendukung` menyuplai angkanya (bukan menceritakan ulang peristiwanya), `yang_diwaspadai` mengangkat yang belum tercermin di harga. Prompt melarang menceritakan ulang berita yang sama antar bagian; kalau sudah dibahas, cukup dirujuk singkat.

### Kenapa hanya candle harian

Brief ini terbit sekali sehari, jadi timeframe analisanya **1D saja**. 4H dan 1H sudah dibuang, dan itu bukan sekadar penyederhanaan tampilan:

- Pada laporan harian, sinyal 1H sudah basi sebelum pembacanya bangun.
- Tiga timeframe sekaligus membuat model menulis angka dari timeframe yang berbeda dalam satu kalimat, lalu pemeriksa fakta mencocokkannya dengan timeframe yang salah dan memvonisnya karangan. Ini benar-benar terjadi di produksi: EMA20 1H dituduh karangan karena dibandingkan dengan EMA20 4H.

Candle 1H tetap diambil, tapi untuk satu keperluan saja: mengukur reaksi harga satu jam setelah sebuah berita terbit. Candle itu tidak pernah dianalisa maupun ditampilkan sebagai timeframe. Atur lewat `timeframes` dan `timeframe_reaksi` di `config.yaml`.

### Sumber berita dinamis

Selain 36 feed tetap di `config.yaml`, tiap run menambahkan feed hasil **riset**: model murah (step `riset`) diminta mengusulkan beberapa kueri pencarian berdasarkan kondisi hari ini — harga, tema laporan sebelumnya, pergeseran narasi — lalu **kode** yang mengambil artikelnya lewat Google News RSS.

Pembagian tugas itu disengaja dan penting: **model tidak pernah menghasilkan berita, judul, atau URL.** Ia cuma menyarankan apa yang layak dicari. Seluruh artikel yang masuk tetap berasal dari feed sungguhan dan melewati jalur yang sama persis dengan feed tetap — penyaringan umur, dedup, skor prioritas, filter relevansi, lalu critic. Jadi tidak ada celah bagi model untuk mengarang sumber.

Kueri yang diusulkan dicatat di `data_quality.catatan` supaya terlihat apa yang diriset hari itu. Matikan lewat `news.riset_dinamis: false` kalau ingin sumbernya benar-benar tetap.

### Penyaringan relevansi

Dengan 36 feed tetap plus feed riset, satu run bisa menarik ratusan artikel. Tiga hal menjaga agar yang lolos ke brief tetap sedikit dan beragam:

- **Dinilai per batch (60 artikel/panggilan).** Sebelumnya seluruh artikel dinilai dalam satu panggilan; pada 132 artikel keluarannya sudah 3.027 token dan mendekati batas. Sekali terpotong, SELURUH penilaian hilang dan brief jatuh ke fallback kata kunci. Dengan batch, satu batch yang gagal tidak menjatuhkan yang lain.
- **Kredibilitas sumber jadi pemecah imbang.** Pada relevansi setara, sumber tier 1 menang (tier 1 ×1,30 … tier 3 ×1,00). Sengaja ringan, bukan bobot penuh `1,0/0,7/0,4` yang dipakai skor sentimen: berita Bitcoin paling banyak datang dari media kripto yang kebanyakan tier 2–3, dan bobot penuh akan membuat berita makro tier 1 yang cuma menyerempet BTC menggusur berita kripto yang justru jadi pokok laporan.
- **Diisi bergiliran per outlet.** Ronde pertama mengambil artikel terbaik dari tiap domain, ronde kedua yang terbaik kedua, dan seterusnya. Satu outlet yang rajin menerbitkan (Blockworks pernah 50 artikel dalam satu tarikan) tidak bisa memborong kuota semata-mata karena jumlahnya banyak. Cara ini juga tidak pernah menyisakan slot kosong kalau kandidatnya memang terkonsentrasi di sedikit outlet.

### Postingan X (Twitter) lewat Grok

Pernyataan Trump di X diambil lewat Grok (step `x_posts`), karena hanya xAI yang punya pencarian langsung ke X — API gratis X sendiri sudah lama tidak mengizinkan pembacaan timeline.

**Ini titik paling rawan di seluruh proyek**, dan diperlakukan begitu. Di semua tempat lain, aturannya "model tidak pernah menghasilkan fakta atau URL". Di sini kita justru meminta sebuah LLM menyebutkan apa yang diposting seseorang — dan model yang tidak tahu jawabannya cenderung mengarang jawaban yang meyakinkan. Postingan presiden AS soal kripto yang dikarang, lalu disiarkan ke Telegram sebagai intelijen pasar, jauh lebih buruk daripada tidak punya data X sama sekali.

Dua lapis pengaman, dan **lapis kedua yang benar-benar dipegang**:

1. **Pencarian langsung.** Permintaan menyertakan `search_parameters` (Live Search xAI) supaya Grok menjawab dari hasil pencarian X sungguhan. Ini menaikkan peluang jawabannya berdasar — tapi tidak bisa diverifikasi dari sisi kita: kalau OpenRouter tidak meneruskan parameternya, model diam-diam kembali menjawab dari ingatan.
2. **Verifikasi kode per item.** Tiap item wajib punya URL status X berbentuk sah (`x.com/<akun>/status/<id>`), dengan **akun yang cocok** dengan yang diminta, teks tidak kosong, dan waktu di dalam jangkauan (termasuk menolak tanggal masa depan — tanda kuat timestamp karangan). Yang tidak lolos dibuang, tidak peduli seberapa meyakinkan teksnya.

**Yang jujur perlu diakui:** verifikasi bentuk URL membuktikan formatnya benar, **bukan** bahwa postingannya ada. Model yang mengarang URL berformat rapi tetap bisa lolos lapis ini. Karena itu:

- Item dari sini ditandai `jenis_sumber: "x_grok"` dan **tidak pernah** diperlakukan sebagai sumber primer.
- Langkah LLM pernyataan tetap wajib menilai `status` (verbatim / dilaporkan media / rumor), dan diberi tahu bahwa sumber ini belum terverifikasi.
- `terkonfirmasi_media` menandai apakah isinya juga muncul di kandidat dari sumber lain — satu-satunya verifikasi ISI yang bisa dilakukan kode.
- Semuanya tetap lewat pintu yang sama: saringan umur, dedup, analisa LLM, critic. Tidak ada jalur pintas.

Matikan lewat `statements.x_grok.aktif: false` kalau ingin brief bersandar sepenuhnya pada sumber yang bisa diverifikasi kode.

### Berita berbahasa Indonesia

Feed sumbernya berbahasa Inggris. Judul dan ringkasan diterjemahkan pada langkah klasifikasi yang memang sudah membaca tiap artikel — jadi tidak ada panggilan LLM tambahan, dan biayanya nyaris tidak berubah. Judul aslinya tetap ditampilkan kecil di bawah terjemahan, karena artikel yang dibuka tetap berbahasa Inggris dan pembaca perlu bisa mencocokkannya.

Nama diri, nama lembaga, dan istilah pasar yang memang dipakai apa adanya di Indonesia (Bitcoin, ETF, Fed, SEC, futures) tidak ikut diterjemahkan.

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

Critic memeriksa seluruh bagian naratif terhadap data mentah yang **persis sama** dengan yang dipakai penulisnya. Yang membedakan: **tidak semua temuan diperlakukan sama.**

| Jenis temuan | Akibatnya |
|---|---|
| `angka_karangan` — angka yang tidak ada di data | **Bagian itu ditahan** |
| `pengetahuan_luar` — peristiwa yang tidak ada di data | **Bagian itu ditahan** |
| `saran_investasi` — kalimat bernada anjuran tindakan | Ditandai, analisa tetap tampil |
| `sebab_akibat` — klaim sebab-akibat yang terlalu percaya diri | Ditandai, analisa tetap tampil |

Alasannya: yang berbahaya adalah **fakta yang dikarang**, bukan kalimat yang kebetulan terbaca seperti anjuran. Brief ini dibaca pemiliknya sendiri yang tetap memutuskan sendiri — menahan seluruh analisa karena satu kalimat menyerempet anjuran justru menghapus bagian yang paling berguna. Kalimat semacam itu diberi keterangan terbuka di web dan Telegram, lalu ditampilkan apa adanya.

Kalau ada temuan yang benar-benar menahan:

1. **Satu putaran revisi** — narasi dikirim balik beserta daftar temuan untuk diperbaiki, lalu diperiksa ulang.
2. **Setelah revisi, hanya kesalahan ANGKA yang masih boleh menahan.** Temuan non-angka yang bertahan sampai putaran kedua diturunkan jadi tanda editorial dan analisanya tetap terkirim. Ini keputusan sadar berdasarkan bukti produksi: temuan `pengetahuan_luar` yang bertahan sampai putaran kedua hampir selalu salah kategori — kalimat tafsir yang divonis fakta karangan — dan menahan SELURUH analisa (narasi + outlook + skenario sekaligus) karena satu kalimat tafsir jauh lebih merugikan pembaca. Angka karangan tetap menahan tanpa pengecualian, karena angka yang salah menyesatkan secara langsung dan kode sudah memverifikasinya sendiri.
3. **Kalau masih ada kesalahan angka, hanya bagian bermasalah yang ditahan** — bukan seluruh analisa. Pembacaan teknikal, analisa whale, dan outlook tetap terkirim kalau tidak ikut ditandai.

**Tuduhan angka karangan diperiksa ulang oleh kode.** Sebelum sebuah temuan boleh menahan apa pun, setiap angka pada kalimat yang dituduh dicocokkan dengan seluruh angka di data — dengan toleransi pembulatan, tanpa peduli pemisah ribuan, dan paham suffix skala Indonesia (`"$20,5 miliar"` dicocokkan terhadap `20497629840` di data, bukan cuma angka `20,5` mentah). Kalau semuanya ternyata ada, tuduhannya dibatalkan. Ini menutup dua kelas kesalahan yang nyata terjadi di produksi: critic memvonis `64.371,18` sebagai karangan padahal datanya memuat `64371.1839` (angka yang sama, ditulis berbeda), dan memvonis `$20,5 miliar` sebagai karangan padahal datanya memuat `20497629840` (angka yang sama, disingkat).

**Keberatan soal tafsir dibantah oleh kode, bukan cuma prompt.** Prompt sudah dua kali dipertegas, tapi pola yang sama terus muncul dengan kalimat berbeda, jadi sekarang ada pembantah di sisi kode. Dua penanda yang dipakai: (a) alasan critic memakai kata **"eksplisit"** dalam konteks negatif (*"tidak dinyatakan eksplisit di data"*) — kalau keberatannya soal EKSPLISITAS, critic sedang mengakui bahan faktanya ada dan yang kurang cuma kalimat penegasnya, itu definisi `sebab_akibat`; (b) subjek kalimat alasannya sendiri sebuah kata benda tafsir (*"Interpretasi bahwa …"*, *"Klaim historis tentang …"*, *"Keterkaitan … tidak …"*). Temuan yang cocok diturunkan otomatis jadi `sebab_akibat` minor. Alasan untuk `pengetahuan_luar` yang SUNGGUHAN berbunyi lain — *"tidak ada satu pun berita yang menyebut peristiwa X"* — dan tidak ikut terbantah.

**Batas `pengetahuan_luar` vs `sebab_akibat` diperjelas tegas.** Critic sempat memvonis kalimat seperti *"pergerakan hari ini tampak lebih terkait dengan mekanika teknikal — squeeze volatilitas, taker sell dominan"* sebagai `pengetahuan_luar` (fatal, menahan) padahal ketiga fakta yang dirujuknya (squeeze, taker ratio, short buildup) semuanya ADA di data — itu cuma model MENGHUBUNGKAN data yang ada, persis tugas seorang analis, seharusnya `sebab_akibat` (minor, tidak menahan). Prompt sekarang eksplisit: `pengetahuan_luar` hanya untuk fakta/angka/entitas yang **tidak ada di mana pun** dalam data; menafsirkan atau menghubungkan data yang sudah ada selalu `sebab_akibat`, tidak pernah fatal.

### Perbaikan analis pasar (Agustus 2026)

Serangkaian penambahan berdasar audit "apa yang bisa membuat insight-nya lebih berguna":

- **Riwayat OI lewat OKX** — cadangan KETIGA untuk riwayat open interest setelah Binance dan Bybit gagal (keduanya diblokir dari IP runner GitHub Actions). Sebelumnya satu-satunya jalan saat itu terjadi adalah membandingkan OI hari ini dengan OI di brief kemarin — valid tapi cuma satu titik pembanding sehari. OKX memberi granularitas per jam.
- **Tren funding, bukan cuma titik terakhir** — funding positif SATU KALI nyaris tidak berarti; funding yang bertahan berhari-hari di sisi yang sama (long/short crowded) adalah sinyal jauh lebih kuat. `funding_persisten_jam` menghitung berapa lama funding bertahan di tanda yang sama, lewat riwayat 7 hari OKX (interval 8 jam).
- **Volatilitas realized vs DVOL (implied)** — dihitung dari candle harian yang SUDAH ADA (log return 30 hari, dianualisasi), dibandingkan dengan DVOL Deribit. Rasio IV/RV menandakan opsi mahal (>1,15×) atau murah (<0,85×) relatif terhadap volatilitas yang SUNGGUHAN terjadi — bukan cuma angka DVOL telanjang tanpa konteks.
- **Dominasi BTC** (`market.btc_dominance_pct`, dari CoinGecko `/global`) — penanda rezim: dominance naik + harga BTC naik berarti uang mengalir KE BTC (altcoin melemah relatif); dominance turun + harga naik berarti risk-on lebih luas ke seluruh pasar kripto.
- **Field yang sudah dikumpulkan tapi tak pernah ditampilkan** kini muncul di web: rentang DVOL 7 hari (bukan cuma titik terakhir), expiry opsi dengan OI terbesar (indikasi pinning/gamma menjelang jatuh tempo), tren posisi whale vs ritel dalam periode pemantauan, rincian kapitalisasi per stablecoin, neraca The Fed dan M2 (skala makro, likuiditas dolar), serta fee mempool.
- **Bug satuan FRED yang ditemukan sekaligus dibetulkan**: WALCL (neraca Fed) dilaporkan FRED dalam JUTAAN dolar dan M2SL dalam MILIAR dolar — bukan dolar mentah. Tanpa penskalaan, `fed_balance_sheet` bernilai ~6.500.000 yang gampang salah dibaca sebagai "$6,5 juta" padahal sebenarnya $6,5 TRILIUN. Diskalakan di `src/collectors/macro.py`, di sumbernya — supaya konteks yang dibaca LLM maupun tampilan web sama-sama memakai dolar sungguhan.

### Kegagalan tidak boleh senyap

Prinsip yang dipegang di jalur parsing dan pelaporan: **gagal keras lebih baik daripada berhasil dengan data terpotong.** Analisa yang hilang sebagian tanpa peringatan lebih berbahaya daripada error yang kelihatan, karena brief yang kehilangan narasi utamanya terbaca persis seperti brief yang memang singkat hari itu.

1. **Perbaikan JSON ditolak kalau isinya menyusut.** `_isi_terjaga()` membandingkan jumlah karakter alfanumerik hasil perbaikan dengan balasan asli; di bawah 70% (80% untuk jalur pemotongan blok kurung yang paling agresif) hasilnya ditolak dan pipeline melempar error. Ini menutup skenario "JSON-nya sah tapi tiga dari empat paragraf hilang".
2. **Objek rusak tidak boleh jatuh jadi array** — objek hampir selalu memuat array di dalamnya, dan mengambilnya menghasilkan parse sukses dengan isi yang sama sekali salah.
3. **Langkah AI yang gagal dicatat dan ditampilkan.** Sebelumnya catatan hanya muncul kalau SELURUH blok AI kosong; sintesis gagal + outlook sukses menghasilkan brief tanpa narasi utama tanpa keterangan. Sekarang tiap bagian dicatat sendiri, dan `data_quality.catatan` (ditulis pipeline tapi tak pernah ditampilkan) muncul di header web serta Telegram.

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

### Agenda ekonomi: 30 hari, dugaan pola + pelengkap dari investing.com

Horizonnya **30 hari**, bukan 7. Dengan 7 hari agenda kerap kosong sama sekali — padahal FOMC, rilis CPI, dan expiry opsi bulanan justru perlu diantisipasi jauh sebelum harinya tiba.

Kalender bawaan (`calendar.py`) tidak membaca sumber luar sama sekali — FOMC diambil dari config, sisanya (CPI, NFP, PCE) dihitung dari pola tanggal rilis yang biasanya stabil tiap bulan ("Rabu ke-2", "Jumat pertama"), makanya ditandai `perkiraan: true`. Ini tahan lama tapi bisa meleset kalau BLS/BEA menggeser jadwal.

Dua sumber luar dicoba untuk mengganti dugaan itu dengan tanggal sungguhan, berurutan dari yang paling bisa diandalkan:

1. **`ff_calendar.py` — feed JSON ForexFactory** (`nfs.faireconomy.media`). Terstruktur, tanpa API key, tanpa proteksi anti-bot, dan **tanpa LLM sama sekali** — parsingnya deterministik, jadi tidak ada yang bisa dikarang. Tiga jendela (minggu ini, minggu depan, bulan ini) digabung lalu disaring: hanya USD/EUR/CNY dengan dampak High/Medium, duplikat antar-berkas dibuang.
2. **`investing.py` — scrape investing.com lewat LLM murah**, dipakai hanya kalau feed di atas kosong. Tabelnya dirender JavaScript dengan markup rumit, jadi ekstraksinya diserahkan ke model (step `agenda` di config) yang jauh lebih tahan banting dibanding regex. Halamannya kerap diblokir dari IP pusat data, jadi wajar kalau sering kembali kosong.

**Dampaknya ke kripto dinilai LLM.** Kalender cuma menghasilkan daftar mentah — CPI, expiry opsi, pidato ECB, penjualan ritel semuanya "acara ekonomi", tapi dampaknya ke BTC jauh dari seragam. Langkah `agenda_dampak` memberi tiap acara skor relevansi terhadap kripto (1-5), arah dampak (`naik`/`turun`/`dua_arah` — yang terakhir paling sering benar untuk rilis data, karena arahnya tergantung angka yang keluar), dan **jalur transmisinya** ke harga BTC. Model tidak boleh menambah atau membuang acara: pencocokannya lewat indeks yang dikirim kode, dan anotasi dengan indeks tak dikenal dibuang. Web menampilkan penilaian ini di bawah tiap agenda; Telegram menandai acara berdampak besar (≥4) dengan 🔴 dan mencantumkan jalurnya — sisanya cukup nama dan waktunya saja supaya yang penting tidak tenggelam.

Event yang dikonfirmasi **menggantikan** dugaan pola bulanan untuk kategori dan tanggal yang sama (bukan ditambahkan sebagai duplikat) — termasuk FOMC, supaya satu keputusan suku bunga tidak tampil dua kali dengan nama berbeda. Kalau dua-duanya gagal, kalender bawaan tetap menghasilkan agenda dan pipeline lanjut tanpa keluhan.

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

## Perbaikan UI/UX (Agustus 2026)

Serangkaian penambahan berdasar audit "apa yang perlu diperbaiki dari sisi UI/UX yang paham market":

- **Ringkasan (TL;DR) paling atas** — vonis satu kalimat (`ai.bagian.judul`) tampil sebagai panel indigo tepat setelah kartu harga, sebelum pembaca perlu scroll melewati lima bagian data mentah. Pembaca yang cuma punya 10 detik dapat MAKNA, bukan cuma angka.
- **"Perubahan vs Brief Sebelumnya" berdiri sendiri di bagian bawah** sebagai konteks penutup, bukan lagi berbagi baris dengan agenda.
- **Urutan bagian**: harga → TL;DR → **pembacaan teknikal** → analisa AI → pasar → opsi & valuasi → whale → agenda → berita → perubahan vs brief sebelumnya. Analisa AI naik dari urutan keenam, dan teknikal ditaruh tepat di atasnya supaya pembaca sudah memegang kondisi harga sebelum membaca tafsirannya. Nav lompat ponsel mengikuti urutan yang sama.
- **Skor sentimen diperbaiki labelnya** — skalanya -100 (bearish penuh) sampai +100 (bullish penuh), tapi label lama menulis "/100" yang gampang salah dibaca seolah skornya selalu positif, apalagi saat angkanya negatif. Sekarang eksplisit "dari -100..+100" dengan tanda `+`/`-` pada angkanya.
- **Sumber gagal ditulis terang-terangan** — sebelumnya cuma tersembunyi di tooltip badge kualitas data, nyaris tak berguna di ponsel (tanpa hover) dan gampang terlewat bahkan di desktop. Sekarang muncul sebagai baris terpisah di header dalam bahasa manusia ("Arus ETF harian", bukan `etf_flow`) setiap kali ada sumber yang gagal run itu.
- **Corong berita di footer** — "Berita terkumpul" (jumlah KOTOR yang ditarik dari seluruh feed, sebelum saringan umur) dan "Lolos saringan" (yang benar-benar dipakai di brief, plus persentasenya). Angka ini menunjukkan apakah menambah feed benar-benar menambah bahan atau cuma menambah derau yang tetap dibuang di langkah filter.
- **Perbandingan dua arsip** — di bagian Arsip, pilih satu arsip lagi untuk dibandingkan dengan yang sedang tampil. Tabel ringkas menampilkan harga, perubahan 24 jam, sentimen, funding, open interest, DVOL, dominasi BTC, dan MVRV berdampingan. Dimuat terpisah dari `data` supaya tidak mengganggu tampilan utama maupun grafik.
- **Grid menyesuaikan jumlah kartu yang benar-benar dirender.** Kelas kolomnya dulu dipatok (`lg:grid-cols-2` untuk whale, `lg:grid-cols-3` untuk data institusional), jadi ketika kartu keduanya tidak ada — sinyal palsu kosong, atau data on-chain gagal diambil — separuh sampai dua pertiga baris tampil melompong. Sekarang jumlah kolom dihitung dari kartu yang ada (`kelasGridWhale`, `kelasGridInstitusional`).
- **Support/resistance digambar di grafik harga** — sebelumnya cuma angka telanjang di kartu sebelah; sekarang garis putus-putus hijau (support) dan merah (resistance) langsung di atas grafik, jadi "harga lagi di mana relatif ke level" terlihat sekali pandang tanpa mencocokkan dua angka secara mental.

## Tampilan Mobile

Halaman dirancang mobile-first dan diuji di lebar 360px, 390px, dan 430px:

- **Tanpa gulir horizontal** di semua lebar tersebut
- **Target sentuh minimal 44px** pada perangkat sentuh, sesuai pedoman iOS dan Android
- **Ukuran teks minimal 11px** di ponsel; ukuran yang lebih kecil hanya dipakai mulai breakpoint `sm`
- **Nav lompat** khusus ponsel di bawah header — halaman ini panjang, jadi ada baris pintasan yang bisa digulir ke samping menuju tiap bagian
- **Daftar panjang dipaginasi** — berita dan pernyataan tokoh 3 baris per halaman, agenda 5 baris per halaman (horizonnya 30 hari, jadi daftarnya lebih panjang). Berita dan pernyataan berbagi satu bagian dengan dua tab, jadi halaman tidak memanjang dan bagian di bawahnya tetap terjangkau
- **Posisi scroll terjaga saat ganti halaman** — tombol Sebelumnya/Berikutnya dikunci di posisi layar yang sama sebelum dan sesudah diklik (lihat `_pindahHalamanTerjaga` di `docs/app.js`), supaya jari yang baru menekan tidak kehilangan tombolnya saat tinggi daftar berubah
- Tabel indikator dan grid makro menyusun ulang jadi satu kolom

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
| `filter` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | murah, tugasnya cuma skor 0–100 |
| `classify` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | patuh JSON, keluaran pendek |
| `format` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | menata tampilan pesan Telegram |
| `riset` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | mengusulkan kueri pencarian berita |
| `agenda` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | ekstraksi teks kalender jadi JSON |
| `agenda_dampak` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | menilai dampak agenda ke kripto |
| `mechanism` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | keterangan pendek per berita |
| `statements` | `anthropic/claude-haiku-4.5` | `deepseek/deepseek-v3.2` | menyaring pernyataan dari derau |
| `x_posts` | `x-ai/grok-4` | `x-ai/grok-3` | **wajib Grok** — cuma xAI yang bisa mencari di X |
| `technical` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menafsirkan indikator candle harian |
| `whale` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | membaca divergensi posisi |
| `synthesis` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menulis analisa panjang |
| `outlook` | `anthropic/claude-sonnet-5` | `openai/gpt-5.1` | menggabungkan banyak sumber |
| `critic` | `openai/gpt-5.1` | `google/gemini-3.1-flash-lite-preview` | **beda keluarga** dari `synthesis` |

**Langkah pengambilan & penyiapan data memakai Haiku, dengan DeepSeek sebagai cadangan.** Langkah-langkah itu cuma mengubah data jadi data — memberi skor, mengklasifikasi, mengekstrak jadwal — dan tidak satupun menulis prosa yang dibaca pengguna, jadi model kecil sudah cukup. Yang memakai model kuat hanya langkah yang MENULIS analisa (`technical`, `whale`, `synthesis`, `outlook`) dan `critic` yang memeriksanya.

Blok ini sempat dipindah ke DeepSeek demi hemat, lalu dikembalikan ke Haiku karena kepatuhan formatnya lebih bisa diandalkan: DeepSeek pernah menulis skala 1–5 sebagai angka Romawi (`"kekuatan": III`) dan menggugurkan satu batch pernyataan utuh. DeepSeek tetap terpasang sebagai cadangan supaya satu vendor bermasalah tidak mematikan langkahnya.

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

Siapa pun bisa mengirim `/start` ke bot untuk berlangganan, dan `/stop` untuk berhenti. Halaman web menyediakan **tombol Berlangganan** (di header dan footer) yang membuka bot lewat `https://t.me/<bot>?start=web` — Telegram menampilkan tombol MULAI yang langsung mengirim `/start`, jadi pembaca tidak perlu mengetiknya. Atur nama botnya di `config.yaml` bagian `telegram.bot_username`; kosongkan kalau tombolnya tidak ingin ditampilkan. Tiap run membaca perintah baru, mengirim sapaan ke pendaftar, lalu memasukkan mereka ke daftar kirim.

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
│   ├── okx.py              # cadangan posisi whale/ritel + taker (pemisahan utuh)
│   ├── bybit.py            # cadangan derivatif saat Binance memblokir
│   ├── options.py          # opsi Deribit: DVOL, put/call, skew, max pain
│   ├── onchain.py          # valuasi on-chain: MVRV, NVT, alamat aktif
│   ├── flows.py            # premium Coinbase, pasokan stablecoin
│   ├── statements.py       # pernyataan tokoh berpengaruh
│   ├── calendar.py         # agenda ekonomi 30 hari (dugaan pola bulanan)
│   ├── ff_calendar.py      # agenda sungguhan: feed JSON ForexFactory
│   └── investing.py        # cadangan agenda: scrape investing.com via LLM
├── analysis/
│   ├── technical.py        # indikator + deteksi sinyal palsu — murni kode
│   ├── llm.py              # klien OpenRouter + budget + logging biaya
│   ├── riset.py            # LLM mengusulkan kueri berita, kode yang mengambil
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
- **Skor kualitas data dibobot menurut kepentingan.** 13 sumber dipantau, tapi tidak setara: harga dan teknikal berbobot 3, pembentuk analisa berbobot 2, pelengkap berbobot 1. Tanpa pembobotan, kehilangan dua sumber pinggiran sudah cukup melabeli seluruh brief "sedang" padahal seluruh data intinya utuh — label yang menyesatkan ke arah yang salah. Jumlah sumber yang berhasil tetap dilaporkan apa adanya di `sources_ok`; yang dibobot hanya labelnya. Footer web juga menampilkan **jumlah token** yang dihabiskan dan **lama proses** run — biayanya sengaja tidak ikut ditampilkan (angkanya tidak berarti bagi pembaca, dan tetap tersimpan di `data_quality.llm_cost_usd`). Skor kualitas tampil di web dan tersimpan di `latest.json`, tapi **tidak dikirim ke Telegram** — itu metrik kesehatan pipeline, bukan informasi pasar, dan pembaca tidak bisa berbuat apa-apa dengannya.
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
- Kalau critic menemukan angka karangan, bagian yang bersangkutan sengaja **tidak** diberi banner atau penjelasan teknis ke pembaca — brief ini untuk pemakaian pribadi, dan membeberkan istilah internal critic (`angka_karangan`, dst) cuma mengganggu tanpa berguna. Bagian yang lolos tetap tampil; kalau semua bagian tertahan, muncul pesan generik "Analisa AI tidak tersedia pada run ini" — sama seperti run yang memang tidak menjalankan LLM sama sekali.
- Kalau critic cuma menandai kalimat bernada anjuran, analisanya tetap tampil disertai keterangan terbuka — di web sebagai panel yang bisa dibuka berisi kalimat yang ditandai, di Telegram sebagai satu baris catatan. Ini beda dari poin di atas: di sini isinya tetap ditampilkan, cuma diberi konteks.
- Nama model LLM tidak ditampilkan di halaman. Yang perlu diketahui pembaca adalah bagian mana yang dihasilkan AI, bukan model mana yang menuliskannya.

---

## Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| Analisa AI di Telegram terasa sangat pendek, cuma bahas teknikal | Critic menahan `narasi` dan/atau `outlook` (cek `ai.bagian_ditahan` di `latest.json`) — bagian yang tersisa (teknikal/whale) memang tampil sendirian, tanpa penjelasan ke pembaca (disengaja, lihat bagian Pelabelan AI). Cek `ai.critic.corrections` untuk tahu alasannya. Kalau `pengetahuan_luar` dipakai untuk kalimat yang sebenarnya cuma menafsirkan data yang ada, itu prompt critic yang perlu diperjelas lagi — lihat bagian "Kalau critic menemukan masalah". |
| Critic menahan narasi karena "volume 24 jam" dianggap karangan | Ada dua angka volume yang berbeda: `harga.volume_24h` (rolling 24 jam sungguhan) vs `teknikal_1d.volume.terakhir`/`.rata_20` (volume per candle harian, bisa jauh berbeda karena batas UTC candle). Prompt sudah diperjelas soal ini; kalau masih terjadi, cek apakah model yang dipakai benar-benar mengikuti instruksi sistemnya. |
| Judul "Ringkasan Pasar Bitcoin" hilang dari pesan Telegram yang dirapikan | Sudah diperbaiki — judul dan timestamp sekarang selalu ditambahkan oleh KODE setelah perapian, tidak pernah dikirim ke LLM sama sekali (`telegram.render_terpisah()`). Kalau masih hilang, berarti bukan dari jalur ini. |
| Analisa AI di Telegram terasa lebih singkat dari biasanya | Kalau itu hasil rapian LLM, verifikasinya sekarang menolak hasil yang panjangnya kurang dari 60% pesan asli (`stylist.RASIO_PANJANG_MINIMAL`) — perapi cuma boleh menata, bukan meringkas. Hasil yang ditolak otomatis jatuh ke pesan asli (lebih panjang, tidak dirapikan). |
| Agenda cuma berisi FOMC + tanggal "perkiraan" | Kedua sumber luar (feed ForexFactory dan investing.com) tidak terjangkau, jadi kalender bawaan (dugaan pola bulanan) yang dipakai. Cek log untuk "Kalender ekonomi ForexFactory tidak terjangkau". |
| Pesan Telegram terasa berhenti di tengah kalimat | Kalau itu hasil rapian LLM (`rapikan_dengan_llm: true`), gerbang verifikasinya sekarang memastikan disclaimer benar-benar ada di ~300 karakter terakhir — balasan yang terpotong otomatis ditolak dan pesan asli (utuh) yang dikirim. Kalau tetap terlihat terpotong, cek dulu apakah itu cuma potongan tangkapan layar (scroll ke bawah) sebelum melapor sebagai bug. |
| Narasi menyebut jangka waktu yang salah ("turun -10,43% dalam **30 jam**") | Nama field yang ambigu ikut masuk konteks LLM. Metrik on-chain dulu bernama `_perubahan_30h_pct` di mana "h" berarti *hari*, tapi model membacanya sebagai *hours*. Sudah diganti jadi `_perubahan_30hari_pct`. Kalau menambah field berjangka waktu baru, tulis satuannya utuh — jangan disingkat. |
| Log `Balasan yang gagal diparse` dengan `Invalid control character` | Model menulis pemisah paragraf sebagai baris baru SUNGGUHAN di dalam nilai string, padahal JSON mewajibkan `\n` yang di-escape. Satu saja menggugurkan seluruh balasan. `_escape_kontrol_dalam_string()` di `src/analysis/llm.py` memindai per karakter lalu meng-escape-nya; aturan 6 di `ATURAN_DASAR` juga melarangnya di sisi prompt. |
| Log `Balasan yang gagal diparse` pada balasan panjang (sintesis/outlook) | Dua kerusakan yang paling sering: kutip di dalam nilai string yang lupa di-escape (`label tren "jual menguat"`) dan string yang lupa ditutup di ujung. Keduanya lolos dari penyeimbang kutip/kurung — yang pertama jumlah kutipnya genap, yang kedua menelan sisa dokumen. `_rapikan_string_baris()` di `src/analysis/llm.py` merapikan keduanya per baris. Kalau muncul bentuk kerusakan lain, tambahkan di situ. |
| Log `Balasan yang gagal diparse` berisi `"kekuatan": III` | Model menulis skala 1-5 sebagai angka Romawi, yang bukan JSON valid — satu kemunculan menggugurkan seluruh batch. Terjadi di produksi pada langkah `statements` setelah pindah ke DeepSeek. `_perbaiki_json()` sekarang menerjemahkan angka Romawi telanjang (I..V) yang berdiri sebagai nilai, dan prompt-nya juga sudah diperjelas. Kalau muncul di luar jangkauan I..V, perluas `_POLA_ROMAWI_NILAI` di `src/analysis/llm.py`. |
| Log `Balasan step 'revisi' terpotong di batas max_tokens` | Step revisi menulis ulang SELURUH narasi (bukan cuma bagian yang salah), jadi butuh ruang sebanyak sintesis sendiri. Kalau masih terpotong meski sudah dinaikkan, naikkan lagi `max_tokens` di `revisi_narasi()` (`src/analysis/news_analysis.py`). |
| Log berhenti di `BERHENTI: data harga tidak tersedia` | Binance dan CoinGecko sama-sama tidak bisa diakses. Biasanya sementara; cek lagi run berikutnya. |
| `failed_sources` memuat `etf_flow` | Farside (di belakang Cloudflare) menolak atau strukturnya berubah. Kalau brief sebelumnya punya angka ETF, angka itu dipakai ulang lengkap dengan tanggal aslinya dan ditandai `etf_flow_kedaluwarsa`. |
| Funding/OI kosong | Binance (451) dan Bybit (CloudFront) sama-sama memblokir IP runner AS. Deribit dipakai sebagai lapis ketiga. |
| Analisa bertanda "belum terverifikasi" | Critic gagal dijalankan pada run itu. Analisanya tetap dikirim supaya tidak hilang, tapi statusnya ditandai terus terang di web dan Telegram. |
| Riwayat OI kosong | Tidak ada bursa yang menyediakannya dari IP runner. Perubahan OI diturunkan dari brief sebelumnya sebagai gantinya. |
| Sebagian metrik on-chain hilang | Tier gratis Coin Metrics tidak menyediakan semua metrik. Metrik yang ditolak dibuang otomatis lalu permintaan diulang — sisanya tetap didapat. |
| Kartu opsi/valuasi/aliran kosong | Deribit, Coin Metrics, atau Coinbase sedang tidak terjangkau. Semuanya opsional — brief tetap terbit, dan sumber yang gagal tercatat di `failed_sources`. |
| Bagian pernyataan kosong | Wajar kalau memang tidak ada pernyataan relevan dalam 48 jam. Kalau selalu kosong, cek `sumber_gagal` di log — Truth Social memang sering memblokir IP data center. |
| Log penuh "HTTP 451 restricted location" | Normal di GitHub Actions. Binance menolak IP runner yang berbasis AS — pembatasan wilayah permanen. Harga otomatis pindah ke CoinGecko, funding/OI ke Bybit. |
| Divergensi whale kosong | Urutan sumbernya Binance → OKX → Bybit. Dua yang pertama memisahkan "top trader" dari "seluruh akun"; Bybit hanya punya rasio agregat, jadi kalau sampai jatuh ke Bybit hanya sisi ritel yang pulih dan divergensi memang tidak bisa dihitung. |
| Sebagian analisa AI kosong tanpa keterangan | Disengaja — kalau critic menemukan angka karangan, bagian itu ditahan secara senyap tanpa banner atau penjelasan teknis ke pembaca. Cek `ai.bagian_ditahan` dan `ai.critic.corrections` di `latest.json` kalau ingin tahu alasannya. Kalimat bernada anjuran tidak lagi menahan apa pun, cuma ditandai. |
| Step AI gagal dengan "terpotong di batas max_tokens" | Naikkan `max_tokens` step tersebut di `src/analysis/news_analysis.py`. Balasan yang terpotong ditolak sengaja, karena JSON separuh jadi lebih berbahaya daripada tidak ada hasil. |
| Brief terbit tanpa bagian AI | `OPENROUTER_API_KEY` kosong, nama model masih placeholder, atau budget per run tercapai. Cek `data_quality.catatan`. |
| Telegram tidak masuk | Pastikan sudah mengirim pesan pertama ke bot, dan `TELEGRAM_CHAT_ID` benar (ID grup diawali minus). |
| Halaman Pages kosong | GitHub Pages belum diarahkan ke folder `/docs`, atau `latest.json` belum pernah dibuat. |
| Workflow berhenti jalan sendiri | Repo tidak aktif 60 hari. Aktifkan kembali di tab Actions. |

---

## Disclaimer

Konten yang dihasilkan bersifat informasional dan **bukan saran investasi**. Bagian yang ditandai `✦ AI` dihasilkan model bahasa dan dapat mengandung kesalahan.
