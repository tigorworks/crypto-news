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
    tabTf: '1d',
    filterKategori: '',
    filterSentimen: '',
    daftarArsip: [],
    arsipDipilih: '',
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
      if (abs >= 1e9) return `${tanda}$${formatAngka(abs / 1e9, 2)} M`;   // miliar
      if (abs >= 1e6) return `${tanda}$${formatAngka(abs / 1e6, 1)} jt`;
      if (abs >= 1e3) return `${tanda}$${formatAngka(abs / 1e3, 1)} rb`;
      return `${tanda}$${formatAngka(abs, 0)}`;
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

    get tfAktif() {
      return this.data?.technical?.[this.tabTf] || null;
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
      return (this.data?.news || []).filter((n) => {
        if (this.filterKategori && n.kategori !== this.filterKategori) return false;
        if (this.filterSentimen && n.sentimen !== this.filterSentimen) return false;
        return true;
      });
    },

    get adaDataWhale() {
      const w = this.data?.whale;
      return !!(w && (w.whale_long_pct !== null && w.whale_long_pct !== undefined));
    },

    get barisPosisi() {
      const w = this.data?.whale || {};
      const baris = [];
      if (w.whale_long_pct !== null && w.whale_long_pct !== undefined) {
        baris.push({ label: 'Top trader (pemain besar)', long: w.whale_long_pct, short: w.whale_short_pct });
      }
      if (w.ritel_long_pct !== null && w.ritel_long_pct !== undefined) {
        baris.push({ label: 'Seluruh akun (ritel)', long: w.ritel_long_pct, short: w.ritel_short_pct });
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

    get daftarMakro() {
      const m = this.data?.macro || {};
      return Object.keys(LABEL_MAKRO).map((kunci) => {
        const nilai = m[kunci];
        let tampil = '—';
        if (nilai !== null && nilai !== undefined) {
          if (kunci === 'ust10y') tampil = `${formatAngka(nilai, 2)}%`;
          else if (kunci === 'wti' || kunci === 'gold') tampil = `$${formatAngka(nilai, 2)}`;
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
