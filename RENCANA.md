# Temuan & Rencana Kerja

Catatan kelemahan, bug, dan pekerjaan yang bisa diambil berikutnya.
Ditulis 18 Agustus 2026, berdasarkan pemeriksaan kode dan data produksi —
bukan daftar generik.

Tiap butir menyebut **buktinya**, supaya bisa diverifikasi ulang dan tidak
dikerjakan atas dasar dugaan. Yang belum terverifikasi ditandai *(dugaan)*.

**Diperbarui 21 Agustus 2026.** Butir 1.2–1.7, 2.2, 2.3, 3.1–3.3, dan 4.1–4.5
sudah dikerjakan; statusnya ditulis di masing-masing butir. Satu butir (1.6)
ternyata **tidak terbukti** saat diperiksa ulang, dan itu ikut dicatat apa
adanya.

---

## 1. Bug & kelemahan

### 1.1 Tidak ada tes yang berjalan otomatis — **paling serius**

Repo tidak punya direktori tes, dan `.github/workflows/` cuma berisi
`brief.yml` yang jalan lewat cron. **Tidak ada satu pun workflow yang
terpicu `pull_request`.**

Seluruh tes yang dipakai selama pengembangan hidup di scratchpad sesi dan
**ikut hilang saat sesi berakhir** — termasuk uji jendela pasar 12 kasus,
pembulatan hitung mundur 8 kasus, pagar skenario 8 kasus, kelengkapan candle
8 kasus, dan lima berkas uji Chromium.

Akibatnya sudah terlihat berkali-kali: fixture jadi usang tanpa ketahuan,
dan beberapa tes sempat **lulus karena alasan yang salah** — pernah semua
kasus stylist tertolak oleh penanda wajib sebelum sampai ke hal yang
sebenarnya diuji, jadi hijau tanpa memeriksa apa pun.

> **Kerjakan:** pindahkan tes ke `tests/` di repo, tambah workflow
> `pull_request` yang menjalankannya. Ini prasyarat bagi hampir semua butir
> lain di dokumen ini.

**Status 22 Agu: SELESAI.** `tests/` ada di repo dengan **62 uji**, dan
`.github/workflows/uji.yml` menjalankannya pada tiap `pull_request`.

Workflow-nya juga dipicu `push` ke `main`, bukan cuma PR: brief harian
menulis ke `docs/data` dan `docs/index.html` langsung di main tanpa melewati
PR, sementara uji halaman membaca kedua berkas itu. Tanpa pemicu itu,
kerusakan yang masuk lewat jalur cron tidak akan pernah terlihat.

Satu bug ikut ketahuan saat menyiapkannya: `tests/conftest.py` memaksakan
jalur Chromium milik lingkungan pengembangan. Di runner CI jalur itu tidak
ada, dan seluruh uji halaman akan gagal dengan "Executable doesn't exist" —
kegagalan yang sama sekali tidak berhubungan dengan yang sedang diuji.
Sekarang jalurnya dipakai hanya kalau berkasnya memang ada; selain itu
Playwright mencari sendiri.

Diverifikasi dengan menjalankan urutan langkah workflow dari nol (aset
dihapus lebih dulu): 62 lolos. Tanpa aset yang dibangkitkan, uji halaman
DILEWATI dengan pesan petunjuk — 46 lolos, 16 dilewati — bukan gagal
berantakan.

### 1.2 Stylesheet uji tidak sama dengan produksi — **selesai**

Tailwind di produksi dimuat dari CDN (JIT, menghasilkan semua utility),
sementara pengujian lokal memakai `tw.css` yang di-vendor. Yang di-vendor
terbukti **kehilangan utility**: `.block`, `.line-clamp-2`, dan
`.self-center` semuanya tidak ada.

Tiga kali ini menyebabkan perbaikan yang tampak gagal padahal kodenya benar
— atau sebaliknya, verifikasi yang menyesatkan. Semua kegagalannya **senyap**:
tidak ada error, hanya tata letak yang salah.

> **Kerjakan:** bangkitkan ulang `tw.css` dari sumber halaman saat ini, atau
> jalankan Tailwind CLI di CI supaya CSS uji selalu sinkron.

