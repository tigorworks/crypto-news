# Temuan & Rencana Kerja

Catatan kelemahan, bug, dan pekerjaan yang bisa diambil berikutnya.
Ditulis 18 Agustus 2026, berdasarkan pemeriksaan kode dan data produksi —
bukan daftar generik.

Tiap butir menyebut **buktinya**, supaya bisa diverifikasi ulang dan tidak
dikerjakan atas dasar dugaan. Yang belum terverifikasi ditandai *(dugaan)*.

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

### 1.2 Stylesheet uji tidak sama dengan produksi

Tailwind di produksi dimuat dari CDN (JIT, menghasilkan semua utility),
sementara pengujian lokal memakai `tw.css` yang di-vendor. Yang di-vendor
terbukti **kehilangan utility**: `.block`, `.line-clamp-2`, dan
`.self-center` semuanya tidak ada.

Tiga kali ini menyebabkan perbaikan yang tampak gagal padahal kodenya benar
— atau sebaliknya, verifikasi yang menyesatkan. Semua kegagalannya **senyap**:
tidak ada error, hanya tata letak yang salah.

> **Kerjakan:** bangkitkan ulang `tw.css` dari sumber halaman saat ini, atau
> jalankan Tailwind CLI di CI supaya CSS uji selalu sinkron.

### 1.3 `skenario` agen dihasilkan tapi tidak ditampilkan

