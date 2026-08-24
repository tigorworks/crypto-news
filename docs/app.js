/* Nawala — Ringkasan Pasar Kripto. Logika halaman.
 * Tanpa build step: Alpine.js untuk state, Chart.js untuk grafik, Lucide untuk ikon.
 */

const BULAN_ID = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
  'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'];
const BULAN_SINGKAT_ID = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun',
  'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];

/* Username bot Telegram. Nilai ini juga dikirim lewat brief
   (`data.bot_telegram`, dari config.yaml) dan itu yang dipakai kalau tersedia
   — konstanta di sini murni cadangan supaya tombol berlangganan tetap muncul
   saat latest.json belum ada. Justru di saat itulah tombolnya paling
   dibutuhkan: pengunjung yang datang sebelum brief pertama terbit tidak punya
   apa pun untuk dibaca, jadi setidaknya bisa berlangganan dulu. */
const BOT_TELEGRAM = 'tmmcrypto_bot';

const LABEL_MAKRO = {
  dxy: 'DXY', ust10y: 'Yield UST 10Y', wti: 'Minyak WTI',
  gold: 'Emas', nasdaq: 'Nasdaq', sp500: 'S&P 500', vix: 'VIX',
  usdjpy: 'USD/JPY',
};

/* Geser Date ke WIB supaya getter lokal (getHours dsb) membaca nilai WIB.
 * Seluruh brief memakai acuan WIB, jadi pembaca di zona waktu mana pun
 * melihat jam yang sama dengan yang tertulis di header dan Telegram. */
function keWIB(tanggal) {
  return new Date(tanggal.getTime() + (7 * 60 + tanggal.getTimezoneOffset()) * 60000);
}

/* Pecah satu blok prosa panjang jadi 2-3 paragraf di batas kalimat.

 * Pipeline sudah melakukan hal yang sama saat brief dibuat, tapi ini tetap
 * perlu ada di sisi tampilan: brief yang SUDAH TERSIMPAN (latest.json hari
 * ini dan seluruh arsip) menyimpan satu blok panjang, dan tanpa ini mereka
 * akan selamanya terbaca sebagai dinding teks. Ambang dan aturannya sengaja
 * dibuat sama dengan _pecah_paragraf() di src/analysis/news_analysis.py.
 *
 * Tidak menyentuh teks yang sudah dipecah, dan tidak memecah yang pendek. */
const MIN_KARAKTER_PECAH = 420;
const MIN_KALIMAT_PECAH = 4;
const TARGET_KARAKTER_PARAGRAF = 400;

