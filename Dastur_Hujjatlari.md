# 📄 BioTime Cloud — Tizim Hujjatlari va Qo'llanma

Ushbu hujjat **BioTime Cloud** bulutli platformasi va **BioTimeControl** lokal mijoz ilovasining ishlash prinsiplari, arxitekturasi va terminallarni ulash yo'riqnomalarini o'z ichiga oladi.

---

## 1. Tizimning Umumiy Tavsifi
**BioTime Cloud** — xodimlarning ish vaqtini hisobga olish (keldi-ketdi) va terminallarni masofadan boshqarish uchun mo'ljallangan **Multi-tenant SaaS** platformadir. 

Tizim ikki qismdan iborat:
1.  **BioTime Cloud (Veb-sayt):** Super admin va tashkilotlar uchun mo'ljallangan boshqaruv paneli. Bulutda (Render.com platformasida) ishlaydi.
2.  **BioTimeControl (Desktop dastur):** Mijozlarning lokal kompyuteriga o'rnatiladigan, lokal tarmoqdagi Hikvision/ZKTeco terminallari bilan gaplashadigan va ma'lumotlarni bulutga sinxronizatsiya qiladigan yordamchi ilova.

---

## 2. Ma'lumotlar Bazasi Arxitekturasi (Database Schema)
Ma'lumotlar bazasi SQLAlchemy ORM orqali boshqariladi. Asosiy jadvallar:

### A. `Organization` (Tashkilotlar / Mijozlar)
Har bir mijoz uchun alohida tenant hisoblanadi.
*   `id`: Unikal raqam.
*   `name`: Tashkilot nomi.
*   `api_key`: Lokal dastur (BioTimeControl) bulutga ulanishi uchun unikal kalit (`bt_...`).
*   `license_expires_at`: Litsenziya tugash sanasi. Bo'sh bo'lsa — Muddatsiz.
*   `is_active`: Mijoz faollik holati (Super admin bloklashi mumkin).

### B. `Terminal` (Qurilmalar)
Tashkilotlarga tegishli terminallar ma'lumotlari.
*   `serial`: Qurilmaning zavod seriya raqami (ISUP/EHome uchun kalit).
*   `mac`: Qurilmaning MAC manzili.
*   `status`: `'online'` yoki `'offline'`.
*   `last_seen`: Oxirgi marta signal kelgan vaqt (3 daqiqadan oshsa -> Oflayn).

### C. `Employee` (Xodimlar)
Tizimdagi ishchilar.
*   `employee_id`: Tashkilot ichidagi unikal xodim ID raqami.
*   `face_image`: Xodimning yuz tasviri (Base64 formatida, terminalga yuzni yuklash uchun ishlatiladi).

### D. `AttendanceLog` (Keldi-ketdi jurnali)
Xodimlarning terminallarda qayd etgan barcha keldi-ketdi vaqtlari.

### E. `RemoteCommand` (Masofaviy buyruqlar navbati)
Terminallarni boshqarish buyruqlari navbati (Command Queue).
*   `status`: `'pending'` (kutilmoqda), `'sent'` (yuborildi), `'success'` (muvaffaqiyatli), `'failed'` (xatolik).

---

## 3. Terminallarni Ulash Tartibi

Terminallar tizimga 2 xil usulda ulanishi mumkin:

### Usul A: ISUP / EHome (To'g'ridan-to'g'ri bulutga ulanish)
Ushbu usulda terminal **lokal kompyutersiz**, to'g'ridan-to'g'ri internet orqali serverga bog'lanadi.

#### Qurilma (Terminal) sozlamalari:
1.  Terminalning veb-interfeysiga yoki ekran menyusiga kiring.
2.  **Network -> Advanced -> ISUP (yoki EHome)** bo'limiga o'ting.
3.  Quyidagi sozlamalarni kiriting:
    *   **Address Type:** IP Address / Domain Name.
    *   **Server Address:** Bizning bulutli server domen manzili (yoki IP).
    *   **Port:** 80 (yoki Render HTTP porti).
    *   **EHome/ISUP Version:** EHome 5.0 yoki ISUP 5.0.
    *   **Device ID:** Qurilma seriya raqamini yozing.