Setelah kebijakan pindah ke analisa AI (PR #72), field `skenario` dari
`agen_kebijakan` **tidak dirender di mana pun** — sudah dicek: tidak ada di
`index.html`, `app.js`, maupun `telegram.py`.

Model masih menghasilkannya tiap run, lengkap dengan pagar anti-angka-harga
yang ikut dijalankan. Biaya token terbuang untuk keluaran yang tidak dibaca
siapa pun.

> **Kerjakan:** pilih satu — tampilkan lagi di analisa AI, atau hapus dari
> skema keluaran agen.

### 1.4 Nama fungsi Telegram tidak lagi cocok dengan isinya

`_blok_siaga_kebijakan()` sekarang merender **jendela risiko**, bukan siaga
kebijakan. Namanya tertinggal setelah isinya berubah.

Sepele, tapi persis jenis hal yang menyesatkan pembaca kode berikutnya.

### 1.5 Komentar usang soal `!b.mundur.lewat`

Di `index.html` ada penjaga `b.mundur && !b.mundur.lewat` dengan komentar
yang menjelaskan kasus "JENDELA LEWAT" — keadaan yang **tidak bisa terjadi
lagi** untuk baris jendela sejak PR #69.

Penjaganya sendiri masih berguna untuk baris agenda (agenda bisa lewat di
sela satu menit antar penyegaran), tapi komentarnya menerangkan alasan yang
salah.

### 1.6 Feed `coinjournal.net` gagal terus-menerus

Muncul di `data_quality.catatan` sebagai "Feed gagal" pada setiap run yang
diperiksa. Di `config.yaml` ia masih terdaftar dengan bobot 3.

Tidak fatal — corong berita tetap mengumpulkan seribuan artikel — tapi ia
menghabiskan satu percobaan request tiap run dan mengotori catatan kualitas
sehingga kegagalan yang benar-benar baru jadi lebih sulit terlihat.

> **Kerjakan:** perbaiki URL-nya, atau keluarkan dari daftar.

### 1.7 Tidak ada telemetri lintas hari

Setiap reset data menghapus seluruh arsip, dan bersamanya hilang kemampuan
melihat **tren**: seberapa sering critic menahan narasi, berapa biaya rata-
rata, sumber mana yang paling sering gagal.

Saat dokumen ini ditulis hanya ada satu berkas arsip, jadi pertanyaan
"seberapa sering critic menahan narasi?" **tidak bisa dijawab** — padahal
itu justru metrik kesehatan yang paling penting.

> **Kerjakan:** simpan ringkasan metrik per run di berkas terpisah yang
> tidak ikut terhapus saat reset.

---

## 2. Risiko yang perlu diawasi

### 2.1 Anggaran LLM cukup dekat dengan batas

`max_cost_usd_per_run: 0.60`. Run yang diperiksa memakai **$0.400 (67%)**,
tapi run lain pada hari yang sama pernah mencapai **$0.597 — 99,5% anggaran**.

Kalau budget habis di tengah, revisi critic terpotong dan analisa bisa hilang
sama sekali. Komentar di `config.yaml` sendiri sudah memperingatkan hal ini.

> **Kerjakan:** naikkan batas sedikit, atau catat peringatan eksplisit saat
> pemakaian melewati ~85%.

### 2.2 Critic menahan narasi karena angka karangan

Pada dua brief terakhir yang tersimpan, `critic.passed = false` dengan
`bagian_ditahan = ['narasi']`, sebabnya **angka karangan berkeparahan fatal**.

Sistemnya bekerja benar — lebih baik menahan daripada menerbitkan angka
palsu — tapi kalau ini sering terjadi, pembaca kehilangan bagian utama brief.
Frekuensinya belum terukur (lihat 1.7).

### 2.3 Hanya volume yang dijaga terhadap candle parsial

Penjaga kelengkapan candle baru dipasang untuk **volume**, karena volume
menumpuk sepanjang hari. Indikator lain memakai harga penutupan candle
berjalan, yang memang wajar bernilai harga sekarang.

*(dugaan)* Perlu dipastikan tidak ada indikator lain yang ikut terdistorsi
saat candle belum penuh — kandidat yang paling perlu dicek: VWAP harian dan
OBV.

---

## 3. Optimasi

### 3.1 Corong berita sangat boros

Dari data terakhir: **1.177 artikel terkumpul → 214 segar → 209 unik → 25
dipakai**. Hanya sekitar **2%** yang benar-benar terpakai.

Penyaringan awal berbasis relevansi sudah ada, tapi 1.177 fetch untuk 25
artikel berarti sebagian besar waktu run (456 detik total) habis di
pengumpulan yang terbuang.

> **Kerjakan:** saring lebih awal berdasarkan umur dan kata kunci sebelum
> fetch penuh, atau kurangi `max_fetch` sambil memantau apakah kualitas turun.

### 3.2 Durasi run ~7,6 menit

`durasi_detik: 456,8`. Belum bermasalah (timeout workflow 15 menit), tapi
marginnya tinggal separuh, dan cron GitHub sendiri kerap tertunda.

### 3.3 Langkah agen kebijakan kini setengah terpakai

Keluarannya masuk ke konteks sintesis, tapi `siaga` dan `skenario`-nya tidak
lagi ditampilkan. Kalau 1.3 diselesaikan dengan menghapus, pertimbangkan
apakah langkah LLM ini masih perlu berdiri sendiri atau bisa dilebur ke
sintesis.

---

## 4. Hal baru yang bisa dikerjakan

### 4.1 Sumber data likuidasi *(sudah lama tertunda)*

Satu-satunya butir yang masih terbuka dari daftar tugas lama. Belum
dikerjakan karena butuh koneksi persisten (WebSocket), sementara pipeline ini
berjalan sekali lalu mati.

> Kemungkinan jalan: API REST yang menyediakan agregat likuidasi harian,
> bukan stream real-time.

### 4.2 Uji "sehari penuh" untuk hitung mundur hidup

Hitung mundur dan padamnya panel jendela bergantung pada waktu nyata. Yang
sudah diuji baru titik-titik tertentu.

> Nilai tambahnya: menjalankan halaman dengan jam palsu yang dimajukan dari
> pagi ke malam, memastikan panelnya menyala dan padam pada saat yang tepat
> tanpa perlu menunggu hari berganti.

### 4.3 Peringatan saat brief basi

Kalau cron gagal beberapa hari, halaman tetap menampilkan data lama dengan
label "3 hari lalu" yang mudah terlewat.

> Nilai tambahnya: penanda mencolok saat brief lebih tua dari ~36 jam.

### 4.4 Verifikasi ETF lintas sumber

Arus ETF kini dari SoSoValue dengan cadangan Farside yang sering 403. Tidak
ada pemeriksaan silang.

> Nilai tambahnya: kalau dua sumber berbeda jauh, itu sinyal salah satunya
> rusak — lebih baik ketahuan daripada menerbitkan angka yang salah.

### 4.5 Riwayat siaga

Sekarang jendela risiko hanya menggambarkan **saat ini**. Tidak ada cara
melihat "akhir pekan lalu siaganya tinggi, dan ternyata harga turun 4%".

> Nilai tambahnya: mengukur apakah alarmnya benar-benar prediktif — satu-
> satunya cara tahu fitur ini berguna atau cuma terasa berguna.

---

## Urutan yang saya sarankan

1. **1.1 tes di CI** — tanpa ini, setiap perbaikan lain dikerjakan tanpa jaring pengaman
2. **1.2 CSS uji sinkron** — supaya verifikasi visual bisa dipercaya
3. **1.3 & 1.4 & 1.5** — bersih-bersih murah setelah jaringnya ada
4. **1.7 telemetri** — supaya 2.1 dan 2.2 bisa diukur, bukan ditebak
5. **3.1 corong berita** — penghematan terbesar
6. Sisanya menurut kebutuhan