function pecahParagraf(teks) {
  const isi = (teks || '').trim();
  if (!isi) return [];
  if (isi.includes('\n\n') || isi.length < MIN_KARAKTER_PECAH) {
    return isi.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  }
  const kalimat = isi.split(/(?<=[.!?])\s+(?=[A-Z"'\u201c])/).map((k) => k.trim()).filter(Boolean);
  if (kalimat.length < MIN_KALIMAT_PECAH) return [isi];

  // Jumlah paragraf mengikuti PANJANG, bukan cacah kalimat.
  let jumlah = Math.min(3, Math.max(2, Math.round(isi.length / TARGET_KARAKTER_PARAGRAF)));
  jumlah = Math.min(jumlah, kalimat.length);

  // Titik potong dari panjang KUMULATIF — lihat _pecah_paragraf() di
  // src/analysis/news_analysis.py, algoritmanya sengaja identik.
  const kumulatif = [];
  let jalan = 0;
  for (const k of kalimat) { jalan += k.length + 1; kumulatif.push(jalan); }
  const total = kumulatif[kumulatif.length - 1];

  const potong = [];
  for (let j = 1; j < jumlah; j++) {
    const sasaran = (total * j) / jumlah;
    let pilih = null;
    for (let i = 0; i < kalimat.length - 1; i++) {
      if (potong.length && i <= potong[potong.length - 1]) continue;
      if (kalimat.length - 1 - i < jumlah - j - 1) continue;
      if (pilih === null || Math.abs(kumulatif[i] - sasaran) < Math.abs(kumulatif[pilih] - sasaran)) pilih = i;
    }
    if (pilih === null) break;
    potong.push(pilih);
  }

  const keluar = [];
  let mulai = 0;
  for (const i of potong.concat([kalimat.length - 1])) {
    keluar.push(kalimat.slice(mulai, i + 1).join(' '));
    mulai = i + 1;
  }
  return keluar;
}

/* =====================================================================
   PEMBACAAN SUARA (text-to-speech)
   =====================================================================

   Suaranya memakai Web Speech API bawaan browser, bukan berkas audio yang
   dibangkitkan pipeline. Alasannya biaya: brief ini ~14.000 karakter prosa
   per hari, dan TTS berbayar termurah pun menambah beberapa dolar sebulan ke
   anggaran yang totalnya $10 — untuk sesuatu yang sudah tersedia gratis di
   perangkat pembaca. Konsekuensinya kualitas suara mengikuti mesin TTS di
   perangkat masing-masing, dan itu pertukaran yang diterima sadar.

   Pekerjaan yang sesungguhnya BUKAN memutar suaranya, tapi menyiapkan
   teksnya. Prosa brief ini padat angka pasar dan akronim, dan mesin TTS
   membacanya apa adanya:

     "$77.614"   -> "dolar tujuh puluh tujuh koma enam ratus empat belas"
     "0,93%"     -> "nol koma sembilan tiga persen"  (ini kebetulan benar)
     "EMA20"     -> "ema dua puluh"
     "$75.559–$78.065" -> dua angka yang berdempet tanpa jeda
     "~$77.024"  -> tilde dibaca atau ditelan, keduanya salah

   Yang pertama fatal: titik ribuan gaya Indonesia dibaca sebagai titik
   desimal, jadi harga Bitcoin terdengar seperti angka tujuh puluh tujuh.
   Karena itu seluruh angka diubah lebih dulu jadi bentuk yang memang
   diucapkan orang ("77 ribu 614 dolar"), bukan diserahkan ke mesin TTS.

   Ini mengikuti pola yang sudah dipegang proyek ini di src/utils/istilah.py:
   KODE yang merapikan teks secara deterministik, bukan model, dan bukan
   harapan bahwa mesin di seberang sana kebetulan menebak benar. */

/* Akronim yang salah dibaca kalau dibiarkan utuh. Dua perlakuan:
   dieja per huruf (dipisah spasi) atau diganti kata penuh. */
const AKRONIM_SUARA = {
  // Diganti kata penuh: bentuk ejaannya justru lebih membingungkan.
  BTC: 'Bitcoin',
  AS: 'Amerika Serikat',
  OI: 'open interest',
  BB: 'Bollinger Band',
  PDB: 'produk domestik bruto',
  GDP: 'produk domestik bruto',
  // Dieja per huruf.
  EMA: 'E M A', RSI: 'R S I', MACD: 'M A C D', ATR: 'A T R', OBV: 'O B V',
  VWAP: 'V W A P', DVOL: 'D V O L', DXY: 'D X Y', VIX: 'V I X',
  ETF: 'E T F', FOMC: 'F O M C', CPI: 'C P I', NFP: 'N F P', PCE: 'P C E',
  MVRV: 'M V R V', NVT: 'N V T', UST: 'U S T', CEO: 'C E O',
};

/* Angka -> bentuk yang diucapkan orang Indonesia.

   Bukan sekadar menghapus titik ribuan: "77614" dibaca mesin TTS sebagai
   deret yang panjang dan sulit ditangkap sambil menyetir atau berjalan.
   "77 ribu 614" adalah cara orang benar-benar menyebut harga. */
function angkaTerbilang(nilai) {
  if (!Number.isFinite(nilai)) return '';
  if (nilai < 0) return 'minus ' + angkaTerbilang(-nilai);

  const bulat = Math.floor(nilai);
  const pecahan = nilai - bulat;

  const bagian = [];
  let sisa = bulat;
  for (const [batas, nama] of [[1e12, 'triliun'], [1e9, 'miliar'], [1e6, 'juta'], [1e3, 'ribu']]) {
    if (sisa >= batas) {
      bagian.push(Math.floor(sisa / batas) + ' ' + nama);
      sisa = sisa % batas;
    }
  }
  if (sisa > 0 || !bagian.length) bagian.push(String(sisa));

  let hasil = bagian.join(' ');
  if (pecahan > 0) {
    // Desimal dibulatkan ke dua angka — presisi di luar itu tidak menambah
    // apa pun saat didengar — lalu diucapkan DIGIT PER DIGIT.
    //
    // Digit per digit bukan gaya-gayaan: itu memang cara angka desimal
    // dibaca dalam bahasa Indonesia ("nol koma sembilan tiga", bukan "nol
    // koma sembilan puluh tiga"), dan sekaligus menutup jebakan angka
    // kecil. Tanpa padding dua digit, 0,05 dibulatkan jadi "5" lalu
    // terdengar sebagai "nol koma lima" — sepuluh kali lipat nilainya.
    let desimal = String(Math.round(pecahan * 100)).padStart(2, '0');
    desimal = desimal.replace(/0$/, '');   // 0,50 -> "koma 5", bukan "koma 5 0"
    if (desimal) hasil += ' koma ' + desimal.split('').join(' ');
  }
  return hasil;
}

/* "77.614" / "1,92" (gaya Indonesia) -> Number. */
function _uraiAngkaID(teks) {
  const bersih = String(teks).replace(/\./g, '').replace(',', '.');
  const nilai = Number(bersih);
  return Number.isFinite(nilai) ? nilai : null;
}

const _SATUAN_BESAR = '(?:\\s+(triliun|miliar|milyar|juta|ribu))?';

/* Ubah satu blok prosa jadi teks yang enak didengar.

   Urutannya penting: rentang harga diproses SEBELUM mata uang tunggal,
   supaya "$75.559–$78.065" tidak terpecah jadi dua angka tanpa kata
   penghubung. */
function untukSuara(teks) {
  let s = String(teks || '');
  if (!s.trim()) return '';

  // Tilde "sekitar" — dibaca atau ditelan mesin TTS, dua-duanya salah.
  s = s.replace(/~\s*/g, 'sekitar ');

  // Rentang mata uang: "$75.559–$78.065" -> "... sampai ...".
  //
  // Pola angkanya WAJIB berakhir di digit (`[\d.,]*\d`, bukan `[\d.,]+`).
  // Dengan `+` yang rakus, "$78.065." di ujung kalimat ikut menelan titik
  // penutupnya — kalimat berikutnya lalu menyambung tanpa jeda, dan
  // pecahUcapan() kehilangan batas kalimat untuk memotong.
  s = s.replace(
    new RegExp('\\$\\s*([\\d.,]*\\d)' + _SATUAN_BESAR + '\\s*[–—-]\\s*\\$\\s*([\\d.,]*\\d)' + _SATUAN_BESAR, 'g'),
    (cocok, a, sa, b, sb) => {
      const na = _uraiAngkaID(a); const nb = _uraiAngkaID(b);
      if (na === null || nb === null) return cocok;
      return `${angkaTerbilang(na)}${sa ? ' ' + sa : ''} sampai ${angkaTerbilang(nb)}${sb ? ' ' + sb : ''} dolar`;
    },
  );

  // Mata uang tunggal. Satuan besar (juta/miliar) tetap di antara angka dan
  // "dolar", karena itu urutan yang diucapkan: "1 koma 92 miliar dolar".
  s = s.replace(
    new RegExp('\\$\\s*([\\d.,]*\\d)' + _SATUAN_BESAR, 'g'),
    (cocok, angka, satuan) => {
      const nilai = _uraiAngkaID(angka);
      if (nilai === null) return cocok;
      return `${angkaTerbilang(nilai)}${satuan ? ' ' + satuan : ''} dolar`;
    },
  );

  // Rentang persen sebelum persen tunggal, dengan alasan yang sama.
  s = s.replace(/([\d.,]*\d)\s*[–—]\s*([\d.,]*\d)\s*%/g, (cocok, a, b) => {
    const na = _uraiAngkaID(a); const nb = _uraiAngkaID(b);
    if (na === null || nb === null) return cocok;
    return `${angkaTerbilang(na)} sampai ${angkaTerbilang(nb)} persen`;
  });
  s = s.replace(/([\d.,]*\d)\s*%/g, (cocok, angka) => {
    const nilai = _uraiAngkaID(angka);
    return nilai === null ? cocok : `${angkaTerbilang(nilai)} persen`;
  });

  // Angka telanjang yang masih memakai titik ribuan. Dibatasi pada yang
  // BENAR-BENAR berpola ribuan (titik diikuti tepat tiga digit), supaya
  // penomoran biasa dan akhir kalimat tidak ikut tersentuh.
  s = s.replace(/\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b/g, (cocok) => {
    const nilai = _uraiAngkaID(cocok);
    return nilai === null ? cocok : angkaTerbilang(nilai);
  });

  // Akronim. Yang berekor angka (EMA20, EMA200) diberi jeda sebelum
  // angkanya, kalau tidak terdengar menyatu jadi satu kata.
  const kunci = Object.keys(AKRONIM_SUARA).sort((a, b) => b.length - a.length);
  s = s.replace(new RegExp('\\b(' + kunci.join('|') + ')(\\d*)\\b', 'g'),
    (cocok, akr, angka) => AKRONIM_SUARA[akr] + (angka ? ' ' + angka : ''));

  // Rentang angka telanjang dan tanda hubung panjang jadi jeda yang wajar.
  s = s.replace(/(\d)\s*[–—]\s*(\d)/g, '$1 sampai $2');
  s = s.replace(/\s*[–—]\s*/g, ', ');

  // Sisa simbol yang tidak punya bunyi.
  s = s.replace(/[«»"'"'`]/g, '');
  s = s.replace(/\s*\/\s*/g, ' atau ');
  s = s.replace(/&/g, ' dan ');

  return s.replace(/\s+/g, ' ').trim();
}

/* Pecah jadi potongan pendek untuk diucapkan satu per satu.

   Bukan sekadar kerapian: Chrome memotong utterance yang panjang di sekitar
   detik ke-15 dan berhenti diam-diam di tengah kalimat. Memecahnya per
   kalimat membuat tiap potongan jauh di bawah ambang itu, dan sekaligus
   memberi titik berhenti yang rapi saat pembaca menekan jeda. */
function pecahUcapan(teks, maks = 220) {
  const bersih = String(teks || '').trim();
  if (!bersih) return [];

  const kalimat = bersih.split(/(?<=[.!?])\s+/).filter(Boolean);
  const keluar = [];
  for (const k of kalimat) {
    if (k.length <= maks) { keluar.push(k); continue; }
    // Kalimat yang tetap kepanjangan dipecah di koma, lalu di spasi.
    let sisa = k;
    while (sisa.length > maks) {
      let potong = sisa.lastIndexOf(', ', maks);
      if (potong < maks * 0.4) potong = sisa.lastIndexOf(' ', maks);
      if (potong <= 0) potong = maks;
      keluar.push(sisa.slice(0, potong + 1).trim());
      sisa = sisa.slice(potong + 1).trim();
    }
    if (sisa) keluar.push(sisa);
  }
  return keluar;
}

/* Angka gaya Indonesia: titik ribuan, koma desimal. */
function formatAngka(nilai, desimal = 2) {
  if (nilai === null || nilai === undefined || Number.isNaN(nilai)) return '—';
  return Number(nilai).toLocaleString('id-ID', {
    minimumFractionDigits: desimal,
    maximumFractionDigits: desimal,
  });
}

function briefApp() {
  return {
    data: null,
    memuat: true,
    error: '',
    gelap: document.documentElement.classList.contains('dark'),
    filterKategori: '',
    filterSentimen: '',
    // Default 'besar': agenda 30 hari bisa panjang dan sebagian besar isinya
    // dampaknya kecil/sedang — yang layak disorot duluan cuma yang besar.
    // Filter ini membuka opsi melihat semuanya kalau memang perlu.
    filterDampakAgenda: 'besar',
    daftarArsip: [],
    arsipDipilih: '',
    // Perbandingan dua arsip: dibaca terpisah dari `data` (arsip yang
    // sedang ditampilkan), jadi tidak mengganggu tampilan utama.
    arsipBanding: '',
    dataBanding: null,
    memuatBanding: false,
    // Telemetri lintas hari (riwayat siaga, kesehatan run). Dimuat terpisah
    // dari brief dan boleh absen.
    telemetri: null,
    // Berita dan pernyataan tokoh berbagi satu bagian dengan dua tab.
    tabKonten: 'berita',
    halamanBerita: 1,
    halamanPernyataan: 1,
    halamanAgenda: 1,
    perHalaman: 3,
    perHalamanAgenda: 5,
    grafik: null,
    // -- pembacaan suara --------------------------------------------
    // `suaraDidukung` menampung DUA syarat sekaligus: browsernya punya Web
    // Speech API, DAN perangkatnya benar-benar punya suara berbahasa
    // Indonesia. Tanpa syarat kedua, menekan tombolnya menghasilkan brief
    // berbahasa Indonesia yang dibacakan dengan fonetik Inggris — lebih
    // buruk daripada tidak ada tombolnya sama sekali.
    suaraDidukung: false,
    suaraStatus: 'diam',    // diam | main | jeda
    suaraJudul: '',
    suaraKecepatan: 1,
    suaraGalat: '',
    _suaraAntre: [],
    _suaraIndeks: 0,
    _jam: null,
    _detak: 0,          // dinaikkan tiap menit supaya waktu relatif ikut menyegar
    tampilKeAtas: false,
    _padaGulir: null,

    // ---------------------------------------------------------------
    // Siklus hidup
    // ---------------------------------------------------------------
    async mulai() {
      await this.muat();
      await this.muatArsip();
      await this.muatTelemetri();
      // Waktu relatif ("3 jam lalu") perlu dihitung ulang berkala.
      this._jam = setInterval(() => { this._detak++; }, 60000);

      // Tombol "kembali ke atas". Ambangnya relatif terhadap tinggi jendela,
      // bukan angka piksel mati: 600 px berarti satu layar penuh di ponsel
      // tapi baru dua pertiga layar di desktop, sehingga tombolnya muncul
      // pada saat yang terasa berbeda di tiap perangkat.
      //
      // Listener-nya `passive` supaya tidak menahan gulir — halaman ini
      // panjang dan digulir jauh, jadi handler yang memblokir langsung
      // terasa sebagai gulir yang tersendat.
      this._padaGulir = () => {
        this.tampilKeAtas = window.scrollY > window.innerHeight * 0.8;
      };
      window.addEventListener('scroll', this._padaGulir, { passive: true });
      this._padaGulir();   // halaman bisa dibuka dalam keadaan sudah tergulir

      this._siapkanSuara();
    },

    /* Deteksi dukungan suara Indonesia, dan hentikan bacaan saat pindah halaman.

       Daftar suara diperiksa DUA KALI: sekali sekarang, sekali lagi setelah
       `voiceschanged`. Chrome memulangkan daftar kosong pada pemanggilan
       pertama dan baru mengisinya belakangan secara asinkron — memeriksa
       sekali saja berarti tombolnya tidak pernah muncul di Chrome, browser
       yang justru paling banyak dipakai pembaca. */
    _siapkanSuara() {
      if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') return;

      const periksa = () => { this.suaraDidukung = !!this._suaraID(); };
      periksa();
      window.speechSynthesis.addEventListener?.('voiceschanged', periksa);

      try {
        const simpan = Number(localStorage.getItem('suara_kecepatan'));
        if (simpan >= 0.5 && simpan <= 2) this.suaraKecepatan = simpan;
      } catch (e) { /* localStorage diblokir */ }

      // Bacaan yang masih berbunyi saat tab ditutup akan TERUS berbunyi:
      // speechSynthesis hidup di level browser, bukan halaman. Tanpa ini,
      // pembaca yang menutup tab di tengah brief harus mencari sendiri dari
      // mana suaranya datang.
      window.addEventListener('beforeunload', () => window.speechSynthesis.cancel());
    },

    /* Gulir balik ke puncak. <html> memakai scroll-smooth, jadi animasinya
       datang gratis — kecuali bagi pengguna yang meminta gerakan dikurangi,
       yang di sini dilompati langsung. */
    keAtas() {
      const kurangiGerak = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      window.scrollTo({ top: 0, left: 0, behavior: kurangiGerak ? 'instant' : 'smooth' });
    },

    async muat(berkas = 'data/latest.json') {
      this.memuat = true;
      this.error = '';
      try {
        const resp = await fetch(`${berkas}?t=${Date.now()}`, { cache: 'no-store' });
        if (!resp.ok) throw new Error(`Berkas data tidak ditemukan (HTTP ${resp.status}).`);
        const isi = await resp.json();
        if (!isi || !isi.price) throw new Error('Format datanya tidak dikenali.');
        this.data = isi;
      } catch (e) {
        this.data = null;
        this.error = e.message || 'Gagal memuat data.';
      } finally {
        this.memuat = false;
        this.$nextTick(() => {
          this.gambarIkon();
          this.gambarGrafik();
        });
      }
    },

    /* Telemetri lintas hari. Berkas TERPISAH dari brief karena umurnya
       berbeda: brief menggambarkan satu hari, telemetri menyimpan riwayat
       yang selamat dari pemangkasan arsip. Kegagalannya sengaja senyap —
       halaman tetap utuh tanpa bagian riwayat kalau berkasnya belum ada. */
    async muatTelemetri() {
      try {
        const resp = await fetch(`data/telemetri.json?t=${Date.now()}`, { cache: 'no-store' });
        if (!resp.ok) return;
        this.telemetri = await resp.json();
      } catch (e) {
        this.telemetri = null;
      }
    },

    /* Riwayat siaga, terbaru dulu, dibatasi supaya footernya tidak jadi
       daftar sepanjang halaman. */
    get riwayatSiaga() {
      const r = this.telemetri?.riwayat_siaga || [];
      return [...r].reverse().slice(0, 12);
    },

    async muatArsip() {
      try {
        const resp = await fetch(`data/index.json?t=${Date.now()}`, { cache: 'no-store' });
        if (!resp.ok) return;
        const idx = await resp.json();
        this.daftarArsip = (idx.items || []).slice(0, 60);
      } catch (e) {
        this.daftarArsip = [];
      }
    },

    bukaArsip() {
      this.muat(this.arsipDipilih ? `data/${this.arsipDipilih}` : 'data/latest.json');
    },

    /* Bandingkan arsip yang sedang tampil (this.data) dengan arsip lain,
       dimuat TERPISAH supaya tidak mengganggu tampilan utama maupun grafik. */
    async muatBanding() {
      if (!this.arsipBanding) { this.dataBanding = null; return; }
      this.memuatBanding = true;
      try {
        const resp = await fetch(`data/${this.arsipBanding}?t=${Date.now()}`, { cache: 'no-store' });
        if (!resp.ok) throw new Error('gagal memuat');
        this.dataBanding = await resp.json();
      } catch (e) {
        this.dataBanding = null;
      } finally {
        this.memuatBanding = false;
      }
    },

    /* Baris perbandingan numerik antara arsip aktif dan arsip pembanding.
       Cuma metrik yang paling sering dicari saat membandingkan dua hari —
       bukan seluruh isi brief, supaya tabelnya tetap ringkas dan terbaca. */
    get barisBanding() {
      const a = this.data, b = this.dataBanding;
      if (!a || !b) return [];
      const ambil = (obj, path) => path.split('.').reduce((o, k) => (o == null ? null : o[k]), obj);
      const daftar = [
        ['Harga', 'price.last', (v) => this.uang(v)],
        ['Perubahan 24j', 'price.change_24h_pct', (v) => this.persen(v, 2)],
        ['Sentimen berita', 'aggregate.sentiment_score', (v) => this.angka(v, 1)],
        ['Fear & Greed', 'market.fear_greed.value', (v) => this.angka(v, 0)],
        ['Funding rate', 'market.funding_rate', (v) => this.tekstFunding(v)],
        ['Open interest', 'market.open_interest', (v) => this.angka(v, 0) + ' BTC'],
        ['DVOL', 'options.dvol', (v) => this.angka(v, 1)],
        ['Dominasi BTC', 'market.btc_dominance_pct', (v) => this.angka(v, 1) + '%'],
        ['MVRV', 'onchain.mvrv', (v) => this.angka(v, 2)],
      ];
      return daftar
        .map(([label, path, format]) => {
          const nilaiA = ambil(a, path);
          const nilaiB = ambil(b, path);
          if (nilaiA === null || nilaiA === undefined || nilaiB === null || nilaiB === undefined) return null;
          const delta = typeof nilaiA === 'number' && typeof nilaiB === 'number' ? nilaiA - nilaiB : null;
          return { label, a: format(nilaiA), b: format(nilaiB), warnaDelta: delta === null ? '' : this.warnaAngka(delta) };
        })
        .filter(Boolean);
    },

    gambarIkon() {
      if (window.lucide) window.lucide.createIcons();
    },

    // ---------------------------------------------------------------
    // Tema
    // ---------------------------------------------------------------
    gantiTema() {
      this.gelap = !this.gelap;
      document.documentElement.classList.toggle('dark', this.gelap);
      try {
        localStorage.setItem('theme', this.gelap ? 'dark' : 'light');
      } catch (e) { /* localStorage diblokir: pilihan tidak tersimpan */ }
      this.$nextTick(() => {
        this.gambarIkon();
        this.gambarGrafik();   // warna grafik ikut tema
      });
    },

    // ---------------------------------------------------------------
    // Grafik harga
    // ---------------------------------------------------------------
    gambarGrafik() {
      const kanvas = document.getElementById('grafikHarga');
      if (!kanvas || !this.data || !window.Chart) return;

      const deret = this.data.price_series || [];
      if (this.grafik) { this.grafik.destroy(); this.grafik = null; }
      if (!deret.length) return;

      const naik = deret[deret.length - 1].c >= deret[0].c;
      const warna = naik ? '#10b981' : '#f43f5e';
      const kisi = this.gelap ? 'rgba(148,163,184,0.15)' : 'rgba(100,116,139,0.15)';
      const teks = this.gelap ? '#94a3b8' : '#64748b';

      const isian = kanvas.getContext('2d').createLinearGradient(0, 0, 0, 160);
      isian.addColorStop(0, naik ? 'rgba(16,185,129,0.25)' : 'rgba(244,63,94,0.25)');
      isian.addColorStop(1, 'rgba(0,0,0,0)');

      const datasets = [{
        label: 'Harga',
        data: deret.map((d) => d.c),
        borderColor: warna,
        backgroundColor: isian,
        borderWidth: 2,
        fill: true,
        tension: 0.25,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: warna,
      }];

      // Support/resistance terdekat digambar sebagai garis putus-putus datar
      // di atas grafik harga: sebelumnya cuma angka telanjang di kartu
      // sebelah, sekarang pembaca langsung lihat "harga lagi di mana relatif
      // ke level" tanpa mencocokkan dua angka secara mental.
      const levelKunci = this.data.technical?.key_levels || {};
      const support = levelKunci.support?.[0];
      const resistance = levelKunci.resistance?.[0];
      const garisLevel = (label, nilai, warnaGaris) => ({
        label,
        data: deret.map(() => nilai),
        borderColor: warnaGaris,
        borderWidth: 1,
        borderDash: [5, 4],
        pointRadius: 0,
        pointHitRadius: 0,
        fill: false,
        tension: 0,
      });
      if (support) datasets.push(garisLevel('Support', support, 'rgba(16,185,129,0.65)'));
      if (resistance) datasets.push(garisLevel('Resistance', resistance, 'rgba(244,63,94,0.65)'));

      this.grafik = new Chart(kanvas, {
        type: 'line',
        data: {
          labels: deret.map((d) => this.tanggalSingkat(d.t)),
          datasets,
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  const nama = ctx.dataset.label !== 'Harga' ? `${ctx.dataset.label} ` : '';
                  return `${nama}$${formatAngka(ctx.parsed.y, 0)}`;
                },
              },
            },
          },
          scales: {
            x: { grid: { display: false }, ticks: { color: teks, maxTicksLimit: 6, font: { size: 10 } } },
            y: {
              grid: { color: kisi },
              ticks: {
                color: teks,
                font: { size: 10 },
                callback: (v) => `$${formatAngka(v, 0)}`,
              },
            },
          },
        },
      });
    },

    // ---------------------------------------------------------------
    // Format
    // ---------------------------------------------------------------
    angka(nilai, desimal = 2) { return formatAngka(nilai, desimal); },

    uang(nilai) {
      if (nilai === null || nilai === undefined) return '—';
      return `$${formatAngka(nilai, 0)}`;
    },

    ringkasUang(nilai, pakaiTanda = false) {
      if (nilai === null || nilai === undefined) return '—';
      const tanda = pakaiTanda && nilai > 0 ? '+' : (nilai < 0 ? '-' : '');
      const abs = Math.abs(nilai);
      if (abs >= 1e12) return `${tanda}$${formatAngka(abs / 1e12, 2)} triliun`;
      if (abs >= 1e9) return `${tanda}$${formatAngka(abs / 1e9, 2)} miliar`;
      if (abs >= 1e6) return `${tanda}$${formatAngka(abs / 1e6, 1)} jt`;
      if (abs >= 1e3) return `${tanda}$${formatAngka(abs / 1e3, 1)} rb`;
      return `${tanda}$${formatAngka(abs, 0)}`;
    },

    /* Statistik run untuk footer: token yang dihabiskan dan lama proses.
       Biaya sengaja tidak ikut — angkanya tidak berarti apa-apa bagi
       pembaca, dan yang membayar sudah bisa melihatnya di latest.json. */
    get statistikRun() {
      const q = this.data?.data_quality;
      if (!q) return [];
      const baris = [];
      if (q.llm_token_total) {
        const rincian = (q.llm_token_masuk && q.llm_token_keluar)
          ? ` (${formatAngka(q.llm_token_masuk, 0)} masuk / ${formatAngka(q.llm_token_keluar, 0)} keluar)`
          : '';
        baris.push({ label: 'Token AI', nilai: formatAngka(q.llm_token_total, 0) + rincian });
      }
      if (q.durasi_detik) {
        const d = q.durasi_detik;
        const nilai = d < 60
          ? `${formatAngka(d, 1)} detik`
          : `${Math.floor(d / 60)} menit ${formatAngka(d % 60, 0)} detik`;
        baris.push({ label: 'Lama proses', nilai });
      }
      /* Corong berita: berapa yang ditarik dari seluruh feed (kotor) vs
         berapa yang akhirnya lolos saringan dan dipakai. Angka ini yang
         menunjukkan apakah menambah feed benar-benar menambah bahan atau
         cuma menambah derau yang tetap dibuang di langkah filter. */
      const c = q.berita_corong;
      if (c && c.terkumpul) {
        const dipakai = c.dipakai ?? c.unik;
        baris.push({
          label: 'Berita terkumpul',
          nilai: `${formatAngka(c.terkumpul, 0)} artikel`,
        });
        if (dipakai !== null && dipakai !== undefined) {
          const persen = c.terkumpul ? (dipakai / c.terkumpul * 100) : 0;
          baris.push({
            label: 'Lolos saringan',
            nilai: `${formatAngka(dipakai, 0)} artikel (${formatAngka(persen, 1)}%)`,
          });
        }
      }
      return baris;
    },

    /* Nama bot untuk tombol berlangganan. Sengaja TIDAK bergantung pada
       `data`: tombolnya harus tetap tampil walau latest.json belum ada. */
    get botTelegram() {
      return this.data?.bot_telegram || BOT_TELEGRAM;
    },

    /* Funding rate kerap sangat kecil; dibulatkan biasa bisa tampil "0,0000%"
       yang kelihatan seperti bug padahal angkanya memang benar. */
    tekstFunding(nilai) {
      if (nilai === null || nilai === undefined) return '—';
      const persenNilai = nilai * 100;
      if (Math.abs(persenNilai) < 0.00005) return 'mendekati 0% (netral)';
      return this.persen(persenNilai, 4);
    },

    /* Funding SATU TITIK nyaris tidak berarti — yang membedakan sinyal kuat
       dari derau adalah sudah berapa lama bertahan di sisi yang sama. */
    labelPersistensiFunding(jam) {
      if (!jam) return '';
      if (jam < 24) return `bertahan ${jam} jam`;
      const hari = Math.round(jam / 24 * 10) / 10;
      return `bertahan ~${hari} hari`;
    },

    persen(nilai, desimal = 2) {
      if (nilai === null || nilai === undefined) return '—';
      const tanda = nilai > 0 ? '+' : '';
      return `${tanda}${formatAngka(nilai, desimal)}%`;
    },

    warnaAngka(nilai) {
      if (nilai === null || nilai === undefined || nilai === 0) return 'text-slate-500 dark:text-slate-400';
      return nilai > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400';
    },

    kelasSentimen(sentimen) {
      if (sentimen === 'bullish') return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300';
      if (sentimen === 'bearish') return 'bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-300';
      return 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300';
    },

    kelasKeyakinan(tingkat) {
      if (tingkat === 'tinggi') return 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900';
      if (tingkat === 'sedang') return 'bg-slate-200 text-slate-700 dark:bg-slate-600 dark:text-slate-200';
      return 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400';
    },

    kelasWaspada(tingkat) {
      if (tingkat === 'tinggi') return 'bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-300';
      if (tingkat === 'sedang') return 'bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300';
      return 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300';
    },

    panahArah(arah) {
      return { naik: '↑', turun: '↓' }[arah] || '·';
    },

    warnaArah(arah) {
      if (arah === 'naik') return 'text-emerald-600 dark:text-emerald-400';
      if (arah === 'turun') return 'text-rose-600 dark:text-rose-400';
      return 'text-slate-400';
    },

    labelDivergensi(label) {
      return {
        whale_distribusi: 'Whale lebih defensif dari ritel',
        whale_akumulasi: 'Whale lebih agresif dari ritel',
        selaras: 'Posisi whale dan ritel selaras',
      }[label] || label || '—';
    },

    labelPola(jenis) {
      return {
        sapuan_likuiditas_atas: 'Sapuan likuiditas di atas',
        sapuan_likuiditas_bawah: 'Sapuan likuiditas di bawah',
        penolakan_atas: 'Penolakan di area atas',
        penolakan_bawah: 'Penolakan di area bawah',
        absorpsi_volume: 'Absorpsi volume',
        breakout_volume_lemah: 'Breakout dengan volume lemah',
        posisi_padat: 'Posisi derivatif padat',
      }[jenis] || (jenis || '').replace(/_/g, ' ');
    },

    labelStatus(status) {
      return {
        verbatim: 'pernyataan langsung',
        dilaporkan_media: 'dilaporkan media',
        rumor: 'rumor',
      }[status] || status || 'tidak jelas';
    },

    kelasStatusPernyataan(status) {
      if (status === 'verbatim') return 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900';
      if (status === 'dilaporkan_media') return 'bg-slate-200 text-slate-700 dark:bg-slate-600 dark:text-slate-200';
      return 'bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300';
    },

    /* "kekuatan 4" tidak berarti apa-apa bagi pembaca umum; kata-katanya
       disamakan dengan yang dipakai di pesan Telegram. */
    labelDampak(kekuatan) {
      return {
        1: 'dampak kecil', 2: 'dampak terbatas', 3: 'dampak sedang',
        4: 'dampak besar', 5: 'dampak sangat besar',
      }[kekuatan] || '';
    },

    /* Relevansi agenda ke kripto (1-5) dalam bahasa manusia — bukan angka
       telanjang, yang tidak berarti apa-apa tanpa membaca dokumentasi. */
    labelRelevansiAgenda(nilai) {
      return {
        1: 'dampak minim', 2: 'dampak terbatas', 3: 'dampak sedang',
        4: 'dampak besar', 5: 'dampak sangat besar',
      }[nilai] || '';
    },

    kelasRelevansiAgenda(nilai) {
      if (nilai >= 4) return 'bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300';
      if (nilai === 3) return 'bg-slate-200 text-slate-700 dark:bg-slate-600 dark:text-slate-200';
      return 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400';
    },

    /* "dua_arah" adalah jawaban yang paling sering benar untuk rilis data:
       arahnya tergantung angka yang keluar, bukan acaranya sendiri. */
    labelArahAgenda(arah) {
      return {
        naik: 'cenderung mengangkat harga',
        turun: 'cenderung menekan harga',
        dua_arah: 'arah tergantung hasilnya',
      }[arah] || '';
    },

    labelZona(zona) {
      return { jenuh_beli: 'jenuh beli', jenuh_jual: 'jenuh jual', netral: 'netral' }[zona] || zona || '';
    },

    /* "priced in" sengaja TETAP Inggris — istilah pasar yang sudah lazim,
       dan menerjemahkannya ("tercermin di harga") justru bikin bingung
       pembaca yang sudah biasa dengan istilah aslinya. Yang diperbaiki
       cuma tata bahasanya: dulu dirender mentah "priced in: ya". */
    labelPricedIn(nilai) {
      return {
        ya: 'sudah priced in',
        tidak: 'belum priced in',
        sebagian: 'sebagian priced in',
      }[nilai] || '';
    },

    tanggalSingkat(ms) {
      const d = keWIB(new Date(ms));
      return `${d.getDate()} ${BULAN_SINGKAT_ID[d.getMonth()]}`;
    },

    waktuSingkat(iso) {
      if (!iso) return '—';
      const asli = new Date(iso);
      if (Number.isNaN(asli.getTime())) return '—';
      const d = keWIB(asli);
      const jam = String(d.getHours()).padStart(2, '0');
      const menit = String(d.getMinutes()).padStart(2, '0');
      return `${d.getDate()} ${BULAN_SINGKAT_ID[d.getMonth()]} ${jam}:${menit} WIB`;
    },

    /* Agenda yang jatuh dalam waktu dekat perlu dibedakan dari yang masih
       berhari-hari lagi. "7 hari lagi" dan "3 jam lagi" tampil dengan chip
       kelabu yang sama persis — padahal cuma yang kedua yang menuntut
       perhatian hari ini.

       Ambangnya 12 jam, bukan 24: brief terbit pagi, dan acara yang jatuh
       malam nanti masih di hari yang sama bagi pembaca. Lewat 12 jam,
       sebagian besar acara sudah berada di "besok" — dan menyebutnya
       berlangsung hari ini justru keliru. */
    agendaHariIni(jamLagi) {
      return typeof jamLagi === 'number' && jamLagi >= 0 && jamLagi <= 12;
    },

    /* Kelas chip hitung mundur: menyala hanya untuk yang benar-benar dekat. */
    kelasHitungMundur(jamLagi) {
      if (!this.agendaHariIni(jamLagi)) {
        return 'bg-slate-100 dark:bg-slate-700';
      }
      return jamLagi <= 3
        ? 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300 font-semibold'
        : 'bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-200 font-semibold';
    },

    hitungMundur(jamLagi) {
      if (jamLagi === null || jamLagi === undefined) return '';
      if (jamLagi < 1) return `${Math.max(1, Math.round(jamLagi * 60))} menit lagi`;
      if (jamLagi < 24) return `${Math.round(jamLagi)} jam lagi`;
      return `${Math.round(jamLagi / 24)} hari lagi`;
    },

    // ---------------------------------------------------------------
    // Turunan
    // ---------------------------------------------------------------
    get waktuRelatif() {
      this._detak; // dependensi supaya Alpine menghitung ulang tiap menit
      if (!this.data?.generated_at) return '';
      const dibuat = new Date(this.data.generated_at);
      if (Number.isNaN(dibuat.getTime())) return '';
      const menit = Math.floor((Date.now() - dibuat.getTime()) / 60000);
      if (menit < 1) return 'baru saja';
      if (menit < 60) return `${menit} menit lalu`;
      const jam = Math.floor(menit / 60);
      if (jam < 24) return `${jam} jam lalu`;
      return `${Math.floor(jam / 24)} hari lalu`;
    },

    /* Umur brief dalam jam. Dihitung ulang tiap menit lewat `_detak`, sama
       seperti waktu relatif — halaman ini bisa dibiarkan terbuka berjam-jam,
       dan brief yang masih segar saat dibuka bisa jadi basi sebelum ditutup. */
    get umurBriefJam() {
      this._detak;
      if (!this.data?.generated_at) return null;
      const dibuat = new Date(this.data.generated_at);
      if (Number.isNaN(dibuat.getTime())) return null;
      return (Date.now() - dibuat.getTime()) / 3600000;
    },

    /* Ambangnya 36 jam, bukan 24: brief terbit sekali sehari dan cron GitHub
       kerap tertunda, jadi 26 jam masih normal. Lewat 36 jam berarti
       setidaknya satu jadwal terbit benar-benar terlewat. */
    get briefBasi() {
      return (this.umurBriefJam ?? 0) > 36;
    },

    get kelasUmurBrief() {
      if (!this.briefBasi) return 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300';
      // Di atas tiga hari nadanya naik dari "perhatikan" jadi "jangan
      // dipakai": harga sudah pasti bergerak jauh.
      if ((this.umurBriefJam ?? 0) > 72) {
        return 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300 font-semibold';
      }
      return 'bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-200 font-semibold';
    },

    /* Nama sumber gagal dalam bahasa manusia. Sebelumnya cuma tersembunyi di
       tooltip badge kualitas — tidak berguna di ponsel (tanpa hover) dan
       gampang terlewat bahkan di desktop. Sumber yang tidak masuk kamus
       (nama domain, dsb) diberi fallback generik: garis bawah/titik dua
       diganti spasi, huruf awal dikapital. */
    labelSumberGagal(kode) {
      const KAMUS = {
        etf_flow: 'Arus ETF harian', funding_oi: 'Funding rate / open interest',
        technical: 'Indikator teknikal', news: 'Berita', whale: 'Posisi whale/ritel',
        whale_posisi: 'Posisi whale', ritel_posisi: 'Posisi ritel', taker_flow: 'Rasio taker',
        macro: 'Data makro', fred: 'Data Fed (M2/neraca)', options: 'Data opsi Deribit',
        dvol: 'Indeks volatilitas opsi', onchain: 'Data on-chain', onchain_fees: 'Fee mempool',
        onchain_valuasi: 'Valuasi on-chain (MVRV/NVT)', fear_greed: 'Indeks Fear & Greed',
        flows: 'Aliran dana', premium_coinbase: 'Premium Coinbase', stablecoin: 'Kapitalisasi stablecoin',
        btc_dominance: 'Dominasi BTC', statements: 'Pernyataan tokoh', feed_resmi: 'Feed resmi Gedung Putih',
        google_news: 'Pencarian Google News',
      };
      if (KAMUS[kode]) return KAMUS[kode];
      if (kode.startsWith('truth_social:')) return `Truth Social @${kode.split(':')[1]}`;
      if (kode.startsWith('x_grok:')) return `X (Grok) @${kode.split(':')[1]}`;
      return kode.replace(/[_:]/g, ' ').replace(/^./, (c) => c.toUpperCase());
    },

    get sumberGagalTampil() {
      const kode = this.data?.data_quality?.failed_sources || [];
      return kode.map((k) => this.labelSumberGagal(k));
    },

    get kelasKualitas() {
      const c = this.data?.data_quality?.confidence;
      if (c === 'baik') return 'border-emerald-300 dark:border-emerald-700 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-800 dark:text-emerald-300';
      if (c === 'sedang') return 'border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/30 text-amber-800 dark:text-amber-300';
      return 'border-rose-300 dark:border-rose-700 bg-rose-50 dark:bg-rose-900/30 text-rose-800 dark:text-rose-300';
    },

    get kelasTitikKualitas() {
      const c = this.data?.data_quality?.confidence;
      if (c === 'baik') return 'bg-emerald-500';
      if (c === 'sedang') return 'bg-amber-500';
      return 'bg-rose-500';
    },

    /* Keterangan saat dua sumber arus ETF tidak sepakat. String kosong
       berarti tidak ada yang perlu dikatakan — kecocokan adalah keadaan
       normal, dan mengumumkannya tiap hari melatih pembaca mengabaikan baris
       ini justru pada hari ia berarti. */
    get etfBerbedaAntarSumber() {
      const v = this.data?.market?.etf_flow_verifikasi;
      if (!v || v.status !== 'berbeda') return '';
      const beda = v.tanggal_sama
        ? 'tanggalnya sama, jadi salah satunya kemungkinan keliru'
        : `tanggalnya berbeda (${v.pembanding_tanggal || 'tidak diketahui'})`;
      return `Sumber pembanding ${v.pembanding_sumber} menyebut `
           + `${this.ringkasUang(v.pembanding_usd, true)} — ${beda}.`;
    },

    /* Porsi likuidasi posisi beli dalam persen, untuk batang dua warna.
       Dibatasi 0..100 supaya data aneh tidak pernah menghasilkan batang yang
       meluber keluar kartunya. */
    get porsiLikuidasiLong() {
      const m = this.data?.market || {};
      const total = m.likuidasi_total_usd;
      if (!total) return 0;
      return Math.max(0, Math.min(100, (m.likuidasi_long_usd || 0) / total * 100));
    },

    /* Keterangan cakupan: bursa mana, dan berapa jam yang benar-benar
       terekam. Jendela yang lebih pendek dari 24 jam disebut apa adanya —
       riwayat bursa bisa saja tidak sampai sejauh itu. */
    get keteranganLikuidasi() {
      const m = this.data?.market || {};
      const jam = m.likuidasi_cakupan_jam;
      const cakupan = jam && jam < 23 ? `${this.angka(jam, 0)} jam terakhir` : '24 jam terakhir';
      return `${m.likuidasi_sumber || 'satu bursa'} · ${cakupan} · bukan gabungan seluruh bursa`;
    },

    /* Perubahan Fear & Greed dibanding pembacaan sebelumnya. Sumbernya sudah
       mengirim `previous`, jadi tidak ada yang dihitung ulang di sini — dan
       kalau field itu absen barisnya cuma hilang, bukan menampilkan "0 poin"
       yang terbaca seolah pasar tidak bergerak. */
    get selisihFearGreed() {
      const fg = this.data?.market?.fear_greed;
      if (!fg || fg.previous === null || fg.previous === undefined) return '';
      const selisih = fg.value - fg.previous;
      if (!selisih) return 'sama seperti kemarin';
      return `${selisih > 0 ? '+' : '−'}${Math.abs(selisih)} poin dari kemarin`;
    },

    get gayaBatangSentimen() {
      const skor = Math.max(-100, Math.min(100, this.data?.aggregate?.sentiment_score || 0));
      const lebar = Math.abs(skor) / 2;                 // 0..50 persen dari lebar total
      return skor >= 0
        ? `left:50%; width:${lebar}%`
        : `right:50%; width:${lebar}%`;
    },

    /* Brief harian memakai satu timeframe saja: candle harian. */
    get tfAktif() {
      return this.data?.technical?.['1d'] || null;
    },

    /* ===== Siaga kebijakan AS =====
       Alarm hanya muncul pada siaga sedang/tinggi. Pada siaga rendah panel
       ini DIAM — alarm yang menyala tiap hari akan diabaikan justru pada
       hari ia benar-benar berarti. */
    /* Siaga BERAKHIR begitu bursa yang ditunggunya buka.

       Versi sebelumnya menyisakan barisnya dengan label "JENDELA LEWAT",
       dengan alasan reaksi pembukaan itu sendiri layak diberitakan. Alasan
       itu tidak bertahan: barisnya tidak MENUNJUKKAN reaksi apa pun — ia
       cuma mengumumkan bahwa jendelanya sudah tutup. Kartu bernama Sorotan
       lalu dibuka oleh peristiwa yang sudah selesai dan tidak membawa kabar
       baru, sampai sepuluh jam lamanya kalau bursa buka malam dan brief
       berikutnya pagi.

       Yang lebih berbahaya ada di bawahnya: seluruh prosa bagian #s-siaga —
       pemicu, skenario, kalimat soal harga kripto bergerak sendirian — ditulis saat
       jendelanya masih terbuka. Membiarkan barisnya berarti mengundang
       pembaca ke uraian yang premisnya sudah gugur.

       Karena keduanya digerakkan getter ini, satu penjagaan di sini
       memadamkan baris dan bagian rinciannya sekaligus. */
    get siagaKebijakan() {
      this._detak;  // ikut menyegar tiap menit supaya padam tepat waktu
      const a = this.data?.agen_kebijakan;
      // Gerbangnya kini RISIKO WAKTU, bukan tingkat siaga kebijakan. Isi
      // kebijakannya sudah pindah ke analisa AI sebagai sebab naik/turun
      // harga, jadi panel ini tinggal mengurus satu hal: seberapa berbahaya
      // JAMNYA. Menampilkannya pada risiko rendah cuma jadi kotak yang
      // menyala tiap hari tanpa alasan.
      if (!a || !['sedang', 'tinggi'].includes(a.risiko_jendela?.tingkat)) return null;
      const buka = a.jendela?.buka_berikutnya_utc;
      if (a.jendela?.dalam_jendela_rawan && buka) {
        const t = new Date(buka);
        if (!Number.isNaN(t.getTime()) && t.getTime() <= Date.now()) return null;
      }
      return a;
    },

    /* Keterangan singkat kenapa risiko waktunya segitu — dirakit dari fase
       dan kerapuhan yang keduanya hitungan kode, jadi tidak bisa berbeda
       dari angka yang dipakai menilainya. */
    get labelRisikoWaktu() {
      const r = this.siagaKebijakan?.risiko_jendela;
      if (!r) return '';
      // Panel ini hanya hidup di dalam jendela rawan, jadi bagian pertama
      // selalu benar; kerapuhan menempel sebagai pengali kalau ada.
      const bagian = ['bursa AS tutup'];
      if (r.kerapuhan && r.kerapuhan !== 'rendah') bagian.push(`pasar rapuh (${r.kerapuhan})`);
      return 'Karena ' + bagian.join(', ') + '.';
    },

    /* "Keputusan menjelang akhir pekan" dalam satu kalimat. Angkanya datang
       dari hitungan kode di pipeline, bukan dari prosa model. */
    get kalimatPendaratan() {
      const p = this.siagaKebijakan?.pendaratan;
      if (!p?.ada_yang_tertahan) return '';
      const n = p.kuat_di_jendela_rawan;
      const dari = p.kuat > n ? ` dari ${p.kuat} sinyal kuat` : ' sinyal kuat';
      return `${n}${dari} mendarat saat pasar AS tidak bisa menyerapnya — `
           + 'efeknya masih menunggu, belum diserap arus institusi.';
    },

    /* ===== Hitung mundur HIDUP =====
       Dihitung ulang tiap menit dari instant absolut, bukan dibaca dari
       selisih yang dibekukan pipeline.

       Ini bukan kenyamanan, ini soal benar-salah. Brief dibuat sekali sehari
       lalu halamannya dibuka kapan saja: selisih yang dibekukan pukul 06.12
       masih akan menulis "7 jam lagi" pada pukul 18.00, dan tetap mengaku
       bursa tutup setelah bursa buka. Untuk halaman market outlook, angka
       yang membeku lebih berbahaya daripada tidak ada angka sama sekali.

       Telegram TIDAK memakai ini: pesan dibaca dekat waktu kirim, jadi
       snapshot di sana justru benar. */
    hitungMundurLive(iso) {
      this._detak; // dependensi supaya Alpine menyegarkan tiap menit
      if (!iso) return null;
      const t = new Date(iso);
      if (Number.isNaN(t.getTime())) return null;

      const detik = (t.getTime() - Date.now()) / 1000;
      if (detik <= 0) return { lewat: true, teks: 'sudah lewat', jam: 0 };

      const jam = detik / 3600;
      let teks;
      // Dibulatkan SEKALI ke satuan terkecil yang ditampilkan, lalu dipecah
      // dengan bagi-sisa. Membulatkan tiap komponen sendiri-sendiri
      // menghasilkan sisa yang tidak menyimpan: 2,9998 jam pernah keluar
      // sebagai "2 jam 60 menit lagi", dan 29,999 jam sebagai "1 hari 5 jam"
      // — hampir satu jam penuh hilang karena dibulatkan ke bawah.
      const totalMenit = Math.round(detik / 60);
      if (totalMenit < 60) {
        teks = `${Math.max(1, totalMenit)} menit lagi`;
      } else if (totalMenit < 1440) {
        const j = Math.floor(totalMenit / 60);
        const m = totalMenit % 60;
        teks = m ? `${j} jam ${m} menit lagi` : `${j} jam lagi`;
      } else {
        const totalJam = Math.round(detik / 3600);
        const h = Math.floor(totalJam / 24);
        const s = totalJam % 24;
        teks = s ? `${h} hari ${s} jam lagi` : `${h} hari lagi`;
      }
      return { lewat: false, teks, jam };
    },

    /* ===== Bar "Yang ditunggu" =====
       Siaga kebijakan dan agenda besar digabung karena keduanya menjawab
       pertanyaan yang sama: apa yang bisa menggerakkan pasar, dan kapan.
       Satu berupa jendela struktural (bursa tutup), satu berupa acara
       terjadwal — menaruhnya di satu garis waktu lebih jujur daripada dua
       kartu terpisah yang saling berebut perhatian.

       Barnya sengaja PENDEK. Uraian panjang pindah ke bagian tersendiri di
       bawah, sehingga tinggi bagian atas tidak lagi bergantung pada seberapa
       panjang tulisan model hari itu. */
    get barisDitunggu() {
      const baris = [];

      const s = this.siagaKebijakan;
      if (s) {
        const j = s.jendela || {};
        // Urgensinya diambil dari RISIKO JENDELA, hitungan kode. Sebelumnya
        // dari `s.siaga`, yaitu tingkat siaga milik langkah LLM agen
        // kebijakan yang sudah dihapus — field itu tidak ada lagi di brief
        // baru, dan membiarkannya berarti baris ini tidak akan pernah lagi
        // tampil mendesak, sekalipun bursanya tutup di pasar yang rapuh.
        const tinggi = (s.risiko_jendela || {}).tingkat === 'tinggi';
        const mundur = this.hitungMundurLive(j.buka_berikutnya_utc);
        // Tidak ada lagi cabang "sudah lewat" di sini: getter siagaKebijakan
        // memulangkan null begitu bursanya buka — untuk siaga yang memang
        // bersandar pada jendela.
        const rawan = !!j.dalam_jendela_rawan;
        baris.push({
          jenis: 'siaga',
          ikon: tinggi ? 'siren' : 'landmark',
          // Label dipendekkan supaya muat sebaris dengan hitung mundur, persis
          // seperti baris agenda. Versi panjang ("SIAGA KEBIJAKAN: SEDANG")
          // membungkus dan mendorong hitung mundur ke baris sendiri, sehingga
          // dua baris yang isinya sejenis tampil dengan susunan berbeda dan
          // terbaca seolah mengukur hal yang berbeda. Tingkat siaganya pindah
          // ke chip di sebelah nama.
          label: 'JENDELA RISIKO',
          tingkat: (s.risiko_jendela || {}).tingkat || '',
          // Hitung mundur dan jangkar hanya ditampilkan kalau jendelanya
          // memang inti persoalannya. Pada siaga yang lahir dari isi
          // kebijakan, "14 jam lagi · bursa AS buka" bukan cuma mubazir —
          // ia mengarahkan pembaca menyangka alarmnya SOAL pembukaan bursa,
          // padahal soal tarif yang baru diumumkan.
          mundur: rawan ? mundur : null,
          jangkar: rawan ? (j.buka_berikutnya_wib || '') : '',
          awalan: 'bursa AS buka',
          // Kalimat jendela hanya ada untuk fase akhir pekan. Tanpa cadangan
          // ini, siaga hari kerja terbit sebagai judul tanpa isi sama sekali.
          isi: this.kalimatJendelaRingkas,
          tautan: '#s-siaga',
          mendesak: tinggi,
          perhatian: true,
        });
      }

      const a = this.agendaSorot;
      if (a) {
        const mundur = this.hitungMundurLive(a.waktu_utc);
        baris.push({
          jenis: 'agenda',
          ikon: 'calendar-clock',
          label: 'AGENDA BESAR',
          mundur,
          jangkar: `${a.hari} · ${a.waktu_wib}`,
          awalan: '',
          isi: a.nama,
          jalur: a.jalur || '',
          tautan: '#s-agenda',
          mendesak: false,
          perhatian: !mundur?.lewat && (mundur?.jam ?? Infinity) < 24,
        });
      }
      return baris;
    },

    /* Kartu Sorotan muncul kalau SALAH SATU isinya ada. Tanpa penjaga ini
       kartu kosong bisa terbit di hari yang sepi — kotak berjudul tanpa isi
       lebih buruk daripada tidak ada kotak sama sekali. */
    get sorotanAda() {
      const adaAi = !!(this.pergerakan24j?.arah
        || (this.bagianAiTampil('narasi') && this.data?.ai?.bagian?.judul));
      return adaAi || this.barisDitunggu.length > 0;
    },

    /* Kartu Sorotan sengaja NETRAL. Sebelum ringkasan AI ikut masuk ke sini,
       latar kartunya diwarnai urgensi tertinggi — dan itu masuk akal selama
       isinya cuma alarm. Begitu vonis pasar tinggal di kartu yang sama,
       mewarnai seluruh kartu berarti headline ikut dicat merah gara-gara
       jendela kebijakan yang tidak ada hubungannya dengan isi headline itu.

       Urgensinya pindah ke garis aksen per baris (lihat `.baris-sorotan`),
       jadi yang mendesak tetap menonjol tanpa menyeret tetangganya. */
    get kelasBarDitunggu() {
      return 'border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60';
    },

    kelasBarisDitunggu(b) {
      if (b.mendesak) return 'text-rose-700 dark:text-rose-300';
      if (b.perhatian) return 'text-amber-700 dark:text-amber-300';
      return 'text-slate-600 dark:text-slate-300';
    },

    /* Nilai HEX/rgba, bukan kelas utility: `border-l-*` terbukti kalah oleh
       `border-*` menurut urutan stylesheet dan gagal diam jadi garis 1px
       kelabu. Sekali kena, cukup. */
    gayaBarisSorotan(b) {
      const warna = b.mendesak ? '#f43f5e' : b.perhatian ? '#f59e0b' : '#cbd5e1';
      const latar = b.mendesak
        ? 'rgba(244,63,94,0.09)'
        : b.perhatian ? 'rgba(245,158,11,0.09)' : 'transparent';
      // Hover menebalkan tint milik barisnya sendiri, bukan memakai satu abu
      // seragam: baris siaga yang sudah beraksen kuning akan terlihat berkedip
      // ke kelabu kalau warnanya diganti jenis saat disentuh. Baris netral —
      // yang memang tak punya tint — barulah memakai abu transparan.
      const hover = b.mendesak
        ? 'rgba(244,63,94,0.18)'
        : b.perhatian ? 'rgba(245,158,11,0.18)' : 'rgba(148,163,184,0.16)';
      return `--aksen-baris: ${warna}; --latar-baris: ${latar}; --latar-hover: ${hover}`;
    },

    /* Kalimat posisi waktu — bagian yang membuat berita yang sama berbahaya
       atau biasa saja. Dirakit dari angka yang dihitung pipeline. */
    get kalimatJendela() {
      const j = this.siagaKebijakan?.jendela;
      if (!j) return '';
      if (j.fase === 'jeda_akhir_pekan') {
        // Angka jam SENGAJA tidak ikut di kalimat ini. `jam_sampai_buka`
        // dibekukan saat brief dibuat, jadi menuliskannya akan mengembalikan
        // persis kebohongan yang baru diperbaiki — "masih 7 jam lagi" pada
        // pukul 6 sore. Sisa waktunya dipegang hitung mundur hidup tepat di
        // bawah kalimat ini; di sini cukup jangkar absolutnya.
        const awal = j.jeda_mulai ? ` sejak ${j.jeda_mulai}` : '';
        const buka = j.buka_berikutnya_wib ? ` dan dibuka kembali ${j.buka_berikutnya_wib}` : '';
        return `Bursa AS & ETF tutup${awal}${buka}. `
             + 'Kalau ada kejutan kebijakan sekarang, hanya harga kripto yang bereaksi — '
             + 'tidak ada transaksi ETF atau institusi AS yang bisa meredamnya.';
      }
      if (j.fase === 'jelang_tutup_pekan') {
        return 'Menjelang penutupan Jumat — berita yang mendarat sekarang tidak sempat '
             + 'dicerna pasar AS sebelum jeda akhir pekan.';
      }
      return '';
    },

    /* Hitung mundur jendela untuk bagian rincian. Sengaja memakai getter yang
       sama dengan bar atas: satu sumber angka, jadi keduanya tidak mungkin
       menampilkan sisa waktu yang berbeda. */
    get hitungMundurJendela() {
      return this.hitungMundurLive(this.siagaKebijakan?.jendela?.buka_berikutnya_utc);
    },

    /* Versi satu baris untuk bar atas. Angka jamnya sengaja TIDAK ikut di
       sini — hitung mundur hidup di sebelahnya yang memegang angka, dan
       menuliskannya dua kali membuka peluang keduanya berbeda. */
    get kalimatJendelaRingkas() {
      const f = this.siagaKebijakan?.jendela?.fase;
      if (f === 'jeda_akhir_pekan') {
        return 'Bursa AS & ETF tutup — kejutan kebijakan sekarang hanya akan terasa di harga '
             + 'kripto, tanpa peredam dari ETF atau bursa AS.';
      }
      if (f === 'jelang_tutup_pekan') {
        return 'Menjelang penutupan Jumat — berita sekarang tidak sempat dicerna pasar AS.';
      }
      return '';
    },

    get kelasSiaga() {
      if (this.siagaKebijakan?.risiko_jendela?.tingkat === 'tinggi') {
        return {
          kotak: 'border-rose-300 dark:border-rose-700/70 bg-rose-50/70 dark:bg-rose-900/20',
          label: 'text-rose-700 dark:text-rose-300',
          ikon: 'siren',
        };
      }
      return {
        kotak: 'border-amber-300 dark:border-amber-700/70 bg-amber-50/70 dark:bg-amber-900/20',
        label: 'text-amber-700 dark:text-amber-300',
        ikon: 'landmark',
      };
    },

    /* Klasifikasi pergerakan 24 jam — dihitung pipeline, bukan AI. Tetap
       tampil walaupun bagian AI gagal atau ditahan critic. */
    get pergerakan24j() {
      return this.data?.technical?.pergerakan_24j || null;
    },

    get labelArah24j() {
      const p = this.pergerakan24j;
      if (!p?.arah) return '';
      if (p.arah === 'datar') return 'Praktis datar dalam 24 jam';
      const arah = p.arah === 'naik' ? 'Naik' : 'Turun';
      // Nilai mutlak + kata arah, bukan persen(): persen() menambahkan tanda
      // "+" untuk angka positif, jadi hari turun akan terbaca "Turun +2,80%".
      const angka = p.perubahan_pct === null || p.perubahan_pct === undefined
        ? '' : ` ${this.angka(Math.abs(p.perubahan_pct), 2)}%`;
      return `${arah}${angka} dalam 24 jam`;
    },

    get labelBesaran24j() {
      return {
        tipis: 'tipis', wajar: 'wajar', besar: 'besar', ekstrem: 'sangat besar',
      }[this.pergerakan24j?.besaran] || '—';
    },

    /* Warna mengikuti ARAH, bukan jenisnya — pembaca membaca warna sebagai
       naik/turun, dan memakainya untuk hal lain justru menyesatkan. */
    get kelasPergerakan() {
      const arah = this.pergerakan24j?.arah;
      if (arah === 'naik') {
        return {
          kotak: 'bg-emerald-50/70 dark:bg-emerald-900/20 border-emerald-300 dark:border-emerald-800/60',
          teks: 'text-emerald-700 dark:text-emerald-300',
          ikon: 'trending-up',
        };
      }
      if (arah === 'turun') {
        return {
          kotak: 'bg-rose-50/70 dark:bg-rose-900/20 border-rose-300 dark:border-rose-800/60',
          teks: 'text-rose-700 dark:text-rose-300',
          ikon: 'trending-down',
        };
      }
      return {
        kotak: 'bg-slate-100/70 dark:bg-slate-800/40 border-slate-300 dark:border-slate-600',
        teks: 'text-slate-700 dark:text-slate-200',
        ikon: 'move-horizontal',
      };
    },

    /* Bagian analis sesuai struktur laporan harian: temuan, penyebab, data
       pendukung, peta level, sisi lawan, katalis, kesimpulan. */
    get bagianAnalis() {
      const b = this.data?.ai?.bagian || {};
      const urutan = [
        ['posisi_harga', 'Posisi harga', 'teks'],
        ['karakter_pergerakan', 'Karakter pergerakan', 'teks'],
        ['penyebab', 'Penyebab', 'teks'],
        ['data_pendukung', 'Data pendukung', 'daftar'],
        ['peta_level', 'Peta level', 'teks'],
        ['yang_diwaspadai', 'Yang perlu diwaspadai', 'teks'],
        // `katalis_berikutnya` dibuang: daftarnya identik dengan bagian
        // Agenda 30 Hari di bawah — diperiksa terhadap sembilan arsip, tidak
        // satu pun butirnya membawa peristiwa yang tidak ada di sana. Bedanya,
        // Agenda punya hitung mundur hidup, bobot dampak, dan jalur
        // transmisinya; di sini cuma teks statis hasil terjemahan model.
        ['kesimpulan', 'Kesimpulan', 'teks'],
      ];
      return urutan
        .filter(([k, , tipe]) => (tipe === 'daftar' ? (b[k] || []).length : !!b[k]))
        .map(([kunci, label, tipe]) => ({ kunci, label, tipe, nilai: b[kunci] }));
    },

    get adaBagianTerstruktur() {
      return this.bagianAnalis.length > 0;
    },

    get paragrafNarasi() {
      const teks = this.data?.ai?.narrative || '';
      return teks.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
    },

    get kategoriTersedia() {
      const set = new Set();
      (this.data?.news || []).forEach((n) => { if (n.kategori) set.add(n.kategori); });
      return [...set].sort();
    },

    get beritaTersaring() {
      const hasil = (this.data?.news || []).filter((n) => {
        if (this.filterKategori && n.kategori !== this.filterKategori) return false;
        if (this.filterSentimen && n.sentimen !== this.filterSentimen) return false;
        return true;
      });
      // Halaman aktif bisa melewati ujung daftar setelah filter dipersempit.
      const maks = Math.max(1, Math.ceil(hasil.length / this.perHalaman));
      if (this.halamanBerita > maks) this.halamanBerita = 1;
      return hasil;
    },

    /* Critic bisa menahan sebagian saja. Bagian yang tidak ditandai tetap
       ditampilkan — menyembunyikan semuanya membuang analisa yang lolos. */
    bagianAiTampil(nama) {
      const ai = this.data?.ai;
      if (!ai) return false;
      const ditahan = ai.bagian_ditahan || [];
      if (ditahan.includes(nama)) return false;
      // Tanpa daftar eksplisit, critic gagal berarti semuanya ditahan.
      if (!ditahan.length && ai.critic && ai.critic.passed === false) return false;
      return true;
    },

    /* True kalau ADA sesuatu yang bisa ditampilkan di bagian analisa AI —
       dipakai untuk fallback "tidak tersedia" yang independen dari alasan
       penahanannya. Pembaca tidak perlu tahu ITU KENAPA kosong, cukup tahu
       BAHWA kosong. */
    get adaKontenAiTampil() {
      const ai = this.data?.ai;
      if (!ai) return false;
      return !!(
        (this.bagianAiTampil('narasi') && (this.adaBagianTerstruktur || ai.narrative)) ||
        (this.bagianAiTampil('teknikal') && ai.teknikal) ||
        (this.bagianAiTampil('whale') && ai.whale) ||
        (this.bagianAiTampil('outlook') && ai.outlook)
      );
    },

    /* Kalimat yang menyerempet anjuran tindakan tidak lagi menahan analisa —
       cuma diberi keterangan. Yang tetap ditahan hanya kesalahan fakta. */
    get tandaEditorial() {
      return this.data?.ai?.tanda_editorial || [];
    },

    get adaDataInstitusional() {
      const d = this.data;
      if (!d) return false;
      return [d.options, d.onchain, d.flows].some((o) => o && Object.keys(o).length);
    },

    /* Kelas grid yang MENGIKUTI jumlah kartu yang benar-benar dirender.
       Sebelumnya kelasnya dipatok (mis. `lg:grid-cols-2` untuk whale), jadi
       ketika kartu keduanya tidak ada — sinyal palsu kosong, data on-chain
       gagal diambil — separuh baris tampil melompong. Kolom hanya dibuat
       sebanyak kartu yang ada. */
    _kelasGrid(jumlah, maks) {
      const kolom = Math.max(1, Math.min(jumlah, maks));
      if (kolom <= 1) return 'grid grid-cols-1 gap-4';
      if (kolom === 2) return 'grid grid-cols-1 lg:grid-cols-2 gap-4';
      return 'grid grid-cols-1 lg:grid-cols-3 gap-4';
    },

    get kelasGridInstitusional() {
      const d = this.data || {};
      const jumlah = [d.options, d.onchain, d.flows]
        .filter((o) => o && Object.keys(o).length).length;
      return this._kelasGrid(jumlah, 3);
    },

    get kelasGridWhale() {
      const jumlah = (this.adaDataWhale ? 1 : 0)
        + ((this.data?.technical?.sinyal_palsu || []).length ? 1 : 0);
      return this._kelasGrid(jumlah, 2);
    },

    get barisOpsi() {
      const o = this.data?.options || {};
      const b = [];
      const ada = (v) => v !== null && v !== undefined;
      if (ada(o.dvol)) {
        const d = ada(o.dvol_perubahan_7h_pp)
          ? ` (${o.dvol_perubahan_7h_pp > 0 ? '+' : ''}${this.angka(o.dvol_perubahan_7h_pp, 1)} pp/7h)` : '';
        // Rentang 7 hari jadi acuan: 35 itu tinggi atau rendah TANPA konteks
        // ini tidak bisa dijawab pembaca.
        const rentang = (ada(o.dvol_min_7h) && ada(o.dvol_maks_7h))
          ? ` · rentang 7h ${this.angka(o.dvol_min_7h, 1)}–${this.angka(o.dvol_maks_7h, 1)}` : '';
        b.push({ label: 'DVOL', nilai: this.angka(o.dvol, 1) + d + rentang,
                 jelas: 'Indeks volatilitas implied — "VIX"-nya Bitcoin' });
      }
      if (ada(o.realized_vol_30hari_pct)) {
        const rasio = ada(o.iv_rv_ratio)
          ? ` (IV/RV ${this.angka(o.iv_rv_ratio, 2)}× — ${
              o.iv_rv_ratio > 1.15 ? 'opsi relatif mahal' : o.iv_rv_ratio < 0.85 ? 'opsi relatif murah' : 'wajar'
            })` : '';
        b.push({ label: 'Volatilitas realized (30 hari)', nilai: this.angka(o.realized_vol_30hari_pct, 1) + '%' + rasio,
                 jelas: 'Volatilitas yang SUNGGUHAN terjadi, dari candle harian — dibandingkan dengan DVOL (implied) untuk menilai opsi mahal/murah' });
      }
      if (ada(o.put_call_ratio_oi)) {
        b.push({ label: 'Put/Call (OI)', nilai: this.angka(o.put_call_ratio_oi, 2),
                 jelas: 'Rasio open interest opsi jual terhadap opsi beli' });
      }
      if (ada(o.skew_put_call)) {
        b.push({ label: 'Skew put−call', nilai: (o.skew_put_call > 0 ? '+' : '') + this.angka(o.skew_put_call, 1),
                 jelas: 'Selisih volatilitas implied put dan call di sekitar ATM' });
      }
      if (ada(o.iv_atm_put) && ada(o.iv_atm_call)) {
        b.push({ label: 'IV ATM (put/call)', nilai: `${this.angka(o.iv_atm_put, 1)} / ${this.angka(o.iv_atm_call, 1)}`,
                 jelas: 'Volatilitas implied di sekitar harga saat ini' });
      }
      if (ada(o.max_pain_expiry_terdekat)) {
        b.push({ label: 'Max pain', nilai: this.uang(o.max_pain_expiry_terdekat),
                 jelas: 'Strike yang paling merugikan pemegang opsi saat expiry terdekat' });
      }
      if (ada(o.oi_put_btc) && ada(o.oi_call_btc)) {
        b.push({ label: 'OI put/call (BTC)',
                 nilai: `${this.angka(o.oi_put_btc, 0)} / ${this.angka(o.oi_call_btc, 0)}`,
                 jelas: 'Total open interest opsi dalam BTC' });
      }
      if (ada(o.expiry_oi_terbesar) && ada(o.oi_pada_expiry_terbesar_btc)) {
        b.push({ label: 'Expiry OI terbesar', nilai: `${o.expiry_oi_terbesar} (${this.angka(o.oi_pada_expiry_terbesar_btc, 0)} BTC)`,
                 jelas: 'Tanggal jatuh tempo dengan open interest terbanyak — biasanya menarik harga mendekati max pain-nya menjelang expiry (efek pinning)' });
      }
      return b;
    },

    /* Arsip yang tersimpan sebelum penggantian nama masih memakai kunci
       `_perubahan_30h_pct`. Keduanya dibaca supaya membuka arsip lama tidak
       menampilkan metrik tanpa perubahan sama sekali. Nama barunya memakai
       "30hari" karena "30h" terbaca sebagai "30 hours" — lihat catatan di
       src/collectors/onchain.py. */
    perubahan30Hari(o, kunci) {
      const baru = o[`${kunci}_perubahan_30hari_pct`];
      if (baru !== null && baru !== undefined) return baru;
      const lama = o[`${kunci}_perubahan_30h_pct`];
      return lama === undefined ? null : lama;
    },

    get barisOnchain() {
      const o = this.data?.onchain || {};
      const b = [];
      const ada = (v) => v !== null && v !== undefined;
      if (ada(o.mvrv)) {
        b.push({ label: 'MVRV', nilai: this.angka(o.mvrv, 2) + (o.mvrv_zona ? ` · ${o.mvrv_zona.replace(/_/g, ' ')}` : ''),
                 perubahan: this.perubahan30Hari(o, 'mvrv'),
                 jelas: 'Kapitalisasi pasar dibagi realized cap — ukuran keuntungan belum terealisasi' });
      }
      if (ada(o.nvt)) {
        b.push({ label: 'NVT', nilai: this.angka(o.nvt, 1), perubahan: this.perubahan30Hari(o, 'nvt'),
                 jelas: 'Kapitalisasi dibagi nilai transaksi — analog rasio P/E' });
      }
      if (ada(o.alamat_aktif)) {
        b.push({ label: 'Alamat aktif', nilai: this.angka(o.alamat_aktif, 0),
                 perubahan: this.perubahan30Hari(o, 'alamat_aktif'),
                 jelas: 'Alamat aktif harian — proksi permintaan nyata' });
      }
      if (ada(o.realized_cap_usd)) {
        b.push({ label: 'Realized cap', nilai: this.ringkasUang(o.realized_cap_usd),
                 perubahan: this.perubahan30Hari(o, 'realized_cap_usd'),
                 jelas: 'Nilai seluruh koin dihargai saat terakhir berpindah' });
      }
      if (ada(o.pasokan_diam_1thn_pct)) {
        b.push({ label: 'Pasokan diam >1thn', nilai: this.angka(o.pasokan_diam_1thn_pct, 1) + '%',
                 perubahan: null,
                 jelas: 'Porsi pasokan yang tidak bergerak setahun terakhir' });
      }
      return b;
    },

    get barisAliran() {
      const f = this.data?.flows || {};
      const b = [];
      const ada = (v) => v !== null && v !== undefined;
      if (ada(f.premium_coinbase_pct)) {
        b.push({ label: 'Premium Coinbase',
                 nilai: (f.premium_coinbase_pct > 0 ? '+' : '') + this.angka(f.premium_coinbase_pct, 3) + '%',
                 warna: this.warnaAngka(f.premium_coinbase_pct),
                 jelas: 'Selisih harga Coinbase terhadap pasar global — proksi permintaan AS' });
      }
      if (ada(f.harga_coinbase)) {
        b.push({ label: 'Harga Coinbase', nilai: this.uang(f.harga_coinbase), jelas: 'Harga spot BTC-USD di Coinbase' });
      }
      if (ada(f.stablecoin_cap_usd)) {
        b.push({ label: 'Kapitalisasi stablecoin', nilai: this.ringkasUang(f.stablecoin_cap_usd),
                 jelas: 'Total USDT + USDC — likuiditas yang siap masuk pasar' });
      }
      const LABEL_STABLECOIN = { tether: 'USDT', 'usd-coin': 'USDC' };
      if (f.stablecoin_rincian && Object.keys(f.stablecoin_rincian).length) {
        const bagian = Object.entries(f.stablecoin_rincian)
          .map(([id, cap]) => `${LABEL_STABLECOIN[id] || id} ${this.ringkasUang(cap)}`)
          .join(' · ');
        b.push({ label: 'Rincian stablecoin', nilai: bagian,
                 jelas: 'Kapitalisasi tiap stablecoin utama' });
      }
      if (ada(f.stablecoin_perubahan_24j_usd)) {
        b.push({ label: 'Perubahan stablecoin 24j',
                 nilai: this.ringkasUang(f.stablecoin_perubahan_24j_usd, true),
                 warna: this.warnaAngka(f.stablecoin_perubahan_24j_usd),
                 jelas: 'Pertambahan atau pengurangan pasokan stablecoin sehari terakhir' });
      }
      return b;
    },

    get adaDataWhale() {
      // Saat Binance terblokir hanya sisi ritel yang pulih lewat Bybit —
      // kartunya tetap berguna, jadi cukup salah satu sisi ada.
      const w = this.data?.whale;
      if (!w) return false;
      const ada = (v) => v !== null && v !== undefined;
      return ada(w.whale_long_pct) || ada(w.ritel_long_pct);
    },

    get barisPosisi() {
      const w = this.data?.whale || {};
      const baris = [];
      if (w.whale_long_pct !== null && w.whale_long_pct !== undefined) {
        const tren = w.whale_tren_long_pp
          ? ` · ${w.whale_tren_long_pp > 0 ? '+' : ''}${this.angka(w.whale_tren_long_pp, 1)} pp selama ${w.jam_dipantau || 24}j`
          : '';
        baris.push({ label: 'Top trader (pemain besar)', long: w.whale_long_pct, short: w.whale_short_pct, tren });
      }
      if (w.ritel_long_pct !== null && w.ritel_long_pct !== undefined) {
        const via = w.sumber_ritel === 'bybit' ? ' · via Bybit' : '';
        const tren = w.ritel_tren_long_pp
          ? ` · ${w.ritel_tren_long_pp > 0 ? '+' : ''}${this.angka(w.ritel_tren_long_pp, 1)} pp selama ${w.jam_dipantau || 24}j`
          : '';
        baris.push({ label: 'Seluruh akun (ritel)' + via, long: w.ritel_long_pct, short: w.ritel_short_pct, tren });
      }
      return baris;
    },

    /* Berita yang mendasari analisa geopolitik, dipilih KODE dari kategori
       yang relevan — bukan atribusi yang dikarang AI. Sumber tier 1
       (regulator, kantor berita besar) didahulukan supaya yang paling bisa
       dipertanggungjawabkan muncul lebih dulu. */
    get sumberGeopolitik() {
      const relevan = ['regulasi', 'geopolitik', 'makro'];
      return (this.data?.news || [])
        .filter((n) => relevan.includes(n.kategori) && n.url)
        .sort((a, b) => {
          const tier = (a.kredibilitas_sumber || 3) - (b.kredibilitas_sumber || 3);
          if (tier !== 0) return tier;
          return (b.relevansi_btc || 0) - (a.relevansi_btc || 0);
        })
        .slice(0, 4);
    },

    get skenarioOutlook() {
      const o = this.data?.ai?.outlook;
      if (!o) return [];
      return [
        { nama: 'Skenario menguat', data: o.skenario_naik || { pemicu: [] }, panah: '↑', warna: 'text-emerald-600 dark:text-emerald-400' },
        { nama: 'Skenario melemah', data: o.skenario_turun || { pemicu: [] }, panah: '↓', warna: 'text-rose-600 dark:text-rose-400' },
      ].filter((s) => s.data.pemicu?.length || s.data.kondisi);
    },

    /* Nav lompat ponsel. Bagian yang datanya kosong tidak ikut ditampilkan
       supaya tidak ada tautan yang menuju ke mana-mana. */
    get navLompat() {
      const d = this.data;
      if (!d) return [];
      const item = [
        { id: 's-harga', label: 'Harga', ada: true },
        { id: 's-teknikal', label: 'Teknikal', ada: !!d.technical?.['1d'] },
        { id: 's-pasar', label: 'Pasar', ada: true },
        { id: 's-institusional', label: 'Opsi & Valuasi', ada: this.adaDataInstitusional },
        { id: 's-whale', label: 'Whale', ada: this.adaDataWhale || !!d.technical?.sinyal_palsu?.length },
        { id: 's-ai', label: 'Ulasan', ada: true },
        { id: 's-agenda', label: 'Agenda', ada: true },
        { id: 's-berita', label: 'Berita', ada: !!d.news?.length || !!d.statements?.length },
      ];
      return item.filter((i) => i.ada);
    },

    /* Berita dipaginasi 3 baris per halaman, bukan digulung habis: daftar
       panjang membuat bagian di bawahnya sulit dijangkau. */
    get totalHalamanBerita() {
      return Math.max(1, Math.ceil(this.beritaTersaring.length / this.perHalaman));
    },

    get beritaTampil() {
      const mulai = (this.halamanBerita - 1) * this.perHalaman;
      return this.beritaTersaring.slice(mulai, mulai + this.perHalaman);
    },

    /* Ganti halaman pada tabel berpaginasi mengubah tinggi daftar di
       atasnya (halaman terakhir bisa lebih pendek dari halaman penuh).
       Browser lalu menggeser posisi scroll begitu saja supaya tetap valid
       — di ponsel ini terasa seperti "berpindah fokus" karena jari sudah
       terlanjur diam di tombol yang baru ditekan, tapi tombolnya sudah
       bergeser dari bawah jari. Diatasi dengan mengunci posisi tombol itu
       sendiri di layar: ukur jaraknya ke atas viewport sebelum & sesudah
       render, lalu kompensasi selisihnya lewat scrollBy. Bukan animasi,
       murni koreksi supaya tombol yang baru ditekan tidak pernah pindah. */
    _pindahHalamanTerjaga(evt, aksi) {
      const tombol = evt?.currentTarget || null;
      const sebelum = tombol ? tombol.getBoundingClientRect().top : null;
      aksi();
      if (this.$nextTick) {
        this.$nextTick(() => {
          // Ikon Lucide diganti (elemen <i> -> <svg>) SEBELUM posisi diukur:
          // penggantian itu sendiri bisa mengubah tinggi baris, jadi kalau
          // diukur duluan kompensasinya jadi ketinggalan satu langkah.
          this.gambarIkon();
          if (tombol && sebelum !== null) {
            const sesudah = tombol.getBoundingClientRect().top;
            // `behavior: 'instant'` wajib eksplisit — halaman ini memakai
            // scroll-behavior:smooth (untuk nav lompat), dan tanpa ini
            // koreksinya jadi teranimasi pelan alih-alih langsung pas.
            if (sesudah !== sebelum) window.scrollBy({ top: sesudah - sebelum, left: 0, behavior: 'instant' });
          }
        });
      }
    },

    gantiHalamanBerita(arah, evt) {
      const tujuan = this.halamanBerita + arah;
      if (tujuan < 1 || tujuan > this.totalHalamanBerita) return;
      this._pindahHalamanTerjaga(evt, () => { this.halamanBerita = tujuan; });
    },

    get pernyataanTersaring() {
      const hasil = (this.data?.statements || []).filter(
        (s) => s.tokoh && !['tidak disebutkan', 'tidak diketahui'].includes(String(s.tokoh).toLowerCase())
      );
      const maks = Math.max(1, Math.ceil(hasil.length / this.perHalaman));
      if (this.halamanPernyataan > maks) this.halamanPernyataan = 1;
      return hasil;
    },

    get totalHalamanPernyataan() {
      return Math.max(1, Math.ceil(this.pernyataanTersaring.length / this.perHalaman));
    },

    get pernyataanTampil() {
      const mulai = (this.halamanPernyataan - 1) * this.perHalaman;
      return this.pernyataanTersaring.slice(mulai, mulai + this.perHalaman);
    },

    gantiHalamanPernyataan(arah, evt) {
      const tujuan = this.halamanPernyataan + arah;
      if (tujuan < 1 || tujuan > this.totalHalamanPernyataan) return;
      this._pindahHalamanTerjaga(evt, () => { this.halamanPernyataan = tujuan; });
    },

    /* Agenda dipaginasi juga: dengan horizon 30 hari daftarnya bisa panjang,
       dan bagian di bawahnya jadi sulit dijangkau kalau digelar semua.
       Filter dampak defaultnya cuma menampilkan yang besar (relevansi >= 4);
       agenda yang belum sempat dinilai AI-nya ikut disembunyikan di mode ini
       — statusnya sama-sama "belum terkonfirmasi besar". */
    get agendaTersaring() {
      const semua = this.data?.calendar || [];
      const hasil = this.filterDampakAgenda === 'besar'
        ? semua.filter((a) => (a.relevansi_kripto || 0) >= 4)
        : semua;
      const maks = Math.max(1, Math.ceil(hasil.length / this.perHalamanAgenda));
      if (this.halamanAgenda > maks) this.halamanAgenda = 1;
      return hasil;
    },

    get totalHalamanAgenda() {
      return Math.max(1, Math.ceil(this.agendaTersaring.length / this.perHalamanAgenda));
    },

    /* Narasi geopolitik sebagai daftar paragraf, bukan satu blok. */
    get paragrafGeopolitik() {
      return pecahParagraf(this.data?.ai?.outlook?.narasi_geopolitik);
    },

    /* ===== Peta jangkauan harga — visual "Pandangan ke depan" =====

       Pandangan ke depan sebelumnya murni prosa. Padahal angkanya SUDAH ADA
       dan dihitung kode: level kunci, level invalidasi, dan kisaran harian
       normal (ATR). Yang kurang cuma menempatkannya pada satu sumbu supaya
       terlihat sekaligus — di mana harga berdiri, seberapa jauh ke batas
       terdekat, dan seberapa besar satu hari normal dibanding jarak itu.

       Bentuknya METER (posisi satu nilai di dalam rentang berbatas), bukan
       grafik: datanya satu nilai terhadap dua batas. Pewarnaannya EMPHASIS —
       satu aksen untuk harga sekarang, sisanya abu-abu redup. Batas atas dan
       bawah dibedakan oleh POSISI dan LABEL, bukan warna: memakai merah/hijau
       di sini gagal uji keterbacaan buta warna (ΔE deutan 5,8, di bawah
       ambang 6) padahal keduanya bersebelahan di satu batang.

       Return null kalau level kuncinya tidak ada — tidak ada yang bisa
       digambar, dan menebak batas akan menyesatkan. */
    get petaJangkauan() {
      const lv = this.data?.technical?.key_levels;
      const harga = this.data?.price?.last;
      if (!lv || !harga) return null;

      const supportTerdekat = (lv.support || []).filter((v) => v < harga).sort((a, b) => b - a)[0] ?? null;
      const resistenTerdekat = (lv.resistance || []).filter((v) => v > harga).sort((a, b) => a - b)[0] ?? null;
      const bawah = Math.min(...[lv.invalidasi_naik, supportTerdekat, harga].filter((v) => v != null));
      const atas = Math.max(...[lv.invalidasi_turun, resistenTerdekat, harga].filter((v) => v != null));
      const rentang = atas - bawah;
      if (!(rentang > 0)) return null;

      // Sisakan ruang di kedua ujung supaya penanda di batas tidak terpotong.
      const tepi = rentang * 0.06;
      const skalaBawah = bawah - tepi;
      const skalaRentang = rentang + tepi * 2;
      const posisi = (nilai) => (nilai == null ? null
        : Math.max(0, Math.min(100, ((nilai - skalaBawah) / skalaRentang) * 100)));

      // Kisaran harian normal (ATR) digambar sebagai pita di sekitar harga:
      // itu yang memberi SKALA pada jarak ke level terdekat — "1,2% lagi"
      // berarti lain kalau satu hari normal saja bergerak 1,8%.
      const vol = this.data?.technical?.['1d']?.volatilitas || {};
      const atr = vol.atr ?? null;
      const pita = atr ? { kiri: posisi(harga - atr), kanan: posisi(harga + atr) } : null;

      const jarakPct = (nilai) => (nilai == null ? null : ((nilai - harga) / harga) * 100);
      return {
        harga,
        posisiHarga: posisi(harga),
        support: supportTerdekat,
        resisten: resistenTerdekat,
        posisiSupport: posisi(supportTerdekat),
        posisiResisten: posisi(resistenTerdekat),
        invalidasiNaik: lv.invalidasi_naik ?? null,
        invalidasiTurun: lv.invalidasi_turun ?? null,
        posisiInvalidasiNaik: posisi(lv.invalidasi_naik),
        posisiInvalidasiTurun: posisi(lv.invalidasi_turun),
        pita,
        atr,
        atrPct: vol.atr_pct ?? null,
        jarakSupportPct: jarakPct(supportTerdekat),
        jarakResistenPct: jarakPct(resistenTerdekat),
      };
    },

    _hariWIB(tanggal) {
      const w = keWIB(tanggal);
      return Math.floor(Date.UTC(w.getFullYear(), w.getMonth(), w.getDate()) / 86400000);
    },

    /* SATU agenda paling berdampak dalam 3 hari ke depan — isi kartu teratas
       halaman.

       Jendelanya dihitung per HARI KALENDER WIB, bukan 72 jam mentah. Bukan
       detail sepele: brief terbit sekitar 00:30 WIB, dan FOMC Meeting Minutes
       tiga hari kemudian jatuh di `jam_lagi` 72,6 — lewat 36 menit dari batas
       72 jam, padahal siapa pun yang membaca "3 hari ke depan" jelas
       mengharapkannya muncul. Menghitung hari menghapus seluruh kelas
       kesalahan tepi itu.

       Ambangnya `relevansi_kripto >= 4` ("dampak besar"), sama dengan ambang
       filter agenda dan notice <24 jam: kalau "besar" berarti hal berbeda di
       tiap tempat, pembaca tidak bisa mempercayai satu pun.

       Urutan pemilihan: relevansi ke kripto dulu (itu yang ditanyakan —
       dampak TERBESAR, bukan yang terdekat), lalu bobot dampak ekonominya,
       baru waktu sebagai pemutus. Dengan begitu FOMC Minutes tiga hari lagi
       tetap menang atas rilis kelas menengah besok. */
    get agendaSorot() {
      this._detak; // ikut menyegar: penyaringan di bawah memakai waktu sekarang
      const acuan = this.data?.generated_at ? new Date(this.data.generated_at) : null;
      if (!acuan || Number.isNaN(acuan.getTime())) return null;
      const hariAcuan = this._hariWIB(acuan);

      const bobotDampak = { tinggi: 3, menengah: 2, rendah: 1 };
      const layak = (this.data?.calendar || []).filter((a) => {
        if ((a.relevansi_kripto || 0) < 4 || !a.waktu_utc) return false;
        const t = new Date(a.waktu_utc);
        if (Number.isNaN(t.getTime())) return false;
        const selisihHari = this._hariWIB(t) - hariAcuan;
        if (selisihHari < 0 || selisihHari > 3) return false;
        // Kelewatannya diukur terhadap WAKTU SEKARANG, bukan `jam_lagi` yang
        // dibekukan saat brief dibuat. Acara yang lewat beberapa jam setelah
        // brief terbit tidak boleh terus dipajang sebagai "akan datang".
        return t.getTime() > Date.now();
      });
      if (!layak.length) return null;

      return layak.slice().sort((a, b) =>
        (b.relevansi_kripto || 0) - (a.relevansi_kripto || 0)
        || (bobotDampak[b.dampak] || 0) - (bobotDampak[a.dampak] || 0)
        || (a.jam_lagi ?? Infinity) - (b.jam_lagi ?? Infinity)
      )[0];
    },

    /* Hitung mundur dalam bahasa manusia. Jam mentah ("61,8 jam lagi") benar
       tapi tidak terbayang; "2 hari 14 jam lagi" langsung terasa. */
    hitungMundurAgenda(jam) {
      if (jam === null || jam === undefined) return '';
      if (jam < 1) return 'kurang dari 1 jam lagi';
      const bulat = Math.floor(jam);
      if (bulat < 24) return `${bulat} jam lagi`;
      const hari = Math.floor(bulat / 24);
      const sisa = bulat % 24;
      return sisa ? `${hari} hari ${sisa} jam lagi` : `${hari} hari lagi`;
    },

    /* Warna mengikuti KEDEKATAN waktu, bukan besar dampaknya: yang lolos ke
       kartu ini semuanya sudah berdampak besar, jadi yang membedakan
       tinggal seberapa mendesak. */
    get kelasAgendaSorot() {
      const jam = this.agendaSorot?.jam_lagi;
      // `aksen` dipakai strip agenda: urgensinya pindah dari border tebal
      // keliling ke satu garis di kiri, supaya pengingat tiga hari lagi
      // tidak tampil semendesak alarm yang sedang berbunyi.
      //
      // Nilainya HEX, bukan kelas utility. Versi utility-nya
      // (`border-l-amber-500`) terukur kalah oleh `border-slate-200` di
      // stylesheet dan menghasilkan garis slate 1px tanpa error apa pun.
      if (jam === null || jam === undefined) {
        return {
          kotak: 'border-slate-300 dark:border-slate-600 bg-slate-100/70 dark:bg-slate-800/40',
          label: 'text-slate-600 dark:text-slate-300',
          teks: 'text-slate-700 dark:text-slate-200',
          aksen: '#94a3b8',   // slate-400
        };
      }
      if (jam < 24) {
        return {
          kotak: 'border-rose-300 dark:border-rose-700/70 bg-rose-50/70 dark:bg-rose-900/20',
          label: 'text-rose-700 dark:text-rose-300',
          teks: 'text-rose-700 dark:text-rose-300',
          aksen: '#f43f5e',   // rose-500
        };
      }
      return {
        kotak: 'border-amber-300 dark:border-amber-700/70 bg-amber-50/70 dark:bg-amber-900/20',
        label: 'text-amber-700 dark:text-amber-300',
        teks: 'text-amber-700 dark:text-amber-300',
        aksen: '#f59e0b',     // amber-500
      };
    },

    /* Agenda BERDAMPAK BESAR dalam <24 jam — dipakai notice mencolok di
       header, supaya event penting yang sangat dekat tidak terlewat walau
       pembaca tidak sempat scroll ke bagian agenda (atau filternya sedang
       diset ke "Semua agenda" yang membenamkannya di antara yang lain). */
    get agendaMendesak() {
      return (this.data?.calendar || []).filter(
        (a) => a.jam_lagi !== null && a.jam_lagi !== undefined
          && a.jam_lagi < 24 && (a.relevansi_kripto || 0) >= 4
      );
    },

    get agendaTampil() {
      const mulai = (this.halamanAgenda - 1) * this.perHalamanAgenda;
      return this.agendaTersaring.slice(mulai, mulai + this.perHalamanAgenda);
    },

    gantiHalamanAgenda(arah, evt) {
      const tujuan = this.halamanAgenda + arah;
      if (tujuan < 1 || tujuan > this.totalHalamanAgenda) return;
      this._pindahHalamanTerjaga(evt, () => { this.halamanAgenda = tujuan; });
    },

    /* Berpindah tab berita/pernyataan: ikon Lucide pada isi yang baru muncul
       perlu digambar ulang, kalau tidak yang tampil cuma placeholder kosong. */
    gantiTab(nama) {
      this.tabKonten = nama;
      if (this.$nextTick) this.$nextTick(() => this.gambarIkon());
    },

    get adaPernyataan() {
      return this.pernyataanTersaring.length > 0;
    },

    // ---------------------------------------------------------------
    // PEMBACAAN SUARA
    //
    // Yang dibacakan SENGAJA bukan seluruh halaman. Tabel, chip, angka
    // makro, dan navigasi tidak punya arti apa pun kalau diucapkan
    // berurutan — yang punya arti adalah prosanya. Urutannya mengikuti
    // urutan baca di halaman: geopolitik dulu (keputusan terbesar yang
    // menggerakkan harga), lalu narasi, sebab, pandangan ke depan,
    // teknikal, dan whale.
    // ---------------------------------------------------------------

    /* Daftar bagian yang akan dibacakan, sudah dinormalkan untuk suara. */
    get segmenSuara() {
      const ai = this.data?.ai;
      if (!ai) return [];

      const calon = [];
      const harga = this.data?.price?.last;
      if (harga) {
        // Pembuka pendek: pendengar perlu tahu ini brief kapan dan harga
        // berapa sebelum masuk ke analisanya.
        calon.push({
          judul: 'Pembuka',
          teks: `Ringkasan pasar kripto, ${this.data.generated_at_wib || ''}. `
              + `Bitcoin berada di ${untukSuara('$' + formatAngka(harga, 0))}.`,
        });
      }

      const o = ai.outlook || {};
      calon.push({ judul: 'Geopolitik & regulasi', teks: o.narasi_geopolitik });
      calon.push({ judul: 'Narasi utama', teks: ai.narrative });

      const sebab = (ai.penyebab_pergerakan || [])
        .map((p, i) => `${i + 1}. ${p.faktor}. ${p.dasar || ''}`)
        .join(' ');
      calon.push({ judul: 'Penyebab pergerakan', teks: sebab });

      calon.push({
        judul: 'Pandangan ke depan',
        teks: [o.ringkasan, o.skenario_naik?.pemicu, o.skenario_turun?.pemicu]
          .filter(Boolean).join(' '),
      });
      calon.push({ judul: 'Pembacaan teknikal', teks: (ai.teknikal || {}).ringkasan });
      calon.push({ judul: 'Whale & sinyal palsu', teks: (ai.whale || {}).ringkasan });

      return calon
        .map((s) => ({ judul: s.judul, teks: untukSuara(s.teks) }))
        .filter((s) => s.teks.length > 20);
    },

    get bisaDibacakan() {
      return this.suaraDidukung && this.segmenSuara.length > 0;
    },

    /* Suara berbahasa Indonesia yang tersedia di perangkat ini.

       Dicari ulang tiap kali dibutuhkan, bukan disimpan sekali: Chrome
       memulangkan daftar KOSONG pada pemanggilan pertama dan baru mengisinya
       setelah event `voiceschanged`. Menyimpan hasil panggilan pertama
       berarti fitur ini mati di Chrome tanpa sebab yang terlihat. */
    _suaraID() {
      const daftar = window.speechSynthesis?.getVoices?.() || [];
      return daftar.find((v) => v.lang === 'id-ID')
          || daftar.find((v) => (v.lang || '').toLowerCase().startsWith('id'))
          || null;
    },

    _siapkanAntrean() {
      this._suaraAntre = [];
      this.segmenSuara.forEach((seg, iSeg) => {
        for (const potong of pecahUcapan(seg.teks)) {
          this._suaraAntre.push({ teks: potong, segmen: iSeg, judul: seg.judul });
        }
      });
    },

    _ucapkanBerikutnya() {
      const antre = this._suaraAntre || [];
      if (this._suaraIndeks >= antre.length) { this.hentikanBaca(); return; }

      const bagian = antre[this._suaraIndeks];
      this.suaraJudul = bagian.judul;

      const ucap = new SpeechSynthesisUtterance(bagian.teks);
      ucap.lang = 'id-ID';
      const suara = this._suaraID();
      if (suara) ucap.voice = suara;
      ucap.rate = this.suaraKecepatan;

      ucap.onend = () => {
        // Berhenti karena ditekan pengguna tidak boleh memicu potongan
        // berikutnya: `cancel()` juga membangkitkan onend.
        if (this.suaraStatus !== 'main') return;
        this._suaraIndeks += 1;
        this._ucapkanBerikutnya();
      };
      ucap.onerror = (e) => {
        // "interrupted"/"canceled" adalah akibat wajar dari tombol berhenti.
        if (e?.error === 'interrupted' || e?.error === 'canceled') return;
        this.suaraGalat = 'Pembacaan terhenti: ' + (e?.error || 'sebab tidak diketahui');
        this.hentikanBaca();
      };

      window.speechSynthesis.speak(ucap);
    },

    mulaiBaca() {
      if (!this.bisaDibacakan) return;
      this.suaraGalat = '';
      window.speechSynthesis.cancel();
      this._siapkanAntrean();
      this._suaraIndeks = 0;
      this.suaraStatus = 'main';
      this._ucapkanBerikutnya();
    },

    jedaBaca() {
      if (this.suaraStatus !== 'main') return;
      window.speechSynthesis.pause();
      this.suaraStatus = 'jeda';
    },

    lanjutBaca() {
      if (this.suaraStatus !== 'jeda') return;
      this.suaraStatus = 'main';
      window.speechSynthesis.resume();
    },

    hentikanBaca() {
      this.suaraStatus = 'diam';
      this.suaraJudul = '';
      this._suaraIndeks = 0;
      window.speechSynthesis?.cancel?.();
    },

    /* Lompat ke bagian berikutnya tanpa menunggu yang sekarang selesai. */
    lewatiBagian() {
      const antre = this._suaraAntre || [];
      const sekarang = antre[this._suaraIndeks]?.segmen;
      if (sekarang === undefined) return;
      let i = this._suaraIndeks;
      while (i < antre.length && antre[i].segmen === sekarang) i += 1;
      if (i >= antre.length) { this.hentikanBaca(); return; }
      this._suaraIndeks = i;
      this.suaraStatus = 'main';
      window.speechSynthesis.cancel();
      this._ucapkanBerikutnya();
    },

    ubahKecepatan(nilai) {
      this.suaraKecepatan = Number(nilai) || 1;
      // Kecepatan hanya berlaku untuk utterance BARU; yang sedang berbunyi
      // harus dimulai ulang dari potongan ini supaya perubahannya terasa
      // saat itu juga, bukan setelah kalimat yang panjang selesai.
      if (this.suaraStatus === 'main') {
        window.speechSynthesis.cancel();
        this._ucapkanBerikutnya();
      }
      try { localStorage.setItem('suara_kecepatan', String(this.suaraKecepatan)); } catch (e) { /* diblokir */ }
    },

    get progresSuara() {
      const total = (this._suaraAntre || []).length;
      if (!total || this.suaraStatus === 'diam') return 0;
      return Math.round((this._suaraIndeks / total) * 100);
    },

    get daftarMakro() {
      const m = this.data?.macro || {};
      return Object.keys(LABEL_MAKRO).map((kunci) => {
        const nilai = m[kunci];
        let tampil = '—';
        if (nilai !== null && nilai !== undefined) {
          if (kunci === 'ust10y') tampil = `${formatAngka(nilai, 2)}%`;
          else if (kunci === 'wti' || kunci === 'gold') tampil = `$${formatAngka(nilai, 2)}`;
          else if (kunci === 'usdjpy') tampil = `¥${formatAngka(nilai, 2)}`;
          else tampil = formatAngka(nilai, 2);
        }
        return {
          kunci,
          label: LABEL_MAKRO[kunci],
          nilai: tampil,
          perubahan: m[`${kunci}_change_pct`] ?? null,
        };
      });
    },
  };
}

// Ikon digambar ulang setelah Alpine selesai merender pohon awal.
document.addEventListener('alpine:initialized', () => {
  if (window.lucide) window.lucide.createIcons();
});
