/* Ringkasan Pasar Bitcoin — logika halaman.
 * Tanpa build step: Alpine.js untuk state, Chart.js untuk grafik, Lucide untuk ikon.
 */

const BULAN_ID = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
  'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'];
const BULAN_SINGKAT_ID = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun',
  'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];

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
    daftarArsip: [],
    arsipDipilih: '',
    // Berita dan pernyataan tokoh berbagi satu bagian dengan dua tab.
    tabKonten: 'berita',
    halamanBerita: 1,
    halamanPernyataan: 1,
    perHalaman: 3,
    grafik: null,
    _jam: null,
    _detak: 0,          // dinaikkan tiap menit supaya waktu relatif ikut menyegar

    // ---------------------------------------------------------------
    // Siklus hidup
    // ---------------------------------------------------------------
    async mulai() {
      await this.muat();
      await this.muatArsip();
      // Waktu relatif ("3 jam lalu") perlu dihitung ulang berkala.
      this._jam = setInterval(() => { this._detak++; }, 60000);
    },

    async muat(berkas = 'data/latest.json') {
      this.memuat = true;
      this.error = '';
      try {
        const resp = await fetch(`${berkas}?t=${Date.now()}`, { cache: 'no-store' });
        if (!resp.ok) throw new Error(`Berkas data tidak ditemukan (HTTP ${resp.status}).`);
        const isi = await resp.json();
        if (!isi || !isi.price) throw new Error('Struktur data tidak dikenali.');
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

      this.grafik = new Chart(kanvas, {
        type: 'line',
        data: {
          labels: deret.map((d) => this.tanggalSingkat(d.t)),
          datasets: [{
            data: deret.map((d) => d.c),
            borderColor: warna,
            backgroundColor: isian,
            borderWidth: 2,
            fill: true,
            tension: 0.25,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBackgroundColor: warna,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => `$${formatAngka(ctx.parsed.y, 0)}`,
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
      if (abs >= 1e9) return `${tanda}$${formatAngka(abs / 1e9, 2)} miliar`;
      if (abs >= 1e6) return `${tanda}$${formatAngka(abs / 1e6, 1)} jt`;
      if (abs >= 1e3) return `${tanda}$${formatAngka(abs / 1e3, 1)} rb`;
      return `${tanda}$${formatAngka(abs, 0)}`;
    },

    /* Funding rate kerap sangat kecil; dibulatkan biasa bisa tampil "0,0000%"
       yang kelihatan seperti bug padahal angkanya memang benar. */
    tekstFunding(nilai) {
      if (nilai === null || nilai === undefined) return '—';
      const persenNilai = nilai * 100;
      if (Math.abs(persenNilai) < 0.00005) return 'mendekati 0% (netral)';
      return this.persen(persenNilai, 4);
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

    labelZona(zona) {
      return { jenuh_beli: 'jenuh beli', jenuh_jual: 'jenuh jual', netral: 'netral' }[zona] || zona || '';
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

    /* Bagian analis sesuai struktur laporan harian: temuan, penyebab, data
       pendukung, peta level, sisi lawan, katalis, kesimpulan. */
    get bagianAnalis() {
      const b = this.data?.ai?.bagian || {};
      const urutan = [
        ['posisi_harga', 'Posisi harga', 'teks'],
        ['penyebab', 'Penyebab', 'teks'],
        ['data_pendukung', 'Data pendukung', 'daftar'],
        ['peta_level', 'Peta level', 'teks'],
        ['yang_diwaspadai', 'Yang perlu diwaspadai', 'teks'],
        ['katalis_berikutnya', 'Katalis berikutnya', 'daftar'],
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

    get adaBagianDitahan() {
      return (this.data?.ai?.bagian_ditahan || []).length > 0;
    },

    /* True kalau ADA sesuatu yang bisa ditampilkan di bagian analisa AI —
       dipakai untuk fallback "tidak tersedia" yang independen dari alasan
       penahanannya. Pembaca tidak perlu tahu ITU KENAPA kosong, cukup tahu
       BAHWA kosong. */
    get adaKontenAiTampil() {
      const ai = this.data?.ai;
      if (!ai) return false;
      return (
        (this.bagianAiTampil('narasi') && (this.adaBagianTerstruktur || ai.narrative)) ||
        (this.bagianAiTampil('teknikal') && !!ai.teknikal) ||
        (this.bagianAiTampil('whale') && !!ai.whale) ||
        (this.bagianAiTampil('outlook') && !!ai.outlook)
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

    get barisOpsi() {
      const o = this.data?.options || {};
      const b = [];
      const ada = (v) => v !== null && v !== undefined;
      if (ada(o.dvol)) {
        const d = ada(o.dvol_perubahan_7h_pp)
          ? ` (${o.dvol_perubahan_7h_pp > 0 ? '+' : ''}${this.angka(o.dvol_perubahan_7h_pp, 1)} pp/7h)` : '';
        b.push({ label: 'DVOL', nilai: this.angka(o.dvol, 1) + d,
                 jelas: 'Indeks volatilitas implied — "VIX"-nya Bitcoin' });
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
      return b;
    },

    get barisOnchain() {
      const o = this.data?.onchain || {};
      const b = [];
      const ada = (v) => v !== null && v !== undefined;
      if (ada(o.mvrv)) {
        b.push({ label: 'MVRV', nilai: this.angka(o.mvrv, 2) + (o.mvrv_zona ? ` · ${o.mvrv_zona.replace(/_/g, ' ')}` : ''),
                 perubahan: o.mvrv_perubahan_30h_pct,
                 jelas: 'Kapitalisasi pasar dibagi realized cap — ukuran keuntungan belum terealisasi' });
      }
      if (ada(o.nvt)) {
        b.push({ label: 'NVT', nilai: this.angka(o.nvt, 1), perubahan: o.nvt_perubahan_30h_pct,
                 jelas: 'Kapitalisasi dibagi nilai transaksi — analog rasio P/E' });
      }
      if (ada(o.alamat_aktif)) {
        b.push({ label: 'Alamat aktif', nilai: this.angka(o.alamat_aktif, 0),
                 perubahan: o.alamat_aktif_perubahan_30h_pct,
                 jelas: 'Alamat aktif harian — proksi permintaan nyata' });
      }
      if (ada(o.realized_cap_usd)) {
        b.push({ label: 'Realized cap', nilai: this.ringkasUang(o.realized_cap_usd),
                 perubahan: o.realized_cap_usd_perubahan_30h_pct,
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
        baris.push({ label: 'Top trader (pemain besar)', long: w.whale_long_pct, short: w.whale_short_pct });
      }
      if (w.ritel_long_pct !== null && w.ritel_long_pct !== undefined) {
        const via = w.sumber_ritel === 'bybit' ? ' · via Bybit' : '';
        baris.push({ label: 'Seluruh akun (ritel)' + via, long: w.ritel_long_pct, short: w.ritel_short_pct });
      }
      return baris;
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
        { id: 's-ai', label: 'Analisa AI', ada: true },
        { id: 's-berita', label: 'Berita', ada: !!d.news?.length || !!d.statements?.length },
        { id: 's-agenda', label: 'Agenda', ada: true },
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

    gantiHalamanBerita(arah) {
      const tujuan = this.halamanBerita + arah;
      if (tujuan < 1 || tujuan > this.totalHalamanBerita) return;
      this.halamanBerita = tujuan;
      // Ikon Lucide perlu digambar ulang untuk baris yang baru muncul.
      if (this.$nextTick) this.$nextTick(() => this.gambarIkon());
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

    gantiHalamanPernyataan(arah) {
      const tujuan = this.halamanPernyataan + arah;
      if (tujuan < 1 || tujuan > this.totalHalamanPernyataan) return;
      this.halamanPernyataan = tujuan;
      if (this.$nextTick) this.$nextTick(() => this.gambarIkon());
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
