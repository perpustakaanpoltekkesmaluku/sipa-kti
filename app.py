import streamlit as st
import json
import re
import time
import requests
import docx
from PyPDF2 import PdfReader

st.set_page_config(page_title="SIPA-KTI Poltekkes Maluku", layout="wide", page_icon="📚")
st.markdown("""
    <style>
    .stAlert { border-radius: 10px; }
    .stButton>button { width:100%; background-color:#00796b; color:white; border-radius:8px; padding:0.5rem; font-size:16px; }
    </style>
""", unsafe_allow_html=True)


def extract_text(file):
    try:
        if file.type == "application/pdf":
            reader = PdfReader(file)
            return "\n".join(p.extract_text() for p in reader.pages if p.extract_text())
        elif "wordprocessingml" in file.type:
            doc = docx.Document(file)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        st.error(f"Gagal membaca file: {e}")
    return ""


def potong_teks(teks, ukuran=3000):
    paragraf = teks.split("\n")
    chunks, buf = [], ""
    for par in paragraf:
        if not par.strip():
            continue
        if len(buf) + len(par) + 1 <= ukuran:
            buf += par + "\n"
        else:
            if buf.strip():
                chunks.append(buf.strip())
            buf = par + "\n"
    if buf.strip():
        chunks.append(buf.strip())
    return chunks if chunks else [teks[:3000]]


