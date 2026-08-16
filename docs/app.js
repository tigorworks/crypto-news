/* Ringkasan Pasar Bitcoin — logika halaman.
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
    // Berita dan pernyataan tokoh berbagi satu bagian dengan dua tab.
    tabKonten: 'berita',
    halamanBerita: 1,
    halamanPernyataan: 1,
    halamanAgenda: 1,
    perHalaman: 3,
    perHalamanAgenda: 5,
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
        ['Sentimen', 'aggregate.sentiment_score', (v) => this.angka(v, 1)],
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
        ['karakter_pergerakan', 'Naik atau turun, dan kenaikan/penurunan jenis apa', 'teks'],
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
        { id: 's-ai', label: 'Analisa AI', ada: true },
        { id: 's-pasar', label: 'Pasar', ada: true },
        { id: 's-institusional', label: 'Opsi & Valuasi', ada: this.adaDataInstitusional },
        { id: 's-whale', label: 'Whale', ada: this.adaDataWhale || !!d.technical?.sinyal_palsu?.length },
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

    /* Indeks hari kalender WIB dari sebuah waktu — dipakai membandingkan
       "hari ke berapa", bukan "berapa jam lagi". */
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
        // Agenda yang jamnya sudah lewat hari ini bukan lagi pengingat.
        return a.jam_lagi === null || a.jam_lagi === undefined || a.jam_lagi >= 0;
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
      if (jam === null || jam === undefined) {
        return {
          kotak: 'border-slate-300 dark:border-slate-600 bg-slate-100/70 dark:bg-slate-800/40',
          label: 'text-slate-600 dark:text-slate-300',
          teks: 'text-slate-700 dark:text-slate-200',
        };
      }
      if (jam < 24) {
        return {
          kotak: 'border-rose-300 dark:border-rose-700/70 bg-rose-50/70 dark:bg-rose-900/20',
          label: 'text-rose-700 dark:text-rose-300',
          teks: 'text-rose-700 dark:text-rose-300',
        };
      }
      return {
        kotak: 'border-amber-300 dark:border-amber-700/70 bg-amber-50/70 dark:bg-amber-900/20',
        label: 'text-amber-700 dark:text-amber-300',
        teks: 'text-amber-700 dark:text-amber-300',
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