**Status:** `scripts/bangun_aset_uji.py` membangkitkan `tw.css` dari
`index.html` **dan** `app.js` (banyak kelas hanya muncul sebagai string di
dalam getter Alpine — justru itu yang paling sering hilang), memakai
konfigurasi Tailwind yang sama persis dengan halaman, lalu **memeriksa**
bahwa ketiga utility di atas benar-benar ada dan gagal keras kalau tidak.
Alpine, Chart.js, dan Lucide ikut diambil dari registry npm dengan versi
yang dikunci ke yang dipakai halaman. Hasilnya tidak di-commit.

### 1.3 `skenario` agen dihasilkan tapi tidak ditampilkan — **selesai**

Setelah kebijakan pindah ke analisa AI (PR #72), field `skenario` dari
`agen_kebijakan` **tidak dirender di mana pun** — sudah dicek: tidak ada di
`index.html`, `app.js`, maupun `telegram.py`.

Model masih menghasilkannya tiap run, lengkap dengan pagar anti-angka-harga
yang ikut dijalankan. Biaya token terbuang untuk keluaran yang tidak dibaca
siapa pun.

> **Kerjakan:** pilih satu — tampilkan lagi di analisa AI, atau hapus dari
> skema keluaran agen.

**Status:** dihapus, bersama seluruh langkah LLM-nya (lihat 3.3).

### 1.4 Nama fungsi Telegram tidak lagi cocok dengan isinya — **selesai**

`_blok_siaga_kebijakan()` sekarang merender **jendela risiko**, bukan siaga
kebijakan. Namanya tertinggal setelah isinya berubah.

**Status:** diganti jadi `_blok_jendela_risiko()`.

### 1.5 Komentar usang soal `!b.mundur.lewat` — **selesai**

Di `index.html` ada penjaga `b.mundur && !b.mundur.lewat` dengan komentar
yang menjelaskan kasus "JENDELA LEWAT" — keadaan yang **tidak bisa terjadi
lagi** untuk baris jendela sejak PR #69.

**Status:** komentarnya ditulis ulang untuk menerangkan alasan yang benar
(baris agenda, yang bisa lewat di sela satu menit antar penyegaran), dan
menyebutkan kenapa baris jendela tidak pernah sampai ke sana dalam keadaan
lewat. Penjagaannya sendiri tetap, dan sekarang ada uji yang menjaga agar
padamnya jendela tidak ikut mematikan baris agenda.

### 1.6 Feed `coinjournal.net` gagal terus-menerus — **tidak terbukti**

Muncul di `data_quality.catatan` sebagai "Feed gagal" pada setiap run yang
diperiksa. Di `config.yaml` ia masih terdaftar dengan bobot 3.

> **Kerjakan:** perbaiki URL-nya, atau keluarkan dari daftar.

**Status: feed DIPERTAHANKAN.** Diperiksa ulang terhadap sembilan arsip
produksi: `coinjournal.net` muncul sebagai feed gagal **hanya pada run 17
Agustus 18.28**, dan delapan run sesudahnya tidak punya satu pun feed gagal.
Premis butir ini — "gagal pada setiap run" — sudah tidak berlaku, jadi
membuangnya berarti kehilangan sumber yang bekerja karena satu kegagalan
sesaat.

Yang dikerjakan sebagai gantinya: kegagalan feed kini ikut tercatat di
telemetri per run (1.7), jadi pertanyaan "feed ini gagal berulang atau
sesekali?" bisa dijawab dari data, bukan dari ingatan.

### 1.7 Tidak ada telemetri lintas hari — **selesai**

Setiap reset data menghapus seluruh arsip, dan bersamanya hilang kemampuan
melihat **tren**: seberapa sering critic menahan narasi, berapa biaya rata-
rata, sumber mana yang paling sering gagal.

> **Kerjakan:** simpan ringkasan metrik per run di berkas terpisah yang
> tidak ikut terhapus saat reset.

**Status:** `src/output/telemetri.py` menulis `state/telemetri.jsonl` (satu
baris per run, di luar `docs/` sehingga selamat dari reset arsip) dan
`docs/data/telemetri.json` (ringkasan untuk halaman). Yang dicatat: biaya
total **dan per langkah**, token, durasi, status critic beserta bagian yang
ditahan, sumber & feed yang gagal, corong berita, tingkat siaga, dan harga.

`scripts/telemetri_dari_arsip.py` mengisinya dari arsip yang sudah ada, jadi
pertanyaan di atas bisa dijawab hari ini. Hasil sembilan run pertama ada di
2.1 dan 2.2 di bawah.

---

## 2. Risiko yang perlu diawasi

### 2.1 Anggaran LLM cukup dekat dengan batas — **terukur, lebih buruk dari dugaan**

`max_cost_usd_per_run: 0.60`. Run yang diperiksa memakai **$0,400 (67%)**,
tapi run lain pada hari yang sama pernah mencapai **$0,597 — 99,5% anggaran**.

> **Kerjakan:** naikkan batas sedikit, atau catat peringatan eksplisit saat
> pemakaian melewati ~85%.

**Status (bukan bagian yang diminta, tapi telemetri menjawabnya sendiri):**
dari sembilan run yang tercatat, rata-ratanya **$0,540**, tertingginya
**$0,759** — artinya plafon $0,60 pernah **terlampaui**, karena pemeriksaan
anggaran terjadi sebelum sebuah panggilan dan satu panggilan besar bisa
menyeberanginya di tengah jalan. **Lima dari sembilan** run memakai ≥85%
plafon.

Yang dikerjakan: peringatan eksplisit di log begitu pemakaian melewati 85%,
plafon dinaikkan ke 0.75 supaya revisi critic tidak terpotong, dan
penghematan yang sesungguhnya dikerjakan di tempat yang benar (lihat 3.1,
3.3, dan 2.2).

### 2.2 Critic menahan narasi karena angka karangan — **diukur, dan penyebab lain ditemukan**

Pada dua brief terakhir yang tersimpan, `critic.passed = false` dengan
`bagian_ditahan = ['narasi']`, sebabnya **angka karangan berkeparahan fatal**.

Sistemnya bekerja benar — lebih baik menahan daripada menerbitkan angka
palsu — tapi kalau ini sering terjadi, pembaca kehilangan bagian utama brief.
Frekuensinya belum terukur (lihat 1.7).

**Status: sekarang terukur — dan masalahnya ternyata bukan yang diduga.**
Dari sembilan run: critic **benar-benar berjalan hanya 4 kali**, dan dari
empat itu ia menahan narasi **1 kali (25%)**. Lima run sisanya tercatat
`dijalankan: false` — analisanya terbit **tanpa pernah diperiksa**, sementara
tokennya sudah telanjur dibayar.

Penjelasan yang paling masuk akal: `gpt-5.1` adalah model **penalar**, dan
token penalarannya ikut memakan jatah `max_tokens` sebelum satu huruf
jawaban ditulis; balasan yang mentok di batas ditolak `llm.chat()`. Jatahnya
dinaikkan 6.000 → 14.000 dan upaya penalarannya diturunkan lewat
`llm.reasoning_effort` (`critic: low`). Ini sekaligus soal biaya dan soal
kualitas — brief yang terbit tanpa critic adalah brief yang tidak pernah
diperiksa.

Telemetri membedakan ketiganya sejak sekarang: *diperiksa dan lolos*,
*diperiksa dan menahan*, *tidak sempat diperiksa*.

### 2.3 Hanya volume yang dijaga terhadap candle parsial — **selesai**

Penjaga kelengkapan candle baru dipasang untuk **volume**, karena volume
menumpuk sepanjang hari. Indikator lain memakai harga penutupan candle
berjalan, yang memang wajar bernilai harga sekarang.

*(dugaan)* Perlu dipastikan tidak ada indikator lain yang ikut terdistorsi
saat candle belum penuh — kandidat yang paling perlu dicek: VWAP harian dan
OBV.

**Status: dugaannya benar untuk keduanya, dan keduanya sudah ditangani.**

- **OBV** menambahkan VOLUME candle ke akumulasi, jadi candle setengah jalan
  menyumbang separuh volumenya dan kemiringan enam candle terakhir bisa
  berubah *hanya karena jam menjalankan pipeline*. Arahnya kini dihitung dari
  candle yang sudah SELESAI saja, ditandai `obv_arah_tanpa_candle_berjalan`.
- **VWAP harian** pada brief yang cuma memakai timeframe 1D berarti harga
  rata-rata tertimbang **hari berjalan** — masih bergerak sampai tengah malam
  UTC. Nilainya tetap ditampilkan, tapi ditandai `vwap_harian_parsial` dan
  diberi keterangan "masih berjalan hari ini" di halaman.

Prompt interpretasi teknikal ikut diberi tahu ketiganya, supaya model tidak
menulis "volume tipis" atas dasar candle yang belum penuh.

---

## 3. Optimasi

### 3.1 Corong berita sangat boros — **selesai**

Dari data terakhir: **1.177 artikel terkumpul → 214 segar → 209 unik → 25
dipakai**. Hanya sekitar **2%** yang benar-benar terpakai.

> **Kerjakan:** saring lebih awal berdasarkan umur dan kata kunci sebelum
> fetch penuh, atau kurangi `max_fetch` sambil memantau apakah kualitas turun.

**Satu koreksi atas premisnya:** pengambilannya **per FEED**, bukan per
artikel — 36 permintaan HTTP, bukan 1.177. Jadi yang boros bukan fetch-nya,
melainkan apa yang terjadi sesudahnya: keempat ratus artikel unik itu
dikirim utuh ke langkah `filter` dan ditagih token untuk memilih 25.

Yang dikerjakan:

- Artikel berskor kata kunci **nol** dibuang kode sebelum menyentuh model
  (`news.prefilter_min_skor`), plus pagar atas jumlah kandidat
  (`news.maks_kandidat_llm`). Ambangnya sengaja tidak lebih tinggi dari 1.
- Langkah `filter` kini hanya menuliskan artikel yang **lolos** ambang.
  Sebelumnya model menulis skor untuk setiap kandidat — ratusan baris
  keluaran yang langsung dibuang kode.
- `berita_corong.kandidat_llm` melaporkan berapa yang benar-benar sampai ke
  model, jadi efeknya terlihat, bukan diasumsikan.

### 3.2 Durasi run ~7,6 menit — **dikerjakan**

`durasi_detik: 456,8`. Belum bermasalah (timeout workflow 15 menit), tapi
marginnya tinggal separuh, dan cron GitHub sendiri kerap tertunda.

**Status:** telemetri sembilan run memberi rata-rata **496 detik**,
tertinggi **662 detik** — jadi marginnya memang lebih tipis dari yang
dicatat di sini. Feed kini ditarik **berbarengan** (delapan sekaligus);
hampir seluruh waktu pengambilan habis menunggu jaringan, jadi di situlah
penghematan terbesar yang tidak mengorbankan apa pun. Urutan hasilnya tetap
mengikuti urutan feed di config, bukan siapa yang selesai duluan. Satu
panggilan LLM juga hilang seluruhnya (3.3). Efeknya akan terbaca di
telemetri run berikutnya.

### 3.3 Langkah agen kebijakan kini setengah terpakai — **selesai**

Keluarannya masuk ke konteks sintesis, tapi `siaga` dan `skenario`-nya tidak
lagi ditampilkan. Kalau 1.3 diselesaikan dengan menghapus, pertimbangkan
apakah langkah LLM ini masih perlu berdiri sendiri atau bisa dilebur ke
sintesis.

**Status: dilebur.** Langkah LLM-nya dibuang seluruhnya. Yang benar-benar
dipakai halaman dan Telegram — fase jendela, kerapuhan, `risiko_jendela`,
ringkasan pendaratan sinyal — semuanya hitungan kode, dan kini dirakit
`jendela_pasar.rangkuman_kode()`. Penilaian kebijakannya sendiri tetap ada
di sintesis, yang memang sudah menerima berita dan pernyataan yang sama
lengkap dengan field `mendarat` per item.

Efek sampingnya justru perbaikan: panel jendela risiko sekarang tetap
lengkap pada hari seluruh langkah LLM gagal — hari ketika sinyal yang tidak
bergantung model paling berharga.

---

## 4. Hal baru yang bisa dikerjakan

### 4.1 Sumber data likuidasi — **selesai**

Satu-satunya butir yang masih terbuka dari daftar tugas lama. Belum
dikerjakan karena butuh koneksi persisten (WebSocket), sementara pipeline ini
berjalan sekali lalu mati.

> Kemungkinan jalan: API REST yang menyediakan agregat likuidasi harian,
> bukan stream real-time.

**Status:** `src/collectors/likuidasi.py` — riwayat order likuidasi lewat
REST (kontrak perpetual BTC di OKX), diagregasi jadi total 24 jam per sisi.
Tampil di kartu Posisi Pasar sebagai batang dua warna dan di Telegram
sebagai satu baris, keduanya **menyebut nama bursanya**: angkanya satu
bursa, bukan gabungan seluruh pasar, dan situs agregator akan menampilkan
angka jauh lebih besar.

**Terverifikasi di produksi 22 Agustus 08.06 UTC.** Endpoint OKX menjawab:
7.420 order likuidasi dalam 24 jam penuh — long $46,4 juta, short $45,8
juta, total $92,2 juta, sisi dominan "seimbang", order terbesar $5,9 juta.
`likuidasi` tidak muncul di `failed_sources`.

Sebelum run itu, yang teruji baru parsing, arah sisi (`sell` = long yang
dilikuidasi), pemotongan jendela, dan penelusuran halaman — semuanya lewat
respons tiruan, karena lingkungan tempat kode ini ditulis memblokir akses
keluar ke OKX. Ia tetap sengaja **tidak** ikut menentukan skor kualitas
data: endpoint publiknya pernah berganti bentuk, dan kegagalannya tidak
boleh menyeret label keyakinan seluruh brief.

### 4.2 Uji "sehari penuh" untuk hitung mundur hidup — **selesai**

Hitung mundur dan padamnya panel jendela bergantung pada waktu nyata. Yang
sudah diuji baru titik-titik tertentu.

**Status:** `tests/test_hitung_mundur_seharian.py` menjalankan halaman
dengan **jam palsu** yang dimajukan dari pagi ke malam: panelnya harus hidup
sepanjang jendela dengan hitung mundur yang ikut menyusut tiap jam, lalu
benar-benar padam — baris DAN bagian rinciannya — setelah bursa buka. Uji
kedua memastikan padamnya jendela tidak ikut mematikan baris agenda yang
berbagi kartu dan penjagaan yang sama.

### 4.3 Peringatan saat brief basi — **selesai**

Kalau cron gagal beberapa hari, halaman tetap menampilkan data lama dengan
label "3 hari lalu" yang mudah terlewat.

**Status:** lewat 36 jam, chip umurnya berubah warna (kuning, lalu merah di
atas 72 jam) dan sebuah pita muncul di bawah judul yang menyatakan seluruh
angka di halaman menggambarkan keadaan saat brief dibuat. Ambangnya 36 jam,
bukan 24: cron GitHub kerap tertunda dan 26 jam masih normal. Ada uji untuk
kedua sisinya — muncul saat basi, dan **tidak** muncul saat cron cuma telat
wajar.

### 4.4 Verifikasi ETF lintas sumber — **selesai**

Arus ETF kini dari SoSoValue dengan cadangan Farside yang sering 403. Tidak
ada pemeriksaan silang.

**Status:** saat SoSoValue berhasil, Farside tetap dicoba sebagai pembanding
(timeout pendek, tanpa retry). Hasilnya di `market.etf_flow_verifikasi`:
`cocok` (≤5%), `berbeda` (>5%, muncul sebagai keterangan di halaman dan
catatan kualitas), `terlalu_kecil_untuk_dibandingkan` (kedua angka di bawah
$20 juta — pada arus mendekati nol persentase selisih meledak tanpa sebab),
atau `tanpa_pembanding` (Farside menolak, yang memang lazim).

Angka utamanya tidak pernah diganti diam-diam oleh pembanding: kalau
keduanya berbeda jauh, yang benar adalah memberi tahu, bukan menebak siapa
yang betul.

### 4.5 Riwayat siaga — **selesai**

Sekarang jendela risiko hanya menggambarkan **saat ini**. Tidak ada cara
melihat "akhir pekan lalu siaganya tinggi, dan ternyata harga turun 4%".

> Nilai tambahnya: mengukur apakah alarmnya benar-benar prediktif — satu-
> satunya cara tahu fitur ini berguna atau cuma terasa berguna.

**Status:** telemetri mencatat tingkat siaga tiap run beserta harganya, lalu
membandingkannya dengan run berikutnya yang berjarak **minimal 18 jam**
(brief terbit sekali sehari, tapi run manual bisa menyelip beberapa jam
setelahnya — selisih harga dua jam tidak menguji apa pun). Footer halaman
menampilkan riwayatnya plus rata-rata perubahan per tingkat siaga.

Jumlah runnya masih kecil, dan itu ditulis terang-terangan di halamannya:
ini catatan pengamatan, bukan bukti sebab-akibat.

---

## Pembacaan telemetri, 22 Agustus

Butir ketiga dari daftar sebelumnya: memeriksa apakah penghematannya
benar-benar terlihat, bukan cuma diharapkan.

| | 21 Agu 22.23 | 21 Agu 23.49 | **22 Agu 08.06** |
|---|---|---|---|
| `synthesis` | $0,133 | $0,178 | **$0,112** |
| `outlook` | $0,079 | $0,089 | **$0,051** |
| total run | $0,421 | $0,497 | **$0,348** |

Total per run turun **36% dari rata-rata lama** ($0,540). `outlook` turun
paling tajam — masuk akal, karena dua field keluarannya dibuang seluruhnya
dan itu perubahan deterministik.

**Yang belum bisa disimpulkan:** seberapa besar andil `reasoning_effort`.
Ukuran konteks masuk berbeda-beda tiap run, jadi selisih biaya di atas tidak
bisa dipisahkan antara "penalaran lebih pendek" dan "harinya memang lebih
sepi". Baru run 22 Agustus yang menyimpan token per langkah; run berikutnya
yang akan membuat perbandingannya sah.

**Yang sudah pasti terlihat:** `synthesis` tetap langkah termahal, dan
sebagian besar keluarannya tidak pernah sampai ke pembaca —

```
ditagih   7.071 token keluaran
mendarat  ~1.500 token di brief  (bagian + penyebab_pergerakan)
selisih   ~5.570 token, 79%
```

`reasoning_effort: medium` DITERIMA provider (tidak ada `effort_ditolak` di
log run itu), tapi jelas tidak menghapus penalarannya. Menurunkannya lagi ke
`low` adalah langkah berikutnya yang paling jelas — sengaja belum dilakukan:
menyetel dua kali berturut-turut tanpa data pembanding di antaranya persis
cara membuat perubahan yang tidak bisa dievaluasi.

---

## Yang tersisa

1. **Turunkan `reasoning_effort` synthesis ke `low`** — setelah beberapa run
   menyimpan token per langkah, supaya efeknya bisa dibandingkan. 79% token
   keluaran yang tidak pernah dibaca adalah sasaran penghematan terbesar
   yang tersisa.
2. **Ukur pengulangan dalam prosa model.** Arus ETF $606,29 juta diceritakan
   di empat tempat pada brief 22 Agustus, padahal prompt sudah melarangnya
   (aturan "SATU PERISTIWA DIBAHAS SATU KALI SAJA"). Menambah aturan prompt
   lagi sudah dua kali terbukti tidak menempel di repo ini — yang dibutuhkan
   pengukuran lebih dulu, baru memilih apakah perbaikannya di prompt atau di
   struktur field.
3. **Langkah `format` yang hasilnya ditolak.** Pada run 22 Agustus 08.06,
   perapi pesan Telegram menghapus penanda wajib `ULASAN LENGKAP` dan
   hasilnya dibuang — $0,0075 terbayar tanpa hasil. Belum diketahui apakah
   ini sesekali atau pola berulang; perlu dilihat beberapa run dulu.
