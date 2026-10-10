STYLE_CORE = (
    "broadcast news anchor portrait, "     
    "young adult, "                          
    "professional dark navy suit, "          
    "modern television newsroom background, " 
    "three-point studio lighting, "          
    "shallow depth of field, "               
    "confident expression, looking at camera, " 
    "photorealistic, highly detailed, 8k"     
)

COUNTRY_APPEARANCE = {
    "TR": "Turkish presenter, dark hair",
    "KR": "Korean presenter, black hair",       
    "US": "American presenter, light brown hair",
    "GB": "British presenter, auburn hair",
    "DE": "German presenter, blonde hair",
    "FR": "French presenter, dark brown hair",
    "RU": "Russian presenter, blonde hair",
    "UA": "Ukrainian presenter, blonde hair",
    "CN": "Chinese presenter, black hair",
    "JP": "Japanese presenter, black hair",
    "IN": "Indian presenter, black hair",
    "BR": "Brazilian presenter, dark brown hair",
    "IR": "Iranian presenter, black hair",
    "IL": "Israeli presenter, dark brown hair",
    "SA": "Saudi presenter, black hair",
    "IT": "Italian presenter, dark brown hair",
    "GR": "Greek presenter, dark hair",
    "NL": "Dutch presenter, blonde hair",
    "EG": "Egyptian presenter, black hair",
    "QA": "Qatari presenter, black hair",
}

COUNTRY_NAMES = {
    "TR": "Türkiye", "KR": "Güney Kore", "US": "Amerika", "GB": "İngiltere",
    "DE": "Almanya", "FR": "Fransa", "RU": "Rusya", "UA": "Ukrayna",
    "CN": "Çin", "JP": "Japonya", "IN": "Hindistan", "BR": "Brezilya",
    "IR": "İran", "IL": "İsrail", "SA": "Suudi Arabistan", "IT": "İtalya",
    "GR": "Yunanistan", "NL": "Hollanda", "EG": "Mısır", "QA": "Katar",
    "SG": "Singapur",
}

CATEGORY_BACKGROUNDS = {
    "Gundem": "modern news studio, breaking news screens",
    "Dunya": "world map, international city skyline",
    "Ekonomi": "stock exchange trading screens, financial data",
    "Siyaset": "parliament chamber, government building",
    "Spor": "stadium under floodlights, sports field",
    "Teknoloji": "modern technology lab, digital circuitry",
    "Asya": "Asian city skyline, regional architecture",
    "Avrupa": "European city skyline, historic architecture",
    "Orta Dogu": "Middle Eastern city skyline, regional architecture",
    "Amerika": "American city skyline, Capitol building",
    "Afrika": "African city skyline, regional landscape",
    "Ukrayna-Rusya": "Eastern European city skyline, regional map",
    "Egitim": "university campus, classroom",
    "Kultur-Sanat": "art gallery, theater stage",
    "Saglik": "modern hospital, medical equipment",
    "Bilim": "science laboratory, research equipment",
    "Cevre": "green forest, clean energy infrastructure",
    "Yasam": "everyday city life, people-free public space",
}

BACKGROUND_STYLE = (
    ", cinematic photography, slightly blurred depth, "
    "darkened for text overlay, no text, no people, no logos"
)
OPENING_TEMPLATE = (
    "{kanal} kesintisiz canlı yayınındasınız. Ben {spiker}. "
    "Saat {saat} itibarıyla gündemdeki gelişmeleri aktarıyoruz. "
    "Yeni haberler geldikçe yayın akışımız sürüyor."
)
_NUMBER_WORDS = {
    1: "bir",
    2: "iki",
    3: "üç",
    4: "dört",
    5: "beş",
    6: "altı",
    7: "yedi",
    8: "sekiz",
    9: "dokuz",
    10: "on",
}


def build_headline_transition(count: int) -> str:
    if count < 1:
        raise ValueError("Manşet sayısı en az 1 olmalıdır.")
    count_text = _NUMBER_WORDS.get(count, str(count))
    return f"Gündemin başında, {count_text} önemli gelişme."


CATEGORY_TRANSITION = "Şimdi sizlerle {kategori} haberleri."
COUNTRY_TRANSITION = "Şimdi {ulke} bir bakış."
BREAKING_LEAD = (
    "Son dakika. {ulke} kaynaklardan gelen haberlere göre, {baslik}. "
    "Detayları aktarıyorum."
)
STORY_LEAD = "{baslik}. {ozet}"
CATEGORY_OUTRO = "{kategori} haberlerimizin sonuna geldik."
TICKER_TEMPLATE = "SON DAKİKA  •  {baslik}"
CONTINUITY_TEMPLATE = (
    "Gündem akışımız kesintisiz devam ediyor. "
    "{kanal} yeni gelişmeleri aktarmayı sürdürüyor."
)
VOICE_MAIN = "tr-TR-EmelNeural"

