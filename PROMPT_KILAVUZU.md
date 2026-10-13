# Prompt Kılavuzu — Haber Ajansı

Bu dosya, projede kullanılan **tüm metin şablonlarının ve görsel promptlarının**
ne işe yaradığını açıklar. Sunumda "promptları nereden buldun / neden böyle
yazdın" sorusuna bu dosyayla cevap verebilirsin.

Görsel promptları ve konuşma metni şablonları proje kökündeki tek kaynakta
toplanır:

```
prompts.py
```

Şablon metinlerini değiştirmek için bu dosyayı düzenle. Senaryo oluşturma ve
haber seçme mantığı `script.py` içindedir; tüm iş kuralları yalnızca
`prompts.py` dosyasında değildir.

---

## 1. Görsel Promptusu

### 1A. `STYLE_CORE` — ortak stil

Tüm spiker görsellerine eklenen ortak parça:

```python
STYLE_CORE = (
    "broadcast news anchor portrait, "          # medya türü
    "young adult, "                            # yaş grubu
    "professional dark navy suit, "            # kıyafet
    "modern television newsroom background, "  # mekân
    "three-point studio lighting, "            # ışık
    "shallow depth of field, "                 # derinlik
    "confident expression, looking at camera, "# bakış
    "photorealistic, highly detailed, 8k"     # kalite
)
```

**Neden ayrı tutuluyor?**

Ülkelerin spikerleri aynı stüdyo ve ışık stilini paylaşır; ülkeye özel görünüm
`COUNTRY_APPEARANCE` üzerinden değişir.

Bu satır pratikte şunu demek: *"sen bir ressam musun, evet; ama sana bir stil
kitabı veriyoruz."*

**Sunumda söylenecek cümle:**
> "Görselleri üretirken her ülkeye tek tek prompt yazmadım. Ortak bir stil
> sabitonyu tanımladım, sadece ülke kısmını değiştirdim. Böylece 20 ülkenin
> spikeri tek bir kanal gibi görünüyor."

### 1B. `COUNTRY_APPEARANCE` — ülkeye göre değişen kısım

Sadece milliyet ve saç rengi değişir:

```python
COUNTRY_APPEARANCE = {
    "TR": "Turkish presenter, dark hair",
    "KR": "Korean presenter, black hair",   # Lee Joung
    "DE": "German presenter, blonde hair",
    ...
}
```

Promptta "young attractive" gibi ifadeler kullanılmıyor.

İki sebeple:

1. AI bu tür ifadelerde görünür biçimde abartılı, yapay bir "patern" üretir.
2. 20 ülkenin spikerine aynı dili uygulamak adil olmaz.

Bunun yerine `young adult` + `professional`: genç, profesyonel, cevici.

**Sunumda söylenecek cümle:**
> "Görsel promptlarına etik tasarım kararı olarak 'güzel/genç' gibi ifadeler
> eklemedim. Hem bu ifadeler yapay sonuç veriyordu hem de 20 ülkenin spikerine
> aynı ölçüyü uygulamak uygun olmazdı."

### 1C. `CATEGORY_BACKGROUNDS` — kategori arka planları

Haberin fotoğrafı yoksa ekranda görünen arka plan:

```python
CATEGORY_BACKGROUNDS = {
    "Gündem":  "generic modern city skyline at dusk, news atmosphere",
    "Ekonomi": "stock exchange trading floor, screens with charts",
    "Spor":    "empty stadium seats, floodlights",
    ...
}
```

**Neden savaş/şiddet görseli yok?**

Haberin metni zaten ekranda yazıyor. Arka plan sadece **hissi** vermek için
var. Savaş görseli, izleyicinin zaten okuduğu şeyi tekrar gösterirdi.

Arkada tüm kategorilere `BACKGROUND_STYLE` eklenir:

```python
BACKGROUND_STYLE = (
    ", cinematic photography, slightly blurred depth, "
    "darkened for text overlay, no text, no people, no logos"
)
```

Buradaki `no text` ve `no people` kritik: AI arka plana yanlış yazılar veya
suratlar ekleyebiliyor. Altyazının okunabilir kalması için `darkened for text
overlay` ile kısmen karartıyoruz.

### 1D. `COUNTRY_NAMES` — ekranda görünen adlar

`COUNTRY_NAMES` ülke kodlarını ekranda ve seslendirme metninde kullanılacak
adlara çevirir.

Spiker "Şimdi Almanya'dan bir bakış" derken ve ekranda etiket görünürken
kullanılır.

Yeni ülke kodu eklenirse `COUNTRY_NAMES` sözlüğüne ve veri modelindeki ülke
seçeneklerine de eklenmelidir; aksi hâlde ekranda ham kod görünebilir.

---

## 2. Senaryo Promptusu (Spikerin söylediği metinler)

### Yayın kuralları

Şablonlar, bu projede izlenen altı yayın kuralını karşılayacak şekilde
oluşturuldu:

| # | Kural | Şablon |
|---|-------|--------|
| 1 | Açılışta spiker **kendini tanıtır ve saati verir** | `OPENING_TEMPLATE` |
| 2 | Blok geçişinde **konunun adını söyler** | `CATEGORY_TRANSITION` |
| 3 | Her haberin sonunda **kaynağını söyler** | `STORY_LEAD` + `_source_label()` |
| 4 | Manşet sayısıyla uyumlu geçiş | `build_headline_transition()` |
| 5 | Kapanışta **yine gönderdiğini söyler** | `CLOSING_TEMPLATE` |
| 6 | Spiker adını **her bültende değil, sadece açılışta** söyler | — |

**Sunumda söylenecek cümle:**
> "Metinleri rastgele yazmadım. Altı madde olarak yayın kurallarını çıkardım
> ve her şablonu bu kurallardan birine bağladım."

