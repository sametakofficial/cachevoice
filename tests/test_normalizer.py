from cachevoice.cache.normalizer import normalize, turkish_lower


def test_turkish_i_lower():
    assert turkish_lower("I") == "ı"
    assert turkish_lower("İ") == "i"
    assert turkish_lower("IŞIK") == "ışık"
    assert turkish_lower("İSTANBUL") == "istanbul"


def test_diacritic_folding():
    assert normalize("çok güzel") == normalize("cok guzel")
    assert normalize("IĞDIR") == normalize("igdir")
    assert normalize("şehir") == normalize("sehir")
    assert normalize("görmüş") == normalize("gormus")


def test_number_replacement():
    assert normalize("3 kaynak buldum") == normalize("5 kaynak buldum")
    assert normalize("10 sonuç var") == normalize("2 sonuç var")


def test_whitespace_punctuation():
    assert normalize("Araştırıyorum!") == normalize("araştırıyorum")
    assert normalize("  çok   güzel  ") == normalize("cok guzel")


def test_cache_hit_scenarios():
    assert normalize("Hemen bakıyorum") == normalize("hemen bakıyorum")
    assert normalize("3 kaynak buldum, analiz ediyorum") == normalize("5 kaynak buldum analiz ediyorum")
    assert normalize("Araştırıyorum...") == normalize("Araştırıyorum")


def test_edge_cases():
    assert normalize("") == ""
    assert normalize("   ") == ""
    assert normalize("123") == "#"
    assert normalize("İİİ") == normalize("iii")
    assert normalize("IIı") == normalize("ııı")