def parse_hasil(raw):
    bersih = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip().strip("`").strip()
    try:
        parsed = json.loads(bersih)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("items", "errors", "hasil", "data", "temuan", "perbaikan"):
                if key in parsed and isinstance(parsed[key], list):
                    return parsed[key]
        return []
    except Exception:
        pass
    match = re.search(r"\[.*\]", bersih, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


def kirim_groq(prompt, api_keys):
    url = "https://api.groq.com/openai/v1/chat/completions"
    errors = []

    for i, api_key in enumerate(api_keys):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                data = r.json()
                if "choices" in data:
                    return data["choices"][0]["message"]["content"], None
            elif r.status_code == 429:
                errors.append(f"key{i+1}: rate limit")
                time.sleep(5)
                continue
            elif r.status_code == 401:
                errors.append(f"key{i+1}: API key tidak valid")
                continue
            else:
                errors.append(f"key{i+1}: HTTP {r.status_code}")
                continue
        except Exception as e:
            errors.append(f"key{i+1}: {str(e)}")
            continue

    return None, "GAGAL: " + " | ".join(errors)


def buat_prompt(mode, bagian, chunk):
    base = (
        f"Kamu adalah auditor akademik yang sangat teliti untuk KTI kesehatan Indonesia.\n"
        f"Bagian: {bagian}\n\n"
        f"Balas HANYA dengan JSON valid:\n"
        f'{{"items":[{{"salah":"teks asli persis","benar":"koreksi benar","ket":"aturan dilanggar"}}]}}\n'
        f'Jika tidak ada kesalahan: {{"items":[]}}\n\n'
    )

    if mode == "Perbaikan Typo / EYD / PUEBI":
        aturan = """PERIKSA SETIAP KATA. Laporkan SEMUA kesalahan:

1. KATA TIDAK BAKU → KBBI:
praktek→praktik, apotik→apotek, nasehat→nasihat, ijin→izin, resiko→risiko,
aktifitas→aktivitas, prosentase→persentase, sistim→sistem, tehnik→teknik,
analisa→analisis, hipotesa→hipotesis, standart→standar, obyek→objek,
subyek→subjek, nampak→tampak, merubah→mengubah, jaman→zaman,
nafas→napas, isteri→istri, kwalitas→kualitas, kwalitatif→kualitatif,
kuisioner→kuesioner, diagnosa→diagnosis, komplek→kompleks,
efektifitas→efektivitas, sekedar→sekadar, karir→karier,
survey→survei, sample→sampel, nomer→nomor, berfikir→berpikir,
fotocopy→fotokopi, menejemen→manajemen

2. AWALAN di-:
di+kata kerja DISAMBUNG: "di lakukan"→"dilakukan","di temukan"→"ditemukan",
"di peroleh"→"diperoleh","di gunakan"→"digunakan","di ketahui"→"diketahui",
"di buat"→"dibuat","di uji"→"diuji","di terapkan"→"diterapkan"
di+tempat DIPISAH: "dirumah sakit"→"di rumah sakit","dipuskesmas"→"di puskesmas"

3. KATA ULANG: "sehari - hari"→"sehari-hari","lain - lain"→"lain-lain",
"masing - masing"→"masing-masing"

4. SPASI SEBELUM TANDA BACA: "kata ,"→"kata,","kata ."→"kata.","kata :"→"kata:"

5. PLEONASME: "adalah merupakan"→pilih satu,"agar supaya"→pilih satu

6. "dimana" sebagai kata tanya → "di mana"

7. Angka 1-9 dalam kalimat → huruf: "1 orang"→"satu orang"

ABAIKAN: sitasi (Nama, 2021), angka statistik, satuan."""

    elif mode == "Audit Sitasi APA 7":
        aturan = """PERIKSA SETIAP SITASI dalam teks.

HITUNG PENULIS dari koma/&/dan, BUKAN spasi:
"(Wally, 2021)"=1 penulis
"(A, B, 2021)"=2 penulis → wajib &
"(A, B, C, 2021)"=3 penulis → wajib et al.

ATURAN APA 7:
1. 1 penulis: (NamaBelakang, Tahun) — hapus inisial
2. 2 penulis dalam kurung: wajib & bukan "dan"
   2 penulis di narasi: wajib "dan" bukan &
3. 3+ penulis: wajib et al. bukan dkk.
4. Wajib koma antara nama dan tahun
5. ibid. dan op.cit. tidak dipakai di APA 7"""

    else:
        aturan = """PERIKSA SETIAP ENTRI daftar pustaka.

ATURAN APA 7:
1. Nama belakang dulu: Santoso, B. — bukan Budi Santoso
2. Tahun dalam kurung + titik: (2021).
3. 2 penulis: Nama1, I., & Nama2, I. — & bukan "dan"
4. Judul artikel: kapital hanya kata pertama dan nama diri
5. DOI: https://doi.org/10.xxx
6. Urutan alfabetis A-Z"""

    return base + aturan + f"\n\nTEKS:\n{chunk}"


def jalankan_analisis(teks, mode, bagian, api_keys):
    chunks = potong_teks(teks, ukuran=3000)
    total = len(chunks)

    if total == 1:
        st.info("Memproses 1 bagian teks...")
    else:
        st.info(f"Dokumen dibagi menjadi **{total} bagian**. Diproses otomatis.")

    progress = st.progress(0, text="Memulai analisis...")
    semua = []

    for i, chunk in enumerate(chunks):
        progress.progress(int((i / total) * 100), text=f"Menganalisis bagian {i+1} dari {total}...")
        prompt = buat_prompt(mode, bagian, chunk)
        raw, err = kirim_groq(prompt, api_keys)

        if err and not raw:
            st.warning(f"Bagian {i+1}: {err}")
        elif raw:
            hasil = parse_hasil(raw)
            if hasil:
                semua.extend(hasil)

        if i < total - 1:
            time.sleep(2)

    progress.progress(100, text="Selesai!")
    time.sleep(0.5)
    progress.empty()

    seen, unik = set(), []
    for r in semua:
        key = r.get("salah", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unik.append(r)
    return unik


# ── SIDEBAR
with st.sidebar:
    st.title("📚 SIPA-KTI")
    st.caption("AI-Powered Library Assistant")
    st.caption("Powered by Groq AI · Poltekkes Kemenkes Maluku")
    st.divider()
    mode_audit = st.selectbox("Pilih Fokus Audit:", [
        "Perbaikan Typo / EYD / PUEBI",
        "Audit Sitasi APA 7",
        "Audit Daftar Pustaka APA 7",
    ])
    pilihan_bab = st.radio("Pilih Bagian yang Diupload:", [
        "Abstrak", "Bab I - Pendahuluan", "Bab II - Tinjauan Pustaka",
        "Bab III - Metodologi", "Bab IV - Hasil & Pembahasan",
        "Bab V - Penutup/Simpulan", "Daftar Pustaka",
    ])
    st.divider()
    st.info("**Cara pakai:**\n\nUpload dokumen per bab atau tempel teks langsung.")


# ── MAIN
st.header(f"📝 SIPA-KTI: {mode_audit}")

col1, col2 = st.columns([1, 1])
with col1:
    st.subheader("Upload Dokumen")
    uploaded_file = st.file_uploader("Upload file PDF atau DOCX", type=["pdf", "docx"])
with col2:
    st.subheader("Atau Tempel Teks")
    input_text = st.text_area("Tempel isi dokumen di sini...", height=200,
                               placeholder="Salin dan tempel teks KTI...")

if uploaded_file:
    extracted = extract_text(uploaded_file)
    if extracted:
        input_text = extracted
        jml = len(potong_teks(extracted))
        st.success(f"**{uploaded_file.name}** berhasil dimuat — {len(extracted):,} karakter, {jml} bagian.")
    else:
        st.warning("File terbaca tapi tidak ada teks yang bisa diekstrak.")

st.divider()

if st.button(f"🔍 Mulai Analisis — {pilihan_bab}", type="primary"):
    teks = input_text if input_text and input_text.strip() else ""
    if not teks:
        st.error("Silakan upload file atau tempel teks terlebih dahulu!")
    else:
        # Ambil semua API key Groq dari Secrets
        api_keys = []
        for k in ["GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"]:
            try:
                val = st.secrets[k]
                if val and val.strip():
                    api_keys.append(val.strip())
            except Exception:
                pass

        if not api_keys:
            st.error("❌ Tidak ada GROQ_API_KEY di Streamlit Secrets!")
            st.info("Tambahkan: GROQ_API_KEY = \"key_kamu\" di Settings → Secrets")
            st.stop()

        st.caption(f"Menggunakan {len(api_keys)} API key Groq")

        results = jalankan_analisis(teks, mode_audit, pilihan_bab, api_keys)
        if results is None:
            pass
        elif len(results) == 0:
            st.success(f"✅ Tidak ditemukan kesalahan pada **{pilihan_bab}** untuk mode **{mode_audit}**.")
            st.info("""⚠️ **Catatan Penting:**
Hasil analisis ini dihasilkan oleh sistem AI dan mungkin tidak 100% sempurna.
Kami menyarankan untuk tetap melakukan pengecekan ulang secara manual.
Jika membutuhkan bantuan lebih lanjut, silakan hubungi pustakawan Poltekkes Kemenkes Maluku.""")
        else:
            st.subheader(f"Hasil Temuan — {pilihan_bab}")
            st.caption(f"Ditemukan **{len(results)}** item yang perlu diperbaiki.")
            bersih = [{k: r[k] for k in ["salah","benar","ket"] if k in r} for r in results if r.get("salah")]
            st.table(bersih)

            # Disclaimer dan info kontak
            st.warning("""⚠️ **Catatan Penting — Harap Dibaca:**

🔍 **Hasil ini perlu diperiksa ulang secara manual.**
Sistem AI dapat melewatkan beberapa kesalahan atau memberikan saran yang kurang tepat,
terutama untuk kalimat yang kompleks atau istilah khusus bidang kesehatan.

📋 **Yang perlu dilakukan setelah ini:**
- Periksa kembali setiap temuan sebelum memperbaiki dokumen
- Pastikan koreksi sesuai konteks kalimat
- Untuk sitasi dan daftar pustaka, verifikasi kembali dengan panduan APA 7

📚 **Butuh bantuan lebih lanjut?**
Kunjungi **Pustakawan Perpustakaan Terpadu Poltekkes Kemenkes Maluku** atau hubungi **Perpustakaan@poltekkes-maluku.ac.id**
untuk konsultasi penulisan KTI, format sitasi APA 7, dan penelusuran referensi ilmiah.""")

            hasil_json = json.dumps(results, ensure_ascii=False, indent=2)
            nama_file = f"audit_{pilihan_bab.replace(' ','_').replace('-','').strip()}.json"
            st.download_button("⬇️ Unduh Hasil Audit (JSON)", data=hasil_json,
                               file_name=nama_file, mime="application/json")

st.divider()
st.caption("© 2026 SIPA-KTI · Perpustakaan Terpadu Poltekkes Kemenkes Maluku · Powered by Groq AI")