VOICE_VARIANTS = {
    "TR": ("tr-TR-EmelNeural", "+0Hz", "+0%"),
    "KR": ("tr-TR-EmelNeural", "+3Hz", "-3%"),
    "US": ("tr-TR-AhmetNeural", "-4Hz", "+2%"),
    "GB": ("tr-TR-AhmetNeural", "-2Hz", "-2%"),
    "DE": ("tr-TR-EmelNeural", "+2Hz", "-4%"),
    "FR": ("tr-TR-EmelNeural", "+4Hz", "-6%"),
    "RU": ("tr-TR-AhmetNeural", "-6Hz", "+4%"),
    "CN": ("tr-TR-EmelNeural", "+1Hz", "-2%"),
    "JP": ("tr-TR-EmelNeural", "+5Hz", "-8%"),
    "IN": ("tr-TR-AhmetNeural", "-3Hz", "+0%"),
    "BR": ("tr-TR-EmelNeural", "+2Hz", "-1%"),
    "IR": ("tr-TR-AhmetNeural", "-5Hz", "+3%"),
    "IL": ("tr-TR-AhmetNeural", "-3Hz", "+1%"),
    "SA": ("tr-TR-AhmetNeural", "-5Hz", "-1%"),
    "IT": ("tr-TR-EmelNeural", "+3Hz", "-2%"),
    "GR": ("tr-TR-AhmetNeural", "-2Hz", "+1%"),
    "NL": ("tr-TR-EmelNeural", "+2Hz", "-3%"),
    "EG": ("tr-TR-AhmetNeural", "-4Hz", "+2%"),
    "QA": ("tr-TR-AhmetNeural", "-4Hz", "-2%"),
}
BREAKING_HOURS = 1
COUNTRY_HOURS = 12


def country_ablative(country_name: str) -> str:
    """Ülke adına Türkçedeki ayrılma hâli ekini ekler."""
    name = country_name.strip()
    if not name:
        raise ValueError("Ülke adı boş olamaz.")

    last_vowel = next(
        (char.casefold() for char in reversed(name) if char.casefold() in "aıoueiöü"),
        None,
    )
    if last_vowel is None:
        raise ValueError(f"Ülke adında ünlü harf yok: {country_name!r}")

    suffix_vowel = "e" if last_vowel in "eiöü" else "a"
    suffix_initial = "t" if name[-1].casefold() in "fstkçşhp" else "d"
    return f"{name}'{suffix_initial}{suffix_vowel}n"

def build_anchor_prompt(country_code: str, gender: str = "female") -> str:
    """Bir ulkenin spiker icin TAM prompt olusturur.

    Neden fonksiyon? 20 ulke x elle yazmak yerine tek kural.
    Yeni ulke eklemek icin COUNTRY_APPEARANCE'a bir satir eklemen yeterli.

    Ornek cikti:
    "broadcast news anchor portrait, young adult, professional dark navy suit,
     modern television newsroom background, three-point studio lighting,
     shallow depth of field, confident expression, looking at camera,
     photorealistic, highly detailed, 8k, Korean presenter, black hair"

    ⚠️ DIKKAT: stil (STYLE_CORE) once, kimlik (COUNTRY_APPEARANCE) sona
       geliyor. AI'da son kelimeler daha guclu etkiye sahiptir; yani
       kimlik bilgisi sonda olmasi onu daha iyi uyguluyor.
    """
    appearance = COUNTRY_APPEARANCE.get(country_code, "presenter, dark hair")
    gender_word = "woman" if gender == "female" else "man"
    return f"{STYLE_CORE}, {appearance}, {gender_word}"


def build_background_prompt(category_name: str) -> str:
    """Kategori arka plan resminin prompt'unu uretir.

    Ornek:
    "stock exchange trading screens, financial data, city at night
     + cinematic photography, slightly blurred depth, darkened for
       text overlay, no text, no people, no logos"
    """
    base = CATEGORY_BACKGROUNDS.get(category_name, CATEGORY_BACKGROUNDS["Gundem"])
    return f"{base}{BACKGROUND_STYLE}"


def build_image_url(prompt: str, width: int, height: int, seed: int) -> str:
    """Pollinations AI adresini kurar.

    ADRES YAPISI:
      https://image.pollinations.ai/prompt/<PROMPT>
        ?width=768
        &height=1152
        &seed=42
        &nologo=true
        &model=flux

    seed : AYNI sayi = AYNI gorsel. Spikerin yuzunu dondurmak icin sabit.
    model=flux : en gercekcici model
    nologo     : koseye atilan sahte imzayi siler
    """
    from urllib.parse import quote_plus

    return (
        f"https://image.pollinations.ai/prompt/{quote_plus(prompt)}"
        f"?width={width}&height={height}&seed={seed}"
        f"&nologo=true&model=flux"
    )


def build_tts_url_options(voice: str, pitch: str, rate: str) -> dict:
    """Edge TTS ayarlarini sozluk olarak dondurur."""
    return {"voice": voice, "pitch": pitch, "rate": rate}