### `OPENING_TEMPLATE` — imza cümle

```python
OPENING_TEMPLATE = (
    "Ben Gündem Haber'den {spiker}. "
    "Saat {saat} itibarıyla Gündem Haber başlıyor. "
    "Gündemden öne çıkan gelişmelerle sizlerleyiz."
)
```

Kanal adı ayarlardan gelir; bu metin açılış cümlesini tanımlar. Spiker
saati verdikten sonra tek tek haberleri sunmaya başlar.

### `build_headline_transition()` — manşet sayısı

```python
build_headline_transition(2)
# "Gündemin başında, iki önemli gelişme."
```

Sayı seçilen manşet adedinden üretilir. 1–10 arası sayılar Türkçe yazıyla,
daha büyük sayılar rakamla yazılır.

### `CATEGORY_TRANSITION` ve altyazı noktalama

Bu şablonlar doğrudan `prompts.py` içindeki metinlerdir.

`COUNTRY_TRANSITION` çekimlenmiş ülke adını kullanır:

```python
COUNTRY_TRANSITION = "Şimdi {ulke} bir bakış."
```

`script.py`, ülke adını `country_ablative()` ile çekimleyip şablona verir.

### `country_ablative()` — Türkçe ek uyumu

Fonksiyon son ünlüye göre `-dan/-den`, son harfin sert ünsüz olmasına göre
`d/t` seçer. Türkçedeki özel ad istisnaları gerektiğinde ayrıca test edilmelidir.

### `CLOSING_TEMPLATE` — döngüyü kapatmak

```python
CLOSING_TEMPLATE = (
    "Bizi dinlediğiniz için teşekkür ederiz. {saat} itibarıyla gündemin "
    "özeti bu kadar. ... Görüşmek üzere."
)
```

7/24 yayında bülten tekrar tekrar döndüğü için izleyici "nereye gitti?" diye
sormamalı. Kapanış, döngünün bittiğini söyler.

Kapanış metni `script.py` tarafından bülten oluşturulurken eklenir. Seçim
limitleri haber ve kategori kalemlerine uygulanır.

---

## 3. Hikâye Metni

Metin `script.py` içinde başlık, özet ve kaynak etiketi birleştirilerek kurulur.

Kaynak etiketi kayıtlı `Source` adını kullanır.

**Neden kaynak?** Gerçek ajanslarda haber kaynağına bağlanır. Ekranı okuyan
kişi haberin nereden geldiğini görebilmeli.

**Noktalama düzeltmesi:** Başlık zaten nokta ile bitiyorsa tekrar nokta
konulmuyordu (`"bir şey oldu." + "."` → `"bir şey oldu.."`). `_clean_text()`
bunu düzeltiyor.

---

## 4. Ülke Tespiti

`COUNTRY_KEYWORDS` sözlüğü `sources_data.py` içindedir; haber metninden ülke
tespiti `services.py` içinde yapılır.

**İki hata yaşandı, ikisi de düzeltildi:**

1. **Alt dizi eşleşmesi.** `if code in text` yazınca `"IN"` (Hindistan) kodu,
   Türkçe metindeki `"in"` kelimesinde bulunuyordu — neredeyse her haber
   Hindistan oluyordu. Çözüm: kelime sınırı kontrolü (`\b`).
2. **ASCII adlar.** Sözlükte `Turkiye`, `Cin`, `Misir` yazıyordu (klasik ASCII
   kısayla yazım). Metin ise `Türkiye`, `Çin`, `Mısır` — eşleşme olmuyordu.
   Çözüm: Türkçe karakterli haller.

---

## 5. Şablonu Değiştirmek

```bash
# 1. Dosyayı aç
notepad prompts.py

# 2. Değiştir, kaydet

# 3. Yeni bülten üret (eski bültenler değişmez)
python manage.py make_bulletin

# 4. Önce dosya üretmeden kontrol et
python manage.py make_bulletin --dry-run
```

**Önemli:** Şablon değişikliği **sadece yeni** bültenleri etkiler. Eski
bültenler `PlayoutItem.text` alanında kendi metnini saklar, şablona bakmaz.
Bu, arşivin bozulmaması için bilinçli bir tasarım.

---

## Özet Tablo

| Değişken | Ne işe yarar | Nerede kullanılıyor |
|----------|---------------|---------------------|
| `STYLE_CORE` | Tüm görsellere eklenen ortak stil | `prompts.py` |
| `COUNTRY_APPEARANCE` | Ülkeye özgü görünüm | `prompts.py` |
| `CATEGORY_BACKGROUNDS` | Kategori arka planları | `prompts.py` |
| `BACKGROUND_STYLE` | Arka planlara eklenen ortak kısım | `prompts.py` |
| `COUNTRY_NAMES` | Ekranda görünen ülke adı | `prompts.py`, `script.py` |
| `OPENING_TEMPLATE` | Bülten açılışı | `prompts.py`, `script.py` |
| `build_headline_transition()` | Manşet sayısına uygun geçiş | `prompts.py`, `script.py` |
| `CATEGORY_TRANSITION` | Kategori girişi | `prompts.py`, `script.py` |
| `COUNTRY_TRANSITION` | Ülke geçişi (çekim hâli) | `prompts.py`, `script.py` |
| `CATEGORY_OUTRO` | Kategori çıkışı | `prompts.py`, `script.py` |
| `CLOSING_TEMPLATE` | Bülten kapanışı | `prompts.py`, `script.py` |
| Haber metni ve kaynak etiketi | Okunacak haber içeriği | `script.py` |
| `COUNTRY_KEYWORDS` | Ülke tespiti | `sources_data.py`, `services.py` |