4.  Sozlamalarni saqlang.

#### Veb-panelda sozlash:
1.  Tashkilot boshqaruv paneliga kiring.
2.  **Qurilmalar -> Yangi terminal qo'shish** tugmasini bosing.
3.  Terminalning **Serial raqami** va **MAC manzili**ni kiriting va saqlang.
4.  Terminal 30 soniya ichida serverga heartbeat yuboradi va status **Onlayn** holatiga o'tadi.

> [!TIP]
> **Shared Terminal (Bir qurilma - bir nechta tashkilot):**
> Agar bitta jismoniy terminal bir nechta tashkilotga qo'shilsa (Serial va MAC bir xil bo'lsa), signal kelganda tizim barcha tashkilotlardagi ushbu terminal statuslarini bir vaqtda **Onlayn** qiladi. Keldi-ketdi logi esa, xodim qaysi tashkilot a'zosi bo'lsa, faqat o'sha tashkilot bazasiga yoziladi.

---

### Usul B: ISAPI (Lokal IP orqali BioTimeControl yordamida)
Terminal to'g'ridan-to'g'ri internetga chiqa olmaydigan yopiq lokal tarmoqda bo'lsa, **BioTimeControl** desktop ilovasi orqali ulanadi.

1.  **BioTimeControl** dasturini lokal kompyuterda ishga tushiring.
2.  Dastur sozlamalariga tashkilotning **API Key** (kaliti)ni kiriting.
3.  Lokal tarmoqdagi terminallarning IP manzilini yozib dasturga bog'lang.
4.  Dastur lokal terminaldan ma'lumotlarni o'qib, bulutga sinxronizatsiya qiladi.

---

## 4. Masofaviy Texnik Qo'llab-quvvatlash va Buyruqlar
Super admin tashkilot profiliga kirib, terminallarga masofaviy buyruq bera oladi. Buyruqlar zanjiri (Command Queue) quyidagicha ishlaydi:

1.  **Navbatga qo'yish:** Admin panelda buyruq berilganda (masalan, `Reboot`), bazada buyruq yaratiladi va holati `pending` bo'ladi.
2.  **Topshirish:** Terminal yoki lokal dastur navbatdagi heartbeat yuborganida, server javobda ushbu buyruqni topshiradi va holatni `sent` qiladi.
3.  **Natija:** Qurilma buyruqni bajarib natijasini yuborganda, holat `success` (muvaffaqiyatli) yoki `failed` (xato) deb yangilanadi va natija (masalan, SQL so'rov natijasi yoki log fayl) ekranda ko'rinadi.

### Mavjud buyruqlar:
*   **Reboot:** Terminalni masofadan o'chirib yoqish.
*   **Log diagnostikasi:** Lokal dasturning logs fayllarini serverga yuklash.
*   **SQLite Baza Nusxasi:** Lokal SQLite bazani (.db formatda) serverga zaxira (backup) sifatida yuklash.
*   **SQL So'rov Bajarish:** Lokal bazada SQL so'rovlarini (SELECT, UPDATE) masofadan bajarish va natijasini ko'rish.
*   **Config yangilash:** Lokal sozlamalarni masofadan tahrirlash.

---

## 5. Litsenziyalarni Boshqarish
Mijoz litsenziyasi tugaganda tizimga kirish avtomatik ravishda cheklanadi.
*   **Uzaytirish tartibi:** Super admin modal oynasidagi **"Litsenziyani uzaytirish / Mijozni tahrirlash"** bo'limidan litsenziya tugash sanasini o'zgartirishi mumkin.
*   **Cheksiz litsenziya:** Agar sana maydoni bo'sh qoldirilsa, litsenziya **Muddatsiz** holatiga o'tadi.
*   **Bloklash:** Litsenziya muddatidan qat'i nazar, super admin istalgan vaqtda mijozni butunlay **Bloklab qo'yishi** (is_active = False) mumkin